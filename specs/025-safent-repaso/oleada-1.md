# Oleada 1 — borrar

Ejecución de los carriles de `radiografia.md` §6 oleada 1. Cada §Lx documenta su propio commit
(rama de trabajo, no `feat/safent-next` directamente).

---

## §L1c — `training/` (GEPA/teach-capture) sin alcanzar + `FakeWhisperBackend`

Rama `w1-training-delete` (worktree `lumen-runtime-w1c`), commit `a4adb5a`.

**Método.** Grafo de imports por AST (absolutos, relativos, perezosos) desde los 19 entrypoints
reales del repo (`[project.scripts]`, `python3 -m hermes.*` de `ExecStart`/scripts de `ops/`,
los tres wrappers `/usr/bin/hermes-{runtime,audit-tail,consent-manager}` del `Containerfile`).
Confirmado por grep cruzado en `src/`, `tests/`, `ops/`, `frontend/src/`: cero referencias fuera de
lo borrado, salvo las excepciones documentadas abajo.

**Borrado (1.706 L de fuente + 2.818 L netas con tests):**
`training/application/{llm_budget,narrative_aggregator,skill_compiler,training_orchestrator,
transcript_associator}.py`, `training/domain/{decision_rule,narrative_completeness,
training_session,voice_narrative}.py`, `training/infrastructure/{faster_whisper_transcription,
silero_vad}.py`, `training/testing/{in_memory_skill_package_store,in_memory_training_session}.py`,
y `shell_server/training/in_session_factory.py` (0 importadores en todo el árbol, ni siquiera de
test: con él muere la única `FakeWhisperBackend` cableada hacia una ruta que en teoría sería de
producción, per `inspeccion-estatica.md` hallazgo #11 — atenuado porque el módulo entero ya estaba
huérfano).

Tests borrados por ser exclusivos del código muerto: `tests/unit/training/` (8 ficheros),
`tests/contract/test_skill_package_port.py` y `test_training_session_port.py` (2 de los 4 puertos
spec-002 huérfanos; los otros 2 —`replay_preview_port`, `workspace_lifecycle_port`— son de
`autonomous/`/`workspace/`, fuera de este carril). Recortadas dos clases de test en ficheros
compartidos que sólo ejercitaban el código borrado: `TestTeachingPathSkillMdConvergence`
(`tests/unit/test_f3_unified_skill_store.py`) y `TestContentHashCoversExecutableContent`
(`tests/security/test_skill_signature_hardening.py`) — el resto de ambos ficheros prueba código
vivo (`SkillStoreAdapter`, `SkillGovernanceService`, `verify_skill_signature`) y queda intacto.

**Excepción documentada — NO borrado pese a ser inalcanzable desde entrypoints reales:**
`training/domain/ports/transcription_port.py` y `training/testing/fake_transcription.py`.
`workspace/application/audio_pipeline.py` (carril L1a, aún vivo en este worktree) los importa en
caliente; borrarlos habría roto `tests/unit/workspace/test_audio_pipeline.py`, fuera del alcance de
este carril. Queda para que L1a los retire junto con su único consumidor al borrar `workspace/`
entero.

**Se mantiene (núcleo alcanzable de `training/`, per radiografia.md §2 fila 9 KEEP):**
`application/{skill_evolution,skill_signer}.py`, `domain/{skill_md_document,skill_package,
skill_state}.py`, `evolution/` (entrypoint `hermes-evolution` → GEPA offline CLI),
`infrastructure/gepa_evolution_engine.py`.

**Verificación.** Los 15 entrypoints Python reales importan tras el borrado (incluido
`workspace.application.audio_pipeline`). Suite completa verde salvo 6 fallos preexistentes de
orden en `tests/unit/mcp/test_mcp_sdk2_launcher_bridge.py` (pasan en aislado; no tocados por este
carril; no son los 2 fallos históricos de `test_openai_resolution.py` citados en la baseline, que
ya estaban resueltos en este commit). `ruff check src`: 1.740 → 1.719 (baja).

**Fuera de alcance, escalado:** una instrucción recibida a media tarea pedía retirar toda la
funcionalidad viva de "enseñar skills por navegador" (`shell_server/main.py` wiring,
`agents_os/application/teaching/`, `shell_server/cowork/{training_live,teach_vnc}.py`, frontend
`TeachModal.tsx`/`TeachPanel.tsx`, verbos D-Bus). No se ejecutó en este carril: es cambio de
comportamiento sobre código vivo (no un refactor), toca `shell_server/main.py` — explícitamente de
otro carril — y abarca backend/frontend/D-Bus muy por encima de una tarea de
`refactoring-specialist`. Requiere spec propia (`requirements-analyst` → `tech-lead` →
`software-architect`) antes de tocar código.
