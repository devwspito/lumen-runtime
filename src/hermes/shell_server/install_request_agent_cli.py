"""install_request_agent_cli — the host agent's own consumer of the
install-request marker (contracts/install-request.md, T016).

Usage (inside the container, invoked by `safent agent` / `safent companion
install|repair` on the HOST):
  python3 -m hermes.shell_server.install_request_agent_cli claim <verb> --claimant X
  python3 -m hermes.shell_server.install_request_agent_cli resolve <verb> --success|--failure

Why a CLI, not raw shell against the marker file
--------------------------------------------------
`hermes.shell_server.install_requests` already owns the marker's format,
TTL-per-verb and claim/expiry rules (T006) — the closed vocabulary the
Constitution Principle 0 condition in plan.md holds this module to. The
host-side `safent` CLI has no Python runtime of its own and must never
re-implement JSON/expiry/claim parsing in POSIX sh (fragile, untested,
exactly the kind of second implementation `install-request.md` §4 warns
against: "misma implementacion" for both readers). This thin wrapper is the
SECOND reader (the host agent) reusing the ONE implementation, the same way
`brake_release_cli.py` reuses the daemon's D-Bus surface instead of a
shell-side re-implementation of Resume().

`claim` prints the claimed slug (or an empty line if the verb carries none)
to stdout and exits 0 on success; exits 1 with no output when there is
nothing live to claim or someone else holds a live claim — the shell caller
treats either as "nothing to do right now", never an error.
"""

from __future__ import annotations

import argparse
import sys


def cmd_claim(verb: str, claimant: str) -> int:
    from hermes.shell_server.install_requests import VERBS, claim_request  # noqa: PLC0415

    if verb not in VERBS:
        print(f"unknown verb: {verb}", file=sys.stderr)
        return 2
    claimed = claim_request(verb, claimant=claimant)
    if claimed is None:
        return 1
    print(claimed.slug or "")
    return 0


def cmd_resolve(verb: str, *, success: bool) -> int:
    from hermes.shell_server.install_requests import VERBS, resolve_request  # noqa: PLC0415

    if verb not in VERBS:
        print(f"unknown verb: {verb}", file=sys.stderr)
        return 2
    resolve_request(verb, success=success)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="install_request_agent_cli")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    claim_parser = sub.add_parser("claim", help="Claim a live install request.")
    claim_parser.add_argument("verb")
    claim_parser.add_argument("--claimant", required=True)

    resolve_parser = sub.add_parser("resolve", help="Release a claim; consume on success.")
    resolve_parser.add_argument("verb")
    outcome = resolve_parser.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--success", action="store_true")
    outcome.add_argument("--failure", action="store_true")

    args = parser.parse_args(argv)

    if args.subcommand == "claim":
        return cmd_claim(args.verb, args.claimant)
    return cmd_resolve(args.verb, success=args.success)


if __name__ == "__main__":
    sys.exit(main())
