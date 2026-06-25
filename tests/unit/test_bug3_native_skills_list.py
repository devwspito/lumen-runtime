"""Regression test — BUG 3: agent-created skills must appear in list_skills_native.

Root cause: the old list_skills() in audit_api read skill_packages_view, which
only contained cage-signed/composio skills. Skills written by the agent's
skill_manage tool landed in $HERMES_HOME/skills/<name>/SKILL.md but never in
the DB, so the Habilidades view showed an empty list.

Fix: list_skills_native() enumerates the Neus native skills dir directly,
reading governance from SKILL.md frontmatter.metadata when present.

This file contains the canonical regression test that must stay green.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from hermes.agents_os.infrastructure.dbus_runtime_service import (
    _list_native_skills_primary,
    _skill_md_to_dto,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_agent_skill(skills_root: Path, name: str) -> Path:
    """Write a minimal SKILL.md as the agent's skill_manage tool would.

    No governance metadata (no cage signing, no DB row) — this is the
    exact situation that caused BUG 3.
    """
    skill_dir = skills_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = (
        f"---\nname: {name}\ndescription: Agent-created test skill\nversion: '1'\n---\n\n"
        "## When\n- The user asks to run the test task.\n\n"
        "## Procedure\n1. Execute the task steps.\n\n"
        "## Verification\n- Confirm the task completed.\n"
    )
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content)
    return skill_file


def _write_cage_skill(skills_root: Path, name: str, signature_hex: str = "a" * 64) -> Path:
    """Write a SKILL.md with cage governance metadata (as SkillStoreAdapter would)."""
    import yaml as _yaml  # noqa: PLC0415

    skill_dir = skills_root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    package_id = str(uuid4())
    skill_id = str(uuid4())
    signed_at = "2026-06-25T00:00:00+00:00"
    meta = {
        "package_id": package_id,
        "skill_id": skill_id,
        "state": "validated",
        "signing_method": "v2",
        "signature_hex": signature_hex,
        "signed_at": signed_at,
        "validated_at": signed_at,
        "surface_kinds": ["skill_store"],
        "version": 1,
    }
    fm = {
        "name": name,
        "description": "Cage-signed test skill",
        "version": "1",
        "metadata": meta,
    }
    content = f"---\n{_yaml.dump(fm).rstrip()}\n---\n\n## When\n- always\n\n## Procedure\n1. run\n"
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content)
    return skill_file


# ---------------------------------------------------------------------------
# BUG 3 regression: agent-created skill (no DB row) is returned
# ---------------------------------------------------------------------------


class TestBug3AgentCreatedSkillsAppearInList:
    def test_agent_skill_only_on_disk_is_returned(self, tmp_path: Path) -> None:
        """Core BUG 3 regression: a SKILL.md with no DB row must appear in the list."""
        skills_root = tmp_path / "skills"
        _write_agent_skill(skills_root, "my-agent-skill")

        skills = _list_native_skills_primary(skills_root=skills_root)

        names = [s["skill_name"] for s in skills]
        assert "my-agent-skill" in names, (
            "agent-created skill (no DB row) must appear in list_skills_native()"
        )

    def test_agent_skill_has_native_state(self, tmp_path: Path) -> None:
        """Agent-created skills surface with state='native' (no cage signing)."""
        skills_root = tmp_path / "skills"
        _write_agent_skill(skills_root, "native-skill")

        skills = _list_native_skills_primary(skills_root=skills_root)

        skill = next(s for s in skills if s["skill_name"] == "native-skill")
        assert skill["state"] == "native"
        assert skill["signing_method"] == "none"
        assert skill["signature_short"] is None

    def test_cage_skill_has_governance_metadata(self, tmp_path: Path) -> None:
        """Cage-signed skills expose their governance fields from frontmatter."""
        skills_root = tmp_path / "skills"
        sig = "deadbeef" * 8  # 64 hex chars
        _write_cage_skill(skills_root, "cage-skill", signature_hex=sig)

        skills = _list_native_skills_primary(skills_root=skills_root)

        skill = next(s for s in skills if s["skill_name"] == "cage-skill")
        assert skill["state"] == "validated"
        assert skill["signing_method"] == "v2"
        assert skill["signature_short"] == sig[:12]

    def test_both_origins_appear_together(self, tmp_path: Path) -> None:
        """Both agent-created and cage-signed skills appear in the same list."""
        skills_root = tmp_path / "skills"
        _write_agent_skill(skills_root, "agent-only")
        _write_cage_skill(skills_root, "cage-signed")

        skills = _list_native_skills_primary(skills_root=skills_root)

        names = {s["skill_name"] for s in skills}
        assert "agent-only" in names
        assert "cage-signed" in names
        assert len(names) == 2

    def test_empty_skills_dir_returns_empty_list(self, tmp_path: Path) -> None:
        skills_root = tmp_path / "no-skills-here"
        skills = _list_native_skills_primary(skills_root=skills_root)
        assert skills == []

    def test_missing_hermes_home_returns_empty_without_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without HERMES_HOME set and no override, returns [] (safe in CI)."""
        monkeypatch.delenv("HERMES_HOME", raising=False)
        skills = _list_native_skills_primary()
        assert skills == []

    def test_hermes_home_env_controls_scan_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When HERMES_HOME is set, skills in $HERMES_HOME/skills/ are found."""
        hermes_home = tmp_path / "hermes-home"
        skills_root = hermes_home / "skills"
        _write_agent_skill(skills_root, "env-skill")

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        skills = _list_native_skills_primary()

        names = [s["skill_name"] for s in skills]
        assert "env-skill" in names

    def test_no_skill_packages_view_needed(self, tmp_path: Path) -> None:
        """list_skills_native() works without a DB at all (no skill_packages_view)."""
        skills_root = tmp_path / "skills"
        _write_agent_skill(skills_root, "no-db-needed")

        # No DB created — this must not raise
        skills = _list_native_skills_primary(skills_root=skills_root)
        assert len(skills) == 1

    def test_package_id_is_stable_for_native_skills(self, tmp_path: Path) -> None:
        """Native skills get a deterministic package_id = 'native:<name>'."""
        skills_root = tmp_path / "skills"
        _write_agent_skill(skills_root, "stable-id-skill")

        skills = _list_native_skills_primary(skills_root=skills_root)
        skill = skills[0]
        assert skill["package_id"] == "native:stable-id-skill"

    def test_cage_skill_package_id_from_frontmatter(self, tmp_path: Path) -> None:
        """Cage-signed skills expose the package_id embedded in frontmatter."""
        skills_root = tmp_path / "skills"
        _write_cage_skill(skills_root, "cage-id-skill")

        skills = _list_native_skills_primary(skills_root=skills_root)
        skill = next(s for s in skills if s["skill_name"] == "cage-id-skill")
        # package_id must not be the default 'native:...' prefix
        assert not skill["package_id"].startswith("native:")


# ---------------------------------------------------------------------------
# SkillStoreAdapter writes governance to frontmatter, not DB (unit)
# ---------------------------------------------------------------------------


class TestSkillStoreAdapterWritesGovernanceToFrontmatter:
    async def test_create_embeds_governance_in_skill_md(self, tmp_path: Path) -> None:
        """SkillStoreAdapter.replay(create) must embed governance in SKILL.md metadata."""
        import yaml as _yaml  # noqa: PLC0415
        from hermes.agents_os.domain.ports.surface_adapter_port import (  # noqa: PLC0415
            CapturedAction,
            ReplayStatus,
        )
        from hermes.agents_os.domain.surface_kind import SurfaceKind  # noqa: PLC0415
        from hermes.capabilities.infrastructure.skill_store_adapter import (  # noqa: PLC0415
            SkillStoreAdapter,
        )

        class _FakeKms:
            async def get_signing_key(self, *, tenant_id: object, key_id: str) -> bytes:  # noqa: ARG002
                return b"fake-key-for-test-32-bytes!!!12"

        skill_root = tmp_path / "skills"
        adapter = SkillStoreAdapter(
            kms=_FakeKms(),
            db_path=tmp_path / "audit.db",
            skill_store_root=skill_root,
        )
        content = (
            "---\nname: gov-skill\ndescription: Test skill\nversion: '1'\n---\n\n"
            "## When\n- always\n\n## Procedure\n1. step\n"
        )
        action = CapturedAction(
            surface_kind=SurfaceKind.SKILL_STORE,
            intent_desc="create gov-skill",
            payload={"action": "create", "name": "gov-skill", "content": content},
            tenant_id=uuid4(),
            human_operator_id=uuid4(),
        )
        outcome = await adapter.replay(action)
        assert outcome.status == ReplayStatus.EXECUTED_OK

        skill_file = skill_root / "gov-skill" / "SKILL.md"
        assert skill_file.exists()

        text = skill_file.read_text()
        end = text.find("---", 3)
        fm = _yaml.safe_load(text[3:end]) or {}
        meta = fm.get("metadata") or {}

        assert meta.get("state") == "validated"
        assert meta.get("signing_method") == "v2"
        assert meta.get("signature_hex"), "signature_hex must be written to frontmatter"
        assert meta.get("package_id"), "package_id must be written to frontmatter"
        assert meta.get("skill_id"), "skill_id must be written to frontmatter"

    async def test_native_list_includes_cage_created_skill(self, tmp_path: Path) -> None:
        """After SkillStoreAdapter.create, list_skills_native returns the skill."""
        from hermes.agents_os.domain.ports.surface_adapter_port import (  # noqa: PLC0415
            CapturedAction,
            ReplayStatus,
        )
        from hermes.agents_os.domain.surface_kind import SurfaceKind  # noqa: PLC0415
        from hermes.capabilities.infrastructure.skill_store_adapter import (  # noqa: PLC0415
            SkillStoreAdapter,
        )

        class _FakeKms:
            async def get_signing_key(self, *, tenant_id: object, key_id: str) -> bytes:  # noqa: ARG002
                return b"fake-key-for-test-32-bytes!!!12"

        skill_root = tmp_path / "skills"
        adapter = SkillStoreAdapter(
            kms=_FakeKms(),
            db_path=tmp_path / "audit.db",
            skill_store_root=skill_root,
        )
        content = (
            "---\nname: listed-cage\ndescription: Listed skill\nversion: '1'\n---\n\n"
            "## When\n- always\n\n## Procedure\n1. step\n"
        )
        action = CapturedAction(
            surface_kind=SurfaceKind.SKILL_STORE,
            intent_desc="create listed-cage",
            payload={"action": "create", "name": "listed-cage", "content": content},
            tenant_id=uuid4(),
            human_operator_id=uuid4(),
        )
        outcome = await adapter.replay(action)
        assert outcome.status == ReplayStatus.EXECUTED_OK

        skills = _list_native_skills_primary(skills_root=skill_root)
        names = [s["skill_name"] for s in skills]
        assert "listed-cage" in names, (
            "cage-created skill must appear in list_skills_native()"
        )
        skill = next(s for s in skills if s["skill_name"] == "listed-cage")
        assert skill["state"] == "validated"
