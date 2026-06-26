"""PolicyApplier — declarative reconcile of cloud policy onto the associate.

The applier is a D-Bus client: it calls the SAME verbs the shell-server REST
routers call, so there is no duplicated write logic.  The runtime daemon
remains the single writer of all state.

Application order (dependency graph):
  1. providers     — agents reference provider_alias
  2. integrations  — (composio key; stateless, no deps)
  3. mcp           — skills and agents can reference MCP servers
  4. skills        — agents can bind skills as capabilities
  5. agents        — upserts only (no deletes yet — P1-4)
  6. consents      — capability grants (HIGH consents require human approval)
  7. egress        — only ADDS to allow-list; never removes owner grants
  8. license       — persisted in association_store
  9. DELETE stale cloud-managed agents (P1-4: AFTER all upserts succeed)

  NOTE — features/views:
  Feature-view entitlements travel in license.views (LicenseSpec) which is
  persisted by __main__.py via store.update_license().  The feature_guard
  middleware reads license["views"] from the association_store directly —
  no D-Bus verb (set_feature_flags) is needed.  FeaturesSpec in the bundle
  is kept for wire-compat but is NOT applied here.

P0-3 — D-Bus verb allow-list:
  Only verbs in _ALLOWED_VERBS may be called by config-sync.  Any attempt to
  call a verb not on the list fails loudly (logged + marked as failure) and the
  call is NEVER made.  This is default-deny for the D-Bus surface.

  Daemon-side note: add_egress_domain, set_feature_flags, update_provider MUST,
  when implemented in the daemon, enforce sovereignty constraints there too.
  The applier's allow-list is a defence-in-depth guard, not the only gate.

Sovereignty invariants (non-negotiable):
  - Egress: ONLY adds domains from the allow-list; never removes owner domains;
    never changes mode; never touches the blocklist.  Each domain is validated
    against the same _DOMAIN_RE regex that the REST API uses (P0-3).
  - HIGH-risk consents (terminal_exec, file_write, …) are NOT auto-approved.
  - No verb that widens the kernel cage or disables the blocklist is permitted.

P0-4 — integration managed_by:
  The composio API key is only pushed when there is no existing local (non-cloud)
  integration already configured.  managed_by tracking for the integrations table
  is a Fase 5 follow-up (the daemon's integration store doesn't have the column).
  In Fase 4: we write the key only if the daemon returns has_key=False (no
  pre-existing key).  Never log the api_key value.

P1-4 — deletes after upserts:
  Cloud-managed agent deletes run ONLY after all upsert phases (providers →
  integrations → mcp → skills → agents upsert → consents → egress → features)
  have completed without failures.  If any upsert fails, deletes are skipped
  and last_applied_version is NOT advanced.

P2 — strict _is_ok for sensitive sections:
  For egress, consents, and integrations, "ok" must be explicitly True.
  An empty dict / None is treated as failure (not silent success).

Follow-ups deferred to Fase 5 / Fase 7:
  - managed_by on providers, integrations, mcp (the daemon stores lack the column).
  - Skill uninstall (declarative delete of cloud-managed skills).
  - mTLS transport (Fase 7 cloud endpoint).
  - NODE_ENROLLMENT HMAC binding (Fase 7, both sides change together).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from hermes.config_sync.policy_document import (
    AgentSpec,
    ConsentSpec,
    EgressSpec,
    IntegrationSpec,
    LicenseSpec,
    McpSpec,
    PolicyPayload,
    ProviderSpec,
    SkillSpec,
)

logger = logging.getLogger("hermes.config_sync.applier")

# ---------------------------------------------------------------------------
# P0-3: D-Bus verb allow-list (default-deny)
# ---------------------------------------------------------------------------

_ALLOWED_VERBS: frozenset[str] = frozenset(
    {
        # Read verbs (safe to call anytime)
        "list_agents",
        "list_providers",
        "list_mcp_servers",
        "list_consents",
        "list_egress_grants",
        # Provider management
        "add_provider",
        "update_provider",
        # Composio integration
        "set_composio_api_key",
        "get_composio_status",
        # MCP server management
        "add_mcp_server",
        # Hub skills
        "install_hub_skill",
        # Agent management
        "create_agent",
        "update_agent",
        "delete_agent",
        # Capability binding
        "bind_capability_to_agent",
        # Consent management (LOW-risk only; HIGH is blocked in _apply_consents)
        "grant_consent",
        # Egress (add domain only — never mode change, never remove)
        "add_egress_domain",
        # NOTE: set_feature_flags is intentionally ABSENT.
        # Feature views travel in license.views (LicenseSpec) and are persisted by
        # __main__.py via store.update_license(). The feature_guard middleware reads
        # them directly from the association_store — no D-Bus verb required.
    }
)

# ---------------------------------------------------------------------------
# P0-3: Egress domain validation (same regex as egress_api.py REST route)
# ---------------------------------------------------------------------------

# Hostname (optionally a leading wildcard, stripped). No scheme, no path, no port.
# Mirrors _DOMAIN_RE in hermes/shell_server/egress_api.py exactly.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
)

# ---------------------------------------------------------------------------
# P2: SSRF check for provider base_url
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
]
_BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata.google.internal", "metadata"})


def _is_safe_base_url(url: str) -> bool:
    """Return False if url is a private/loopback/metadata address (SSRF guard).

    Mirrors the validation logic in http_control_plane_client._validate_cloud_endpoint.
    Only https:// is allowed; loopback, RFC1918, link-local, and metadata blocked.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        if hostname in _BLOCKED_HOSTNAMES:
            return False
        try:
            addr = ipaddress.ip_address(hostname)
            return not any(addr in net for net in _BLOCKED_NETWORKS)
        except ValueError:
            return True  # hostname is a domain name; allowed
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Consents
# ---------------------------------------------------------------------------

