"""HeuristicFallbackScanner — CVE slot placeholder when trivy is unavailable.

Emits a single MEDIUM notice under category="cve" so the scoring engine
deducts the cve-weight fraction (preventing a false 100-score when no CVE
data was collected) and so the UI can show honest provenance.

IMPORTANT: this scanner is ALWAYS paired with engine='heuristic' in the
ScanService composition.  ScanService._compose_score() treats CRITICAL from
category='cve' with engine='heuristic' as a WARN cap (≤ 55), not a hard FAIL,
because the finding means "trivy absent" not "known CVE detected".  This
prevents a legitimate install from being permanently blocked solely because
the trivy binary was not available at build time.
"""

from __future__ import annotations

import logging

from hermes.security_center.domain.install_target import InstallTarget
from hermes.security_center.domain.scan_score import Risk, Severity

logger = logging.getLogger("hermes.security_center.heuristic_fallback")


class HeuristicFallbackScanner:
    """Injected in place of TriviaCveScanner when the trivy binary is absent.

    Emits a single MEDIUM risk (not CRITICAL) so the score is nudged
    below the 100 ceiling but the heuristic engine's cap rule in
    _compose_score handles the final verdict.  The message is explicit
    about the fallback so it surfaces in the UI's risk list.
    """

    name = "cve"

    async def scan(self, target: InstallTarget) -> list[Risk]:  # noqa: ARG002
        logger.warning(
            "hermes.security.trivy_unavailable kind=%s identifier=%s — "
            "CVE scan skipped, using heuristic fallback",
            target.kind, target.identifier,
        )
        return [Risk(
            category="cve",
            severity=Severity.MEDIUM,
            message=(
                "Escaneo CVE no disponible (trivy ausente) — "
                "revisión básica (heurística), no es un análisis completo de vulnerabilidades. "
                "El dueño puede revisar y aprobar."
            ),
            evidence_ref="cve:heuristic_fallback",
        )]
