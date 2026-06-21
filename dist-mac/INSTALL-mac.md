# Install Lumen on macOS (Apple Silicon)

Lumen is a **Docker/OCI container** (systemd PID1 + the kernel cage). On macOS it
runs inside a **podman machine** (a tiny Linux VM) — podman supports the systemd +
capabilities + seccomp the cage needs.

## Prerequisites
- macOS on **Apple Silicon** (arm64). The published image is arm64.
- **Homebrew** (https://brew.sh). The installer installs podman via brew if missing.
- Access to the private image. Log in once:
  ```sh
  podman login ghcr.io -u <your-github-user>      # paste a GitHub token with read:packages
  ```

## Install + run (one command)
```sh
git clone https://github.com/devwspito/lumen-runtime.git
cd lumen-runtime
./dist-mac/install-lumen-mac.sh
```
It will: install/start a podman machine (4 CPU / 8 GB), pull the image, run Lumen with
the correct flags, wait for boot, and print a ready-to-open URL **with the auth token**:
```
http://localhost:17517/?k=<bootstrap-token>
```
Open that URL — that's Lumen. (The `?k=` token authorizes the UI's actions; without it,
config/install buttons return 401.)

## First steps in the UI
1. **Configure your model** (the "Configura un modelo" button → "Añadir modelo propio"):
   your OpenAI-compatible endpoint (base URL + model + API key). Save & activate.
2. **MCP → ruflo** is pre-installed and connects out of the box (302 tools); its LLM
   auto-wires to the model you just configured. Ask the agent to "use ruflo to plan a
   project" → it discovers + invokes ruflo (you approve the HITL card).
3. **Integraciones → Composio**: paste your Composio API key → connect → 250+ apps.
4. **Skills**: search the hub and install (each install is scanned by the Security Center).

## Manage
```sh
podman logs -f lumen        # daemon logs
podman stop lumen           # stop
podman start lumen          # start again
podman rm -f lumen          # remove (state persists in the 'lumen-data' volume)
```

## Notes
- Do **NOT** run with `--cap-drop ALL` or container-wide `--security-opt no-new-privileges`
  — systemd PID1 needs SETUID/SETGID, and the hardened units set NoNewPrivileges per-unit.
  The installer uses the correct flags (see `ops/container/run-lumen.sh`).
- State (your model key, conversations, installed MCPs/skills) lives in the `lumen-data`
  podman volume and survives `podman rm` + image updates.