_HIGH_RISK_CONSENT_PREFIXES: frozenset[str] = frozenset(
    {
        "terminal_exec",
        "file_write",
        "process_spawn",
        "camera",
        "microphone",
        "credential_store",
    }
)

_CLOUD_MANAGED = "cloud"

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ApplyResult:
    """Outcome of one policy application pass.

    `ok` is True only if zero entities failed.  The sync loop advances
    `last_applied_version` only when `ok` is True (retry-on-failure).
    """

    applied: int = 0
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.failed) == 0


# ---------------------------------------------------------------------------
# Applier
# ---------------------------------------------------------------------------


class PolicyApplier:
    """Applies a PolicyPayload to the local associate via the D-Bus proxy.

    The proxy is injected so tests can pass a FakeDbusProxy without D-Bus.
    """

    def __init__(self, proxy: Any) -> None:
        self._proxy = proxy

    async def apply(
        self,
        payload: PolicyPayload,
        *,
        current_agents: list[dict] | None = None,
    ) -> ApplyResult:
        """Reconcile all sections in dependency order (P1-4: deletes at end)."""
        result = ApplyResult()

        # Phase 1: upsert everything
        await self._apply_providers(payload.providers, result)
        await self._apply_integrations(payload.integrations, result)
        await self._apply_mcp(payload.mcp, result)
        await self._apply_skills(payload.skills, result)

        live_agents = current_agents
        if live_agents is None:
            live_agents = await self._fetch_agents()
        cloud_managed = _cloud_managed_index(live_agents)
        await self._upsert_agents(payload.agents, cloud_managed, result)

        await self._apply_consents(payload.consents, result)
        await self._apply_egress(payload.egress, result)
        # NOTE: features.views is NOT applied here via D-Bus.  Feature-view
        # entitlements travel in license.views (LicenseSpec) and are persisted
        # by __main__.py via store.update_license() immediately after apply()
        # returns ok.  The feature_guard middleware reads them directly from the
        # association_store — no separate D-Bus verb is needed or correct.
        self._validate_license(payload.license)

        # Phase 2: DELETE stale cloud-managed agents — only if all upserts ok.
        # If any upsert failed we stop here; the sync loop will not advance the
        # version and will retry on the next tick (P1-4).
        if result.ok:
            bundle_ids = {s.agent_id for s in payload.agents}
            await self._delete_stale_agents(cloud_managed, bundle_ids, result)

        return result

    # ------------------------------------------------------------------
    # Section appliers
    # ------------------------------------------------------------------

    async def _apply_providers(
        self, providers: list[ProviderSpec], result: ApplyResult
    ) -> None:
        """Upsert cloud providers.  No delete in Fase 4 (follow-up: managed_by).

        P2: base_url validated against SSRF blocklist before calling the daemon.

        UpdateProvider requires (provider_id, draft_json): the existing list is
        indexed by alias to retrieve the provider_id before calling the verb.
        """
        existing = await self._proxy.call_list("list_providers")
        existing_by_alias = {p.get("alias", ""): p for p in existing}

        for spec in providers:
            if spec.base_url and not _is_safe_base_url(spec.base_url):
                logger.warning(
                    "hermes.config_sync.applier.provider_unsafe_base_url",
                    extra={"alias": spec.alias},
                )
                result.failed.append(f"provider:{spec.alias}:unsafe_base_url")
                continue

            draft = _provider_draft(spec)
            if spec.alias not in existing_by_alias:
                resp = await self._call_mutator("add_provider", json.dumps(draft))
            else:
                provider_id = existing_by_alias[spec.alias].get("provider_id", "")
                resp = await self._call_mutator(
                    "update_provider", provider_id, json.dumps(draft)
                )

            if _is_ok_lenient(resp):
                result.applied += 1
            else:
                result.failed.append(f"provider:{spec.alias}")

    async def _apply_integrations(
        self, integrations: list[IntegrationSpec], result: ApplyResult
    ) -> None:
        """Push Composio API key.

        P0-4: Only push the key when no local key exists (has_key=False from the
        daemon), to avoid overwriting a locally-configured key with a cloud value.
        managed_by tracking for integrations is a Fase 5 follow-up.

        Security: the api_key is NEVER logged; only its presence is logged.
        """
        for spec in integrations:
            if spec.kind != "composio":
                logger.warning(
                    "hermes.config_sync.applier.unknown_integration_kind",
                    extra={"kind": spec.kind},
                )
                result.failed.append(f"integration:{spec.kind}")
                continue

            # Check for existing local (non-cloud) key before overwriting.
            status = await self._proxy.call_dict("get_composio_status")
            if status.get("has_key") and not status.get("managed_by") == _CLOUD_MANAGED:
                # A locally-configured key exists; do not overwrite it.
                logger.info(
                    "hermes.config_sync.applier.integration_skipped_local_key",
                    extra={"kind": spec.kind},
                )
                result.applied += 1
                continue

            # Never log spec.api_key — log only that we are pushing it.
            logger.info(
                "hermes.config_sync.applier.integration_pushing_key",
                extra={"kind": spec.kind},
            )
            resp = await self._call_mutator("set_composio_api_key", spec.api_key)
            if _is_ok_strict(resp):
                result.applied += 1
            else:
                result.failed.append(f"integration:{spec.kind}")

    async def _apply_mcp(
        self, servers: list[McpSpec], result: ApplyResult
    ) -> None:
        """Upsert MCP servers.  No delete in Fase 4 (follow-up: managed_by)."""
        existing = await self._proxy.call_list("list_mcp_servers")
        existing_ids = {s.get("server_id", "") for s in existing}

        for spec in servers:
            if spec.server_id in existing_ids:
                # No update verb for MCP; skip (idempotent).
                result.applied += 1
                continue
            draft = {
                "server_id": spec.server_id,
                "label": spec.label or spec.server_id,
                "argv": spec.argv,
                "env": spec.env,
                "force": False,
            }
            resp = await self._call_mutator("add_mcp_server", json.dumps(draft))
            if _is_ok_lenient(resp):
                result.applied += 1
            else:
                result.failed.append(f"mcp:{spec.server_id}")

    async def _apply_skills(
        self, skills: list[SkillSpec], result: ApplyResult
    ) -> None:
        """Install hub skills (idempotent; daemon is no-op if already installed)."""
        for spec in skills:
            resp = await self._call_mutator("install_hub_skill", spec.identifier, False)
            if _is_ok_lenient(resp):
                result.applied += 1
            else:
                result.failed.append(f"skill:{spec.identifier}")

    async def _upsert_agents(
        self,
        specs: list[AgentSpec],
        cloud_managed: dict[str, dict],
        result: ApplyResult,
    ) -> None:
        """Upsert-only phase.  Deletes happen separately after all upserts (P1-4)."""
        for spec in specs:
            ok = await self._upsert_agent(spec, cloud_managed)
            if ok:
                result.applied += 1
            else:
                result.failed.append(f"agent:{spec.agent_id}")

    async def _delete_stale_agents(
        self,
        cloud_managed: dict[str, dict],
        bundle_ids: set[str],
        result: ApplyResult,
    ) -> None:
        """Delete cloud-managed agents absent from the bundle (P1-4: runs last)."""
        for agent_id in cloud_managed:
            if agent_id not in bundle_ids:
                ok = await self._delete_agent(agent_id)
                if ok:
                    result.applied += 1
                else:
                    result.failed.append(f"agent:delete:{agent_id}")

    async def _apply_consents(
        self, consents: list[ConsentSpec], result: ApplyResult
    ) -> None:
        """Grant LOW-risk consents.  HIGH-risk are skipped (human approval required)."""
        existing = await self._proxy.call_list("list_consents")
        existing_caps = {c.get("capability", "") for c in existing}

        for spec in consents:
            if _is_high_risk_consent(spec.capability):
                logger.warning(
                    "hermes.config_sync.applier.high_risk_consent_skipped",
                    extra={"capability": spec.capability},
                )
                result.failed.append(f"consent:high_risk:{spec.capability}")
                continue
            if spec.capability in existing_caps:
                result.applied += 1
                continue
            resp = await self._call_mutator(
                "grant_consent", spec.capability, spec.scope
            )
            # P2: strict check for consent — None/{} not treated as success.
            if _is_ok_strict(resp):
                result.applied += 1
            else:
                result.failed.append(f"consent:{spec.capability}")

    async def _apply_egress(
        self, egress: EgressSpec, result: ApplyResult
    ) -> None:
        """Add domains to the allow-list.  Never removes; never changes mode.

        P0-3 domain validation: each domain must pass _DOMAIN_RE (same as the
        REST egress endpoint).  Wildcards, IPs, and whitespace are all rejected
        before the D-Bus call is made.
        """
        existing = await self._proxy.call_list("list_egress_grants")
        existing_domains = {g.get("domain", "") for g in existing}

        for domain in egress.allow_domains:
            normalised = domain.strip().lower().removeprefix("*.").rstrip(".")
            if not normalised or not _DOMAIN_RE.match(normalised):
                logger.warning(
                    "hermes.config_sync.applier.egress_invalid_domain",
                    extra={"domain": domain[:64]},
                )
                result.failed.append(f"egress:invalid:{domain[:64]}")
                continue
            if normalised in existing_domains:
                result.applied += 1
                continue
            resp = await self._call_mutator("add_egress_domain", normalised)
            # P2: strict check for egress.
            if _is_ok_strict(resp):
                result.applied += 1
            else:
                result.failed.append(f"egress:{normalised}")

    def _validate_license(self, license_spec: LicenseSpec) -> None:
        if not license_spec.plan:
            logger.warning("hermes.config_sync.applier.license_missing_plan")

    # ------------------------------------------------------------------
    # Agent upsert helpers
    # ------------------------------------------------------------------

    async def _upsert_agent(
        self, spec: AgentSpec, cloud_managed: dict[str, dict]
    ) -> bool:
        draft = _agent_draft(spec)
        if spec.agent_id in cloud_managed:
            resp = await self._proxy.call_mutator(
                "update_agent", spec.agent_id, json.dumps(draft)
            )
        else:
            draft["agent_id"] = spec.agent_id
            draft["managed_by"] = _CLOUD_MANAGED
            resp = await self._call_mutator("create_agent", json.dumps(draft))

        if not _is_ok_lenient(resp):
            return False

        created_id = (resp or {}).get("agent_id") or (resp or {}).get("id") or spec.agent_id
        for cap in spec.capabilities:
            await self._proxy.call_mutator(
                "bind_capability_to_agent",
                created_id,
                cap.get("kind", "skill"),
                cap.get("id", ""),
                cap.get("version", ""),
            )
        return True

    async def _delete_agent(self, agent_id: str) -> bool:
        try:
            return bool(await self._proxy.call_bool("delete_agent", agent_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "hermes.config_sync.applier.delete_agent_failed",
                extra={"agent_id": agent_id, "reason": str(exc)},
            )
            return False

    # ------------------------------------------------------------------
    # Proxy wrappers — enforce the verb allow-list (P0-3)
    # ------------------------------------------------------------------

    async def _fetch_agents(self) -> list[dict]:
        try:
            return await self._proxy.call_list("list_agents")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "hermes.config_sync.applier.fetch_agents_failed",
                extra={"reason": str(exc)},
            )
            return []

    async def _call_mutator(self, verb: str, *args: Any) -> dict:
        """Call a D-Bus mutator guarded by the allow-list.  Never raises."""
        if verb not in _ALLOWED_VERBS:
            logger.error(
                "hermes.config_sync.applier.verb_not_allowed",
                extra={"verb": verb},
            )
            return {"ok": False, "error": "verb_not_in_allowlist"}

        try:
            result = await self._proxy.call_mutator(verb, *args)
            return result if isinstance(result, dict) else {"ok": bool(result)}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "hermes.config_sync.applier.mutator_failed",
                extra={"verb": verb, "reason": str(exc)},
            )
            return {"ok": False}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _cloud_managed_index(live_agents: list[dict]) -> dict[str, dict]:
    return {
        a["agent_id"]: a
        for a in live_agents
        if a.get("managed_by") == _CLOUD_MANAGED
    }


