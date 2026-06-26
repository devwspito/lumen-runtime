"""hermes.config_sync — remote policy sync (cloud → associate).

Cloud publishes a signed PolicyBundle per employee.  The associate pulls it
periodically, verifies the Ed25519 signature, and reconciles local state
by calling the existing D-Bus verbs on the runtime daemon.

Public surface (Fase 4):
    policy_document  — Pydantic bundle + canonical serialisation
    signature        — Ed25519 verify (fail-closed)
    applier          — PolicyApplier (declarative reconcile via D-Bus proxy)
    __main__         — sync loop (python -m hermes.config_sync)
"""
