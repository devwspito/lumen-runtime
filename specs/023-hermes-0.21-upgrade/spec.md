# 023 — hermes-agent engine upgrade 0.15.1 → 0.21.1 (v2026.9.7)

## Goal
Move the Nous engine baked into the Safent runtime from the untagged `main`
snapshot `475ecea3` (2026-06-03, pyproject 0.15.1) to the tagged release
`v2026.9.7` = commit `2237be355906fbe6065ce1815711eee52b2d646e` (0.21.1,
2026-09-07), with **zero change to Safent's governance posture**: every native
tool call still crosses the `pre_tool_call`/`post_tool_call` hooks, the caged
exec chokepoint, the broker for external tools, and HITL block-and-resume.

## Scope
- `ops/container/Containerfile` (`HERMES_AGENT_COMMIT`, `mcp`/`pydantic` pins,
  import smoke), `pyproject.toml` (`hermes-agent==0.21.1`).
- Surgical adapter fixes in `src/hermes/runtime/nous_engine.py` and
  `src/hermes/agents_os/infrastructure/dbus_runtime_service.py` for symbols
  that moved/changed between the two trees (compat table in the report).
- No refactors, no new features, no upstream-feature adoption.

## Risks
1. **Tool executor contract** — 0.21 calls `agent._invoke_tool(...)` with three
   new kwargs; a wrapper that rejects them breaks every concurrent tool call.
2. **Plugin hook timeout** — 0.21 bounds `pre_tool_call` callbacks at
   `plugins.hook_callback_timeout` (30 s) and fails closed; Safent's HITL gate
   blocks up to 30 min → must run unbounded (`0`) or approvals auto-deny.
3. **Unlimited iterations** — `AIAgent.max_iterations` default became
   `sys.maxsize` (was 90); keep an explicit ceiling.
4. **Module decomposition** — `hermes_cli.auth`, `tools.mcp_tool`,
   `tools.skills_hub`, `tools.approval` were split; most names survive through
   PEP 562 compat maps, `_load_mcp_config` does not.
5. **Dependency drift** — MCP SDK 1.26 → 2.0 (needs pydantic ≥ 2.12; our
   last-word `pydantic==2.10.6` pin must move to upstream's `2.13.4`), new
   core deps (`nemo-relay`, `firecrawl-anydoc`, `Pillow`) on aarch64.
6. **Removed upstream features** we bridged: xAI loopback-PKCE OAuth helpers
   (replaced upstream by device-code), toolsets `messaging`/`moa`.
7. Config schema `_config_version` 26 → 41 (auto-migration floor is 12).

## Verification plan
1. Static: AST diff of every imported upstream symbol + signature (old vs new
   tarball) → compatibility table; fix only what is broken.
2. Host unit suite (`tests/unit`, hermes-agent not installed) must not regress
   (baseline ≈ 4119 passing); add regression tests for each adapter fix.
3. Local image build `localhost/safent-runtime:hermes021` (aarch64, no push);
   builder-stage import smoke must pass.
4. Boot smoke as `h021-smoke` with a fresh volume: daemon up, version reported,
   engine import OK, `/api/v1/profile` answers, journal free of tracebacks.
5. Chat smoke only if a provider key is available; otherwise document the gap.
6. VERSION bump only after 3 and 4 succeed.
