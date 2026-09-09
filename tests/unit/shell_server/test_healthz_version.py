"""/healthz must report the real running hermes.__version__, not a stale literal.

Regression: the payload hardcoded "version": "0.4.0" — frozen since an early
build — while hermes.__version__ (the single source of truth synced from the
repo-root VERSION file by ops/container/build.sh) moved on to 0.8.x. Anyone
polling /healthz during a boot smoke (see ops/container/build.sh step 4 and
specs/023-hermes-0.21-upgrade/spec.md's boot-smoke checklist) got a version
string that never matched the image actually running.
"""

from __future__ import annotations

import pytest

from hermes import __version__ as HERMES_VERSION
from hermes.shell_server.main import _healthz_payload

pytestmark = pytest.mark.unit


class TestHealthzVersion:
    def test_reports_the_real_running_version(self) -> None:
        payload = _healthz_payload()
        assert payload["version"] == HERMES_VERSION

    def test_does_not_hardcode_the_stale_040_literal(self) -> None:
        payload = _healthz_payload()
        assert payload["version"] != "0.4.0"

    def test_shape_is_unchanged(self) -> None:
        payload = _healthz_payload()
        assert payload["status"] == "ok"
        assert payload["service"] == "hermes-shell-server"
        assert "ts" in payload
