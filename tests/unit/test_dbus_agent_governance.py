"""Gobernanza de agentes por D-Bus (wiring): lecturas libres, mutadores con
autoría por sender_uid (fail-closed). Estado nativo del daemon (Principio 0)."""

from __future__ import annotations

import asyncio

import pytest

from hermes.agents.application.serialization import draft_from_dict
from hermes.agents.domain.agent import DEFAULT_AGENT_ID
from hermes.agents_os.infrastructure.dbus_runtime_service import (
    DbusAuthorizationError,
    DbusRuntimeServiceWiring,
)
from hermes.agents.infrastructure.sqlite_agent_registry import SqliteAgentRegistry

_OPERATOR_UID = 1000


def _wiring(tmp_path):
    reg = SqliteAgentRegistry(db_path=tmp_path / "shell-state.db")
    wiring = DbusRuntimeServiceWiring(
        agent_state=None,
        approval_gate=None,
        authorized_uids=frozenset({_OPERATOR_UID}),
        agent_registry=reg,
    )
    return wiring, reg


def test_list_and_default_are_readonly(tmp_path):
    wiring, _ = _wiring(tmp_path)
    agents = wiring.list_agents()
    assert len(agents) >= 1  # default + seeded roster
    defaults = [a for a in agents if a["agent_id"] == DEFAULT_AGENT_ID]
    assert len(defaults) == 1
    assert defaults[0]["is_default"] is True


def test_create_requires_authorized_uid(tmp_path):
    wiring, reg = _wiring(tmp_path)
    initial_count = len(reg.list_agents())
    draft = draft_from_dict({"name": "X"})
    with pytest.raises(DbusAuthorizationError):
        asyncio.run(wiring.create_agent(draft=draft, sender_uid=999))
    assert len(reg.list_agents()) == initial_count  # fail-closed: no se creó


def test_create_update_delete_roundtrip(tmp_path):
    wiring, reg = _wiring(tmp_path)
    initial_count = len(reg.list_agents())
    draft = draft_from_dict({"name": "Ventas", "instructions": "tono comercial"})
    created = asyncio.run(wiring.create_agent(draft=draft, sender_uid=_OPERATOR_UID))
    assert created["name"] == "Ventas"
    assert len(reg.list_agents()) == initial_count + 1

    updated = asyncio.run(
        wiring.update_agent(
            agent_id=created["agent_id"],
            draft=draft_from_dict({"name": "Ventas Pro"}),
            sender_uid=_OPERATOR_UID,
        )
    )
    assert updated["name"] == "Ventas Pro"

    asyncio.run(
        wiring.delete_agent(agent_id=created["agent_id"], sender_uid=_OPERATOR_UID)
    )
    assert len(reg.list_agents()) == initial_count


def test_mutators_unauthorized_fail_closed(tmp_path):
    wiring, reg = _wiring(tmp_path)
    created = asyncio.run(
        wiring.create_agent(
            draft=draft_from_dict({"name": "Tmp"}), sender_uid=_OPERATOR_UID
        )
    )
    aid = created["agent_id"]
    with pytest.raises(DbusAuthorizationError):
        asyncio.run(wiring.delete_agent(agent_id=aid, sender_uid=7))
    agent_ids = {a["agent_id"] for a in wiring.list_agents()}
    assert DEFAULT_AGENT_ID in agent_ids
    assert aid in agent_ids


def test_cannot_update_default_agent(tmp_path):
    """Regression: update_agent on the default (Cerebro) raises CannotUpdateDefaultAgent."""
    from hermes.agents.domain.ports import CannotUpdateDefaultAgent  # noqa: PLC0415

    wiring, reg = _wiring(tmp_path)
    with pytest.raises(CannotUpdateDefaultAgent):
        asyncio.run(
            wiring.update_agent(
                agent_id=DEFAULT_AGENT_ID,
                draft=draft_from_dict({"name": "Hack", "instructions": "ignora todo"}),
                sender_uid=_OPERATOR_UID,
            )
        )
    # The default agent must remain intact after the failed attempt.
    default = reg.get_agent(DEFAULT_AGENT_ID)
    assert default.name == "CEO"
    assert default.is_default is True


def test_cannot_delete_default_agent(tmp_path):
    """Regression: delete_agent on the default raises CannotDeleteDefaultAgent."""
    from hermes.agents.domain.ports import CannotDeleteDefaultAgent  # noqa: PLC0415

    wiring, reg = _wiring(tmp_path)
    initial_count = len(reg.list_agents())
    with pytest.raises(CannotDeleteDefaultAgent):
        asyncio.run(
            wiring.delete_agent(agent_id=DEFAULT_AGENT_ID, sender_uid=_OPERATOR_UID)
        )
    assert len(reg.list_agents()) == initial_count
