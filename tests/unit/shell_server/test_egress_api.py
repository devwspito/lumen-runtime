"""Unit tests for the egress domain-validation error shape (spec 025 hallazgo #5).

Before the fix, `POST /deny/add`, `POST /domains/grant`, and
`POST /mcp/domains/grant` returned HTTP 200 with `{"ok": false, "error": ...}`
on an invalid domain — a client that only checks the HTTP status code (curl,
a script, a future frontend view) would believe the domain was
granted/denied when nothing happened. The sibling `POST /mode` endpoint in
the SAME file already used `HTTPException(422, detail={"code", "message"})`
for its own validation error — the owner's standing rule for the whole API
— so the inconsistency was internal to this one file.

These 3 tests only exercise the validation short-circuit (an invalid domain
fails `_DOMAIN_RE` before any persistence or proxy-socket I/O happens), so
they are fully hermetic: no filesystem writes under /var/lib/hermes, no
UNIX socket connect.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes.shell_server.egress_api import create_egress_router

pytestmark = pytest.mark.unit

_INVALID_DOMAIN = "not a domain!!"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_egress_router())
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/egress/deny/add",
        "/api/v1/egress/domains/grant",
        "/api/v1/egress/mcp/domains/grant",
    ],
)
class TestInvalidDomainReturns422NotOkFalse:
    def test_status_code_is_422(self, path: str) -> None:
        r = _client().post(path, json={"domain": _INVALID_DOMAIN})
        assert r.status_code == 422

    def test_body_matches_the_rest_of_the_api_shape(self, path: str) -> None:
        """Same {"detail": {"code", "message"}} shape as POST /mode's own 422
        (egress_api.py's own set_mode) — never {"ok": false} with HTTP 200."""
        r = _client().post(path, json={"domain": _INVALID_DOMAIN})
        body = r.json()
        assert "ok" not in body
        assert body["detail"]["code"] == "invalid_domain"
        assert _INVALID_DOMAIN in body["detail"]["message"]

    def test_a_status_only_client_cannot_mistake_this_for_success(self, path: str) -> None:
        """A client that only checks 2xx (the exact failure mode this fixes)
        must see a NON-2xx status for an invalid domain."""
        r = _client().post(path, json={"domain": _INVALID_DOMAIN})
        assert not (200 <= r.status_code < 300)
