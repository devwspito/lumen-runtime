"""Regresión: _coerce_skill_version NO debe crashear el listado /skills con versiones
reales del hub (semver "2.0.0"). El bug original: int("2.0.0") -> ValueError -> _skill_md_to_dto
caía -> NINGUNA skill aparecía (ni hub ni agente). Los tests de la Fase 3 MOCKEABAN la
versión (entero), así que nunca tocaron este camino -> "verde en test, roto en vivo"."""

from __future__ import annotations

import pytest

from hermes.agents_os.infrastructure.dbus_runtime_service import _coerce_skill_version

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2.0.0", 2),      # semver del hub (el que crasheaba)
        ("1.4.7", 1),
        (3, 3),            # entero (skills del agente / cage)
        ("5", 5),
        (None, 1),         # ausente -> default
        ("", 1),
        ("v2", 1),         # no parseable como int ni semver -> default seguro
        ("latest", 1),
    ],
)
def test_coerce_skill_version_never_crashes(raw, expected) -> None:
    assert _coerce_skill_version(raw) == expected