def _is_ok_lenient(result: dict | bool | None) -> bool:
    """Lenient check: None/{} treated as success for non-sensitive sections.

    Used for: providers, mcp, skills, agents, features.
    These sections have existing daemon verbs that may return {} on success.
    """
    if isinstance(result, bool):
        return result
    if result is None:
        return True
    if isinstance(result, dict):
        return result.get("ok", True) is not False
    return False


def _is_ok_strict(result: dict | bool | None) -> bool:
    """Strict check: requires explicit {"ok": True}.  None/{} → failure.

    P2: Used for sensitive sections — egress, consents, integrations.
    An empty or absent "ok" is not safe to assume success here.
    """
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        return result.get("ok") is True
    return False


def _is_high_risk_consent(capability: str) -> bool:
    return any(capability.startswith(prefix) for prefix in _HIGH_RISK_CONSENT_PREFIXES)


def _agent_draft(spec: AgentSpec) -> dict:
    return {
        "name": spec.name,
        "role": spec.role,
        "register": spec.register_tone,
        "primary_mission": spec.primary_mission,
        "instructions": spec.instructions,
        "color": spec.color,
        "language": spec.language,
        "golden_rules": spec.golden_rules,
        "forbidden_phrases": spec.forbidden_phrases,
        "autonomy_level": spec.autonomy_level,
        "department": spec.department,
        "provider_alias": spec.provider_alias,
    }


def _provider_draft(spec: ProviderSpec) -> dict:
    draft: dict = {
        "kind": spec.kind,
        "alias": spec.alias,
        "default_model": spec.default_model,
        "set_active": spec.set_active,
    }
    if spec.base_url:
        draft["base_url"] = spec.base_url
    return draft
