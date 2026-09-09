"""companions — `/etc/hermes/companions.json` loader + strict validation (024).

A "companion" is a sibling container Safent's launcher provisions and joins
to a FIXED network (`safent-companions`, 10.201.0.0/24) at install time —
today, exactly one: `safent-ads`. Unlike a user-typed managed-remote URL
(`hermes.shell_server.managed_remote_endpoints`, the OPPOSITE trust
direction: DNS-name-only, no IP literal), a companion's destination is
PINNED by the local installer, never by the owner typing a URL and never by
the daemon:

  - `/etc/hermes/companions.json` is a HOST bind-mount, read-only inside the
    container (run-safent.sh). `/etc` is read-only to hermes-runtime.service
    (ProtectSystem=strict) — no D-Bus verb, no REST path, no config-sync
    verb writes this file (INV-2). This loader is the ONLY reader.
  - The path is a CONSTANT — no env override — an override would be a knob
    a compromised process could point elsewhere (mirrors managed_remote_
    endpoints._ENDPOINTS_PATH's own no-override rule, stricter here since a
    companion's IP is trusted for a live nftables accept rule).
  - `slug` must be one of the shipped companions (`_COMPANION_SLUGS`) — an
    unknown slug is a tampered/future file this build doesn't understand.
  - `url` must be `https://<host ending in .safent.internal>:8443/...` — the
    EXACT INVERSE of managed_remote_endpoints' DNS-name rule: here the name
    is fixed by us, never a name the owner could point at a third party.
  - `ip` must sit inside the companion subnet (10.201.0.0/24) and match
    `port == 8443` — both are product constants (spec.md §7); a value
    outside them is a fabricated/corrupted file, not "a different
    companion" — reject entirely (SC-3).
  - `ca_fingerprint` is RE-DERIVED from the DER bytes at `ca_path` and
    cross-checked against the JSON value — the JSON field is a tamper
    check, never the root of trust; `ca_path`/the `bearer_ref` file target
    must live under the companion's own read-only mount directory (no
    arbitrary host file read via a corrupted JSON value).
  - Any single anomaly discards the WHOLE entry (fail-soft to "no
    companion" for that slug, never a partially-trusted one) — a companion
    is optional infrastructure; Safent boots and runs without it (FR-3).

Infrastructure layer: filesystem-backed (JSON + PEM), stdlib only.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_COMPANIONS_PATH = Path("/etc/hermes/companions.json")
_COMPANION_MOUNT_DIR = Path("/etc/hermes/companions")
_COMPANION_SUBNET = ipaddress.ip_network("10.201.0.0/24")
_ALLOWED_PORT = 8443
_ALLOWED_SCHEME = "https"
_HOST_SUFFIX = ".safent.internal"
_BEARER_REF_SCHEME = "file:"

# The only companions this build knows how to seed/trust. An entry for any
# other slug is a tampered or future-version file — rejected, not ignored
# per-field (a slug we don't recognise gets NO partial trust).
_COMPANION_SLUGS: frozenset[str] = frozenset({"safent-ads"})


class CompanionConfigError(ValueError):
    """Raised internally when one companion entry fails validation.

    Never escapes `load_companions`/`get_companion` — callers only ever see
    the entry silently dropped (fail-soft, SC-3).
    """


@dataclass(frozen=True)
class CompanionEndpoint:
    """A validated companion — the ONLY shape `_grant_mcp_egress_for_managed_
    remote`/`_mcp_connect`/the nft generator are allowed to trust."""

    slug: str
    url: str
    host: str
    ip: str
    port: int
    ca_path: str
    ca_fingerprint: str
    bearer_ref: str

    @property
    def argv(self) -> list[str]:
        """The mcp-remote argv this companion is reached through (plan.md §1.4).

        `--header "Authorization: Bearer ${ADS_BEARER}"` is the literal
        `${VAR}` placeholder text, NOT the bearer's value (INV-4) — mcp-remote
        expands it from its own process env at connect time (`ADS_BEARER`,
        filled by `_autowire_companion_env`). Without this flag mcp-remote
        sends no Authorization header at all and every request to `/mcp`
        (BearerTokenMiddleware, safent-ads's mcp/presentation/http.py) is
        rejected 401 — the companion would list as a seeded server with zero
        reachable tools, not "absent" (FR-3) but silently broken instead.
        """
        return [
            "npx", "-y", "mcp-remote", self.url,
            "--header", "Authorization: Bearer ${ADS_BEARER}",
        ]


def load_companions(*, path: Path = _COMPANIONS_PATH) -> dict[str, CompanionEndpoint]:
    """Return {slug: CompanionEndpoint} for every entry in *path* that
    validates. Fail-soft to {} on ANY anomaly (missing file, bad owner/
    permissions, malformed JSON, wrong version, any entry failing
    validation) — a companion is optional infrastructure (FR-3)."""
    if not _is_trustworthy_file(path):
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != 1:
        return {}
    raw_entries = data.get("companions")
    if not isinstance(raw_entries, list):
        return {}

    result: dict[str, CompanionEndpoint] = {}
    for raw in raw_entries:
        try:
            endpoint = _validate_companion_entry(raw)
        except CompanionConfigError:
            continue
        result[endpoint.slug] = endpoint
    return result


def get_companion(slug: str, *, path: Path = _COMPANIONS_PATH) -> CompanionEndpoint | None:
    """Return the validated companion for *slug*, or None if absent/invalid."""
    return load_companions(path=path).get(slug)


def read_companion_bearer(endpoint: CompanionEndpoint) -> str | None:
    """Read the bearer token *endpoint.bearer_ref* points at.

    Returns None (never raises) on any I/O error or an out-of-mount path —
    the bearer never appears in argv/logs/REST (INV-4); this is the ONLY
    function allowed to read its value, and only at connect time.
    """
    if not endpoint.bearer_ref.startswith(_BEARER_REF_SCHEME):
        return None
    bearer_path = Path(endpoint.bearer_ref[len(_BEARER_REF_SCHEME):])
    if not _is_within_companion_mount(bearer_path):
        return None
    try:
        return bearer_path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Validation internals
# ---------------------------------------------------------------------------


def _is_trustworthy_file(path: Path) -> bool:
    """uid 0, no group/other write bit — a companion file the owner/root
    installed, never one an unprivileged/compromised process could plant."""
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_uid != 0:
        return False
    return not bool(st.st_mode & 0o022)


def _validate_companion_entry(raw: object) -> CompanionEndpoint:
    if not isinstance(raw, dict):
        raise CompanionConfigError("companion entry must be a JSON object")

    slug = str(raw.get("slug") or "")
    if slug not in _COMPANION_SLUGS:
        raise CompanionConfigError(f"unknown companion slug: {slug!r}")

    host = _validate_url(str(raw.get("url") or ""))
    ip = _validate_ip(str(raw.get("ip") or ""))
    port = raw.get("port")
    if port != _ALLOWED_PORT:
        raise CompanionConfigError(f"companion port must be {_ALLOWED_PORT}")

    ca_path = _validate_mounted_path(str(raw.get("ca_path") or ""))
    ca_fingerprint = _validate_ca_fingerprint(ca_path, str(raw.get("ca_fingerprint") or ""))
    bearer_ref = _validate_bearer_ref(str(raw.get("bearer_ref") or ""))

    return CompanionEndpoint(
        slug=slug, url=str(raw.get("url")), host=host, ip=ip, port=port,
        ca_path=str(ca_path), ca_fingerprint=ca_fingerprint, bearer_ref=bearer_ref,
    )


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != _ALLOWED_SCHEME:
        raise CompanionConfigError(f"companion url must use https:// (got {parsed.scheme!r})")
    host = parsed.hostname or ""
    if not host.endswith(_HOST_SUFFIX):
        raise CompanionConfigError(f"companion host must end in {_HOST_SUFFIX!r}: {host!r}")
    if parsed.port is not None and parsed.port != _ALLOWED_PORT:
        raise CompanionConfigError(f"companion url port must be {_ALLOWED_PORT}")
    return host


def _validate_ip(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise CompanionConfigError(f"companion ip is not a valid address: {ip!r}") from exc
    if addr not in _COMPANION_SUBNET:
        raise CompanionConfigError(f"companion ip {ip!r} outside {_COMPANION_SUBNET}")
    return ip


def _validate_mounted_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not _is_within_companion_mount(path):
        raise CompanionConfigError(f"path outside the companion mount: {raw_path!r}")
    return path


def _is_within_companion_mount(path: Path) -> bool:
    try:
        path.relative_to(_COMPANION_MOUNT_DIR)
    except ValueError:
        return False
    return True


def _validate_ca_fingerprint(ca_path: Path, claimed_fingerprint: str) -> str:
    """Recompute the SHA-256 of the DER at *ca_path* and cross-check it
    against *claimed_fingerprint* — the JSON value is a tamper check, never
    the root of trust (see module docstring)."""
    try:
        pem = ca_path.read_text(encoding="utf-8")
        der = ssl.PEM_cert_to_DER_cert(pem)
    except (OSError, ValueError) as exc:
        raise CompanionConfigError(f"cannot read/parse CA at {ca_path}: {exc}") from exc
    actual = f"sha256:{hashlib.sha256(der).hexdigest()}"
    if actual != claimed_fingerprint:
        raise CompanionConfigError("ca_fingerprint does not match the CA on disk")
    return actual


def _validate_bearer_ref(bearer_ref: str) -> str:
    if not bearer_ref.startswith(_BEARER_REF_SCHEME):
        raise CompanionConfigError(f"bearer_ref must start with {_BEARER_REF_SCHEME!r}")
    _validate_mounted_path(Path(bearer_ref[len(_BEARER_REF_SCHEME):]))
    return bearer_ref
