"""Loader for the system-wide malicious-domain blocklist.

The blocklist is a plain-text file with one domain per line. Lines starting with
``#`` or ``!`` are comments. Lines in /etc/hosts format (``0.0.0.0 evil.com``,
``127.0.0.1 evil.com``) are also handled — only the hostname part is extracted.

Default path: /usr/share/hermes/egress-blocklist.txt (baked into the container
image at build time from the hagezi "light" list).

Fail-soft: any read or parse error returns an empty set and logs a WARNING.
The proxy MUST NOT fail to start because the blocklist is missing or malformed.
The security posture degrades gracefully (no blocklist = no system-level blocking)
without affecting the rest of the policy engine.

TODO(refresh): add a systemd timer (hermes-egress-blocklist-refresh.timer) that
re-downloads the hagezi list daily and calls ``load_blocklist`` on the live engine
via a signal or a dedicated reload endpoint. For now the snapshot bundled at build
time is the blocklist in effect for the lifetime of the container.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("hermes.egress_proxy.blocklist")

_DEFAULT_PATH = Path("/usr/share/hermes/egress-blocklist.txt")

# Matches a leading IP address (0.0.0.0, 127.0.0.1, ::1) in hosts-file lines.
_HOSTS_IP_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}\s+|^::1\s+|^::\s+")

# A minimal domain-shaped sanity check — not strict, just filters garbage.
_DOMAIN_LIKE_RE = re.compile(r"^[a-z0-9]([a-z0-9.\-]{0,251}[a-z0-9])?$")


def load_blocklist_file(path: Path = _DEFAULT_PATH) -> set[str]:
    """Read a domain blocklist file and return the set of normalized hostnames.

    Returns an empty set if the file is missing, empty, or unreadable (fail-soft).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning(
            "hermes.egress_proxy.blocklist_load_skipped path=%s reason=%s",
            path,
            exc,
        )
        return set()

    domains: set[str] = set()
    skipped = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        # Strip leading IP address from hosts-file format lines.
        line = _HOSTS_IP_RE.sub("", line).strip()
        # Drop any trailing inline comment.
        line = line.split("#", 1)[0].strip()
        normalized = line.lower().rstrip(".")
        if not normalized or not _DOMAIN_LIKE_RE.match(normalized):
            skipped += 1
            continue
        # Skip localhost entries — they must never be blocked.
        if normalized in ("localhost", "localhost.localdomain"):
            continue
        domains.add(normalized)

    logger.info(
        "hermes.egress_proxy.blocklist_loaded path=%s entries=%d skipped=%d",
        path,
        len(domains),
        skipped,
    )
    return domains
