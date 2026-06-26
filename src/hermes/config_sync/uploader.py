"""UsageUploader — uploads aggregate usage counters to the cloud control plane.

Privacy invariant (HARD):
  Only numeric counters + opaque identifiers are ever sent.
  NEVER: content, prompts, conversation text, URLs, file paths, API keys,
         model responses, or any PII.

Gate: only runs when edition == 'associate' AND is_associated() is True.
  Community edition (not paired) → no-op, no network call, no error.

Telemetry opt-in note:
  This uploader handles AGGREGATE USAGE BILLING DATA (tokens, cost, task counts),
  which is a contractual obligation in the Associate edition — not optional
  observability telemetry.  It is therefore gated on association state alone,
  not on TelemetryOptInService.  The TelemetryOptInService governs trace/log/metric
  exporters (Prometheus, OTLP), which are a separate surface.

Transport:
  POST {cloud_endpoint}/v1/metering
  Authorization: Bearer {instance_secret}
  Content-Type: application/json

  Body shape (see UploadPayload):
    {
      "instance_id": "<uuid>",
      "tenant_id":   "<uuid>",
      "window":      {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
      "items": [
        {
          "agent_id":           "<str|null>",
          "day":                "YYYY-MM-DD",
          "prompt_tokens":      <int>,
          "completion_tokens":  <int>,
          "cost_usd":           <float>,
          "tasks":              <int>,
          "failures":           <int>
        },
        ...
      ]
    }

Fase 7 injection point: add mTLS cert to httpx.post() once the cloud issues
client certs at pairing time (same pattern as config_sync.__main__._fetch_bundle).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from hermes.instance.association_store import SQLiteAssociationStore
    from hermes.shell_server.metering.usage_repo import SQLiteUsageRepository

logger = logging.getLogger("hermes.config_sync.uploader")

_HTTP_TIMEOUT_S = 20.0

# Keys that must NEVER appear in the upload body (privacy regression test anchor).
PROHIBITED_BODY_KEYS: frozenset[str] = frozenset({
    "content",
    "prompt",
    "message",
    "text",
    "url",
    "file",
    "secret",
    "api_key",
    "password",
    "token",
    "conversation",
    "response",
})


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Outcome of one upload_once() call."""

    uploaded_items: int = 0
    skipped_reason: str | None = None  # set on no-op


class _UsageRepoProtocol(Protocol):
    def unsent_aggregates(self) -> list:
        ...

    def mark_uploaded(self, event_ids: list[str]) -> None:
        ...


class _AssociationStoreProtocol(Protocol):
    def is_associated(self) -> bool:
        ...

    def edition(self) -> str:
        ...

    def get(self):
        ...

    def reveal_instance_secret(self) -> str | None:
        ...


class UsageUploader:
    """Uploads aggregate usage counters to the cloud control plane.

    Constructed with injected dependencies so the caller (config_sync loop)
    controls the objects' lifetimes.  All I/O is synchronous (httpx sync
    client) to match the surrounding synchronous fetch pattern in
    config_sync.__main__._fetch_bundle.
    """

    def __init__(
        self,
        *,
        usage_repo: _UsageRepoProtocol,
        association_store: _AssociationStoreProtocol,
    ) -> None:
        self._repo = usage_repo
        self._store = association_store

    def upload_once(self) -> UploadResult:
        """Upload unsent aggregates.  Fail-soft: network errors are logged, not raised."""
        if not self._store.is_associated():
            logger.debug("hermes.config_sync.uploader.skip_not_associated")
            return UploadResult(skipped_reason="not_associated")

        assoc = self._store.get()
        if assoc is None:
            return UploadResult(skipped_reason="not_associated")

        aggregates = self._repo.unsent_aggregates()
        if not aggregates:
            logger.debug("hermes.config_sync.uploader.skip_no_data")
            return UploadResult(skipped_reason="no_data")

        instance_secret = self._store.reveal_instance_secret()
        if not instance_secret:
            logger.warning("hermes.config_sync.uploader.no_instance_secret")
            return UploadResult(skipped_reason="no_secret")

        body = _build_payload(assoc.instance_id, assoc.tenant_id, aggregates)
        url = f"{assoc.cloud_endpoint.rstrip('/')}/v1/metering"

        try:
            resp = httpx.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {instance_secret}",
                    "Content-Type": "application/json",
                },
                timeout=_HTTP_TIMEOUT_S,
                follow_redirects=False,  # SSRF mitigation (same policy as _fetch_bundle)
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "hermes.config_sync.uploader.network_error",
                extra={"reason": str(exc)},
            )
            # Fail-soft: do NOT mark as uploaded; retry on next tick.
            return UploadResult(skipped_reason="network_error")

        if not _is_success(resp.status_code):
            logger.warning(
                "hermes.config_sync.uploader.http_error",
                extra={"status": resp.status_code},
            )
            return UploadResult(skipped_reason=f"http_{resp.status_code}")

        all_ids = _collect_event_ids(aggregates)
        self._repo.mark_uploaded(all_ids)

        logger.info(
            "hermes.config_sync.uploader.uploaded",
            extra={"items": len(aggregates), "events": len(all_ids)},
        )
        return UploadResult(uploaded_items=len(aggregates))


# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------


def _build_payload(
    instance_id: str,
    tenant_id: str,
    aggregates: list,
) -> dict:
    """Build the POST body.  Only numeric counters + opaque IDs — no content."""
    days = [a.day for a in aggregates]
    window = {"start": min(days), "end": max(days)} if days else {"start": "", "end": ""}
    items = [
        {
            "agent_id": a.agent_id,
            "day": a.day,
            "prompt_tokens": a.prompt_tokens,
            "completion_tokens": a.completion_tokens,
            "cost_usd": a.cost_usd,
            "tasks": a.tasks,
            "failures": a.failures,
        }
        for a in aggregates
    ]
    return {
        "instance_id": instance_id,
        "tenant_id": tenant_id,
        "window": window,
        "items": items,
    }


def _is_success(status_code: int) -> bool:
    return 200 <= status_code < 300


def _collect_event_ids(aggregates: list) -> list[str]:
    """Flatten event_ids from all aggregates into a single list."""
    ids: list[str] = []
    for agg in aggregates:
        ids.extend(eid for eid in agg.event_ids if eid)
    return ids
