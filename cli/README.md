# Lumen

Tu agente de IA personal, en tu máquina. **Un solo comando:**

```sh
npx @devwspito/lumen
```

Descarga la imagen endurecida de Lumen, la arranca con la jaula de seguridad por
defecto y abre el navegador. El **modelo, Composio, Brave, agentes y skills se
configuran en la UI** — sin ficheros de configuración ni secretos en disco.

## Requisitos
- **podman** (recomendado) o **docker**.
- En macOS, una `podman machine` **rootful**:
  ```sh
  podman machine init --rootful --cpus 4 --memory 8192 --disk-size 60
  podman machine start
  ```

## Qué hace
1. `pull` de `ghcr.io/devwspito/lumen:latest`.
2. La arranca con capacidades mínimas + seccomp + securityfs, publicada **solo en
   loopback** (`127.0.0.1`).
3. Abre `http://localhost:17517/?k=<token>` — el **token se mintea fresco en cada
   arranque, es único y nunca se persiste**.

## Opciones (variables de entorno)
- `LUMEN_PORT` (por defecto `17517`)
- `LUMEN_NAME` (por defecto `lumen`)
- `LUMEN_IMAGE` (por defecto `ghcr.io/devwspito/lumen:latest`)

MIT.
