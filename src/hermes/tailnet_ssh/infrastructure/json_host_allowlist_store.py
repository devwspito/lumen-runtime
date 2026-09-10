"""JsonHostAllowlistStore — the owner's per-host SSH grant store.

Persists `/var/lib/hermes/tailscale/ssh-allowlist.json` (mirrors the
`{"domains": [...]}` shape `shell_server/egress_api.py` uses for the owner's
egress grants — same convention, own file, own bounded context: this store
is tailnet_ssh's alone, never shared with the egress plane).

Stateless helpers over one JSON file: cheap enough to construct fresh per
call (no in-memory cache to go stale across daemon restarts or concurrent
callers).
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_ALLOWLIST_PATH = Path("/var/lib/hermes/tailscale/ssh-allowlist.json")


class JsonHostAllowlistStore:
    def __init__(self, allowlist_path: Path = DEFAULT_ALLOWLIST_PATH) -> None:
        self._path = allowlist_path

    def is_allowed(self, host: str) -> bool:
        return host.lower() in self._load()

    def allow(self, host: str) -> None:
        hosts = self._load()
        normalized = host.lower()
        if normalized in hosts:
            return
        hosts.add(normalized)
        self._save(hosts)

    def list_allowed(self) -> frozenset[str]:
        return frozenset(self._load())

    def revoke(self, host: str) -> None:
        hosts = self._load()
        hosts.discard(host.lower())
        self._save(hosts)

    def _load(self) -> set[str]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return {str(h).lower() for h in data.get("hosts", []) if h}
        except (OSError, json.JSONDecodeError):
            return set()

    def _save(self, hosts: set[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"hosts": sorted(hosts)}), encoding="utf-8")
