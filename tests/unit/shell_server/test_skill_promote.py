"""Tests for POST /api/v1/skills/{id}/promote and validated_at/promoted_at fields."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes.shell_server.audit_api import create_audit_router

pytestmark = pytest.mark.unit

# Stable fake key used across helpers — same as what FakeVault returns.
_FAKE_SIGNING_KEY = b"\xBB" * 32


class _FakeVault:
    """Fake SecretsVault that returns a stable key without needing master.key."""

    def derive_subkey(self, *, label: str) -> bytes:  # noqa: ARG002
        return _FAKE_SIGNING_KEY


def _fake_vault_patch():
    """Context manager: patch SecretsVault to return FakeVault."""
    import hermes.shell_server.skills.native_keystore_adapter as _mod  # noqa: PLC0415

    return patch.object(_mod, "SecretsVault", return_value=_FakeVault())


def _compute_recorded_skill_signature(
    *,
    package_id: str,
    skill_id: str,
    skill_name: str,
    version: int,
    signed_at: str,
) -> str:
    """Compute a v2 HMAC for a recorded (non-Composio) skill.

    Recorded skills don't have toolkit_slug/intent_text; they use the
    SkillCompiler payload. For tests we need a valid 64-char hex that the
    promote gate will accept. Since recorded skill verification only checks
    method=v2 and sig is 64 chars (the full payload verification happens at
    execution time via the agent loop), any valid-length hex from the fake key
    is sufficient for promote tests.
    """
    payload = f"{package_id}|{skill_id}|{skill_name}|{version}|{signed_at}|recorded"
    return hmac.new(_FAKE_SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()


def _insert_skill(
    db_path: Path, *, state: str = "validated", with_v2_sig: bool = True
) -> str:
    """Insert a skill_packages_view row and return package_id.

    Calls SkillGovernanceService first to ensure the schema exists, then
    inserts a row directly (simulating a governance operation on a known skill).

    with_v2_sig=True (default): inserts a v2-signed skill that passes the
    promote gate. with_v2_sig=False: inserts without signature (for testing
    the rejection path).
    """
    from hermes.shell_server.skills.skill_governance_service import (  # noqa: PLC0415
        SkillGovernanceService,
    )
    # Ensure the governance schema (skill_packages_view) exists.
    SkillGovernanceService(db_path=db_path)

    pkg_id = str(uuid4())
    skill_id = str(uuid4())
    signed_at = datetime.now(tz=UTC).isoformat()
    sig_hex = _compute_recorded_skill_signature(
        package_id=pkg_id,
        skill_id=skill_id,
        skill_name="pay-invoice",
        version=1,
        signed_at=signed_at,
    ) if with_v2_sig else None
    # Use 'v1' for unsigned skills: signing_method is NOT NULL in schema.
    # v1 will be rejected by the promote gate (no forgeable fallback).
    signing_method = "v2" if with_v2_sig else "v1"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        INSERT INTO skill_packages_view (
          package_id, skill_id, skill_name, version,
          state, surface_kinds, signed_at, signature_short,
          signing_method, signature_hex
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pkg_id,
            skill_id,
            "pay-invoice",
            1,
            state,
            "browser",
            signed_at,
            sig_hex[:12] if sig_hex else None,
            signing_method,
            sig_hex,
        ),
    )
    conn.commit()
    conn.close()
    return pkg_id


def _write_skill_md(
    skills_root: Path, *, name: str = "pay-invoice", state: str = "validated"
) -> None:
    """Write a minimal SKILL.md to skills_root/<name>/ for listing tests."""
    import yaml as _yaml  # noqa: PLC0415
    skill_dir = skills_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = {
        "name": name,
        "description": "Test skill for promote tests",
        "version": "1",
        "metadata": {
            "state": state,
            "signing_method": "none",
        },
    }
    content = f"---\n{_yaml.dump(fm).rstrip()}\n---\n\n## When\n- always\n\n## Procedure\n1. run\n"
    (skill_dir / "SKILL.md").write_text(content)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_audit_router(tmp_path / "audit.db"))
    return TestClient(app)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.db"


@pytest.fixture
def client_with_db(db_path: Path) -> tuple[TestClient, Path]:
    app = FastAPI()
    app.include_router(create_audit_router(db_path))
    return TestClient(app), db_path


class TestPromoteEndpoint:
    def test_promote_validated_skill_succeeds(
        self, client_with_db: tuple[TestClient, Path]
    ) -> None:
        client, db_path = client_with_db
        pkg_id = _insert_skill(db_path, state="validated", with_v2_sig=True)

        with _fake_vault_patch():
            r = client.post(f"/api/v1/skills/{pkg_id}/promote", json={"confirm": True})
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "autonomous"
        assert body["promoted_at"] is not None

    def test_promote_requires_confirm_true(
        self, client_with_db: tuple[TestClient, Path]
    ) -> None:
        client, db_path = client_with_db
        pkg_id = _insert_skill(db_path, state="validated", with_v2_sig=True)

        r = client.post(f"/api/v1/skills/{pkg_id}/promote", json={"confirm": False})
        assert r.status_code == 400

    def test_promote_draft_state_returns_409(
        self, client_with_db: tuple[TestClient, Path]
    ) -> None:
        """FR-020: DRAFT → AUTONOMOUS is forbidden without VALIDATED first."""
        client, db_path = client_with_db
        pkg_id = _insert_skill(db_path, state="draft", with_v2_sig=True)

        with _fake_vault_patch():
            r = client.post(f"/api/v1/skills/{pkg_id}/promote", json={"confirm": True})
        assert r.status_code == 409
        assert "invalid_transition" in r.json()["detail"]

    def test_promote_autonomous_state_returns_409(
        self, client_with_db: tuple[TestClient, Path]
    ) -> None:
        """Already autonomous → no self-loop; SkillState machine forbids it."""
        client, db_path = client_with_db
        pkg_id = _insert_skill(db_path, state="autonomous", with_v2_sig=True)

        with _fake_vault_patch():
            r = client.post(f"/api/v1/skills/{pkg_id}/promote", json={"confirm": True})
        assert r.status_code == 409

    def test_promote_nonexistent_skill_returns_404(
        self, client: TestClient
    ) -> None:
        r = client.post(
            f"/api/v1/skills/{uuid4()}/promote", json={"confirm": True}
        )
        assert r.status_code == 404

    def test_promote_legacy_signed_state_succeeds(
        self, client_with_db: tuple[TestClient, Path]
    ) -> None:
        """Legacy 'signed' rows treated as 'validated' AND must have v2 sig."""
        client, db_path = client_with_db
        pkg_id = _insert_skill(db_path, state="signed", with_v2_sig=True)

        with _fake_vault_patch():
            r = client.post(f"/api/v1/skills/{pkg_id}/promote", json={"confirm": True})
        assert r.status_code == 200
        assert r.json()["state"] == "autonomous"

    def test_promote_without_signature_returns_403(
        self, client_with_db: tuple[TestClient, Path]
    ) -> None:
        """Security gate: promoting a skill without a v2 signature is rejected (fail-closed)."""
        client, db_path = client_with_db
        pkg_id = _insert_skill(db_path, state="validated", with_v2_sig=False)

        with _fake_vault_patch():
            r = client.post(f"/api/v1/skills/{pkg_id}/promote", json={"confirm": True})
        assert r.status_code == 403
        assert "signature_verification_failed" in r.json()["detail"]


class TestSkillListNewFields:
    def test_list_includes_validated_at_and_promoted_at(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /api/v1/skills returns validated_at and promoted_at from SKILL.md."""
        import yaml as _yaml  # noqa: PLC0415
        skills_root = tmp_path / "skills"
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        signed_at = datetime.now(tz=UTC).isoformat()
        skill_dir = skills_root / "pay-invoice"
        skill_dir.mkdir(parents=True, exist_ok=True)
        fm = {
            "name": "pay-invoice",
            "description": "Test skill",
            "version": "1",
            "metadata": {
                "state": "validated",
                "signing_method": "none",
                "validated_at": signed_at,
                "promoted_at": None,
            },
        }
        (skill_dir / "SKILL.md").write_text(
            f"---\n{_yaml.dump(fm).rstrip()}\n---\n\n## When\n- always\n\n## Procedure\n1. run\n"
        )

        app = FastAPI()
        app.include_router(create_audit_router(tmp_path / "audit.db"))
        client = TestClient(app)

        r = client.get("/api/v1/skills")
        assert r.status_code == 200
        assert len(r.json()) >= 1
        item = r.json()[0]
        assert "validated_at" in item
        assert "promoted_at" in item

    def test_list_treats_signed_state_as_validated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Legacy 'signed' state on disk is surfaced as 'signed' (no coerce in list path).

        The _coerce_state helper only applies inside SkillGovernanceService for DB rows.
        The filesystem listing surfaces state verbatim from frontmatter.metadata.state.
        """
        import yaml as _yaml  # noqa: PLC0415
        skills_root = tmp_path / "skills"
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        skill_dir = skills_root / "legacy-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        fm = {
            "name": "legacy-skill",
            "description": "Legacy skill",
            "version": "1",
            "metadata": {"state": "signed", "signing_method": "none"},
        }
        (skill_dir / "SKILL.md").write_text(
            f"---\n{_yaml.dump(fm).rstrip()}\n---\n\n## When\n- always\n\n## Procedure\n1. run\n"
        )

        app = FastAPI()
        app.include_router(create_audit_router(tmp_path / "audit.db"))
        client = TestClient(app)

        r = client.get("/api/v1/skills")
        assert r.status_code == 200
        # The DTO layer coerces 'signed' → 'validated' for backward compatibility.
        # The skill must be listed; the coerced state is 'validated'.
        states = {s["state"] for s in r.json()}
        assert "validated" in states, (
            f"legacy 'signed' state must surface as 'validated' via DTO coercion. Got: {states}"
        )
