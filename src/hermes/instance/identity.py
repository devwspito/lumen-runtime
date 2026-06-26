"""Instance identity — per-installation identifiers.

resolve_instance_id(db_path) returns a random UUID generated once and
persisted in a dedicated 0600 SQLite table on the same DB as the pairing.
On subsequent calls the same UUID is returned.  Machine-id / hostname are
used as a last-resort fallback ONLY when the DB path is not provided (e.g.
in tests that do not care about persistence), but they are NOT the primary
source.

hardware_fingerprint() returns a SHA-256 hex digest of host signals.  It is
used as a hardware-binding input in the HMAC proof so the proof cannot be
replayed on a different machine.  It is NOT the anti-replay mechanism —
the real anti-replay is the nonce (random per-challenge) combined with the
one-time pairing code enforced server-side.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from pathlib import Path

_INSTANCE_ID_TABLE = """
CREATE TABLE IF NOT EXISTS instance_identity (
  id          INTEGER PRIMARY KEY CHECK(id = 1),
  instance_id TEXT    NOT NULL
);
"""


def _read_machine_id() -> str:
    """Read /etc/machine-id (Linux) if available, else fall back to hostname."""
    machine_id_path = Path("/etc/machine-id")
    try:
        text = machine_id_path.read_text(encoding="ascii").strip()
        if text:
            return text
    except OSError:
        pass
    return _hostname()


def _hostname() -> str:
    if hasattr(os, "uname"):
        return os.uname().nodename
    return "hermes-local"


def _generate_random_instance_id() -> str:
    """Generate a fresh random UUID4 string for this installation."""
    import uuid  # noqa: PLC0415

    return str(uuid.UUID(bytes=secrets.token_bytes(16), version=4))


def resolve_instance_id(db_path: Path | None = None) -> str:
    """Return the stable random instance_id for this installation.

    Behaviour:
      - If db_path is given: read the id from the instance_identity table.
        If not yet generated, produce a random UUID, persist it, and return it.
        The DB file is created with mode 0600 before the first write.
      - If db_path is None (tests / environments without a DB): generate a
        deterministic fallback from machine-id/hostname (non-random, used only
        when persistence is not available).

    The random instance_id is preferred over machine-id because:
      1. It is not guessable (128-bit random vs. public hostname).
      2. It is stable across container recreates as long as the volume persists.
      3. It does not leak host topology information to the cloud.
    """
    if db_path is None:
        return _fallback_instance_id()
    return _load_or_create_instance_id(db_path)


def _load_or_create_instance_id(db_path: Path) -> str:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure file is created with 0600 before SQLite writes anything.
    if not db_path.exists():
        db_path.touch(mode=0o600)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_INSTANCE_ID_TABLE)
        row = conn.execute(
            "SELECT instance_id FROM instance_identity WHERE id = 1"
        ).fetchone()
        if row is not None:
            return row["instance_id"]
        new_id = _generate_random_instance_id()
        conn.execute(
            "INSERT INTO instance_identity (id, instance_id) VALUES (1, ?)",
            (new_id,),
        )
        return new_id
    finally:
        conn.close()


def _fallback_instance_id() -> str:
    """Deterministic fallback from machine-id/hostname (no persistence available).

    Used in tests and environments without a writable DB path.
    Uses the same SHA-256-truncation approach as _resolve_tenant_id() in the
    runtime daemon for consistency.
    """
    from uuid import UUID  # noqa: PLC0415

    seed = _read_machine_id()
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x50   # version 5
    raw[8] = (raw[8] & 0x3F) | 0x80   # variant bits
    return str(UUID(bytes=bytes(raw)))


def hardware_fingerprint() -> str:
    """Return a stable SHA-256 hex digest of host signals (no root required).

    Signals used (in order of availability):
      1. /etc/machine-id — Linux persistent machine identity
      2. hostname — always available

    Both are concatenated before hashing so the fingerprint changes if
    either changes (e.g. after a hostname rename in the same container).

    This is the hardware-binding input to the HMAC proof.  Anti-replay is
    provided by the random nonce (per-challenge) and the one-time pairing
    code (enforced server-side), NOT by this fingerprint alone.
    """
    parts = [_read_machine_id(), _hostname()]
    payload = ":".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
