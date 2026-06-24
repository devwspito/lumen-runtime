#!/bin/sh
# Lumen — instalador de una línea.
#
#   curl -fsSL https://raw.githubusercontent.com/devwspito/lumen-runtime/main/get-lumen.sh | sh
#
# Instala el comando `lumen` en tu PATH y arranca Lumen con la jaula de seguridad
# por defecto (loopback), abriendo el navegador en el token único de este arranque.
# Después, controla Lumen desde la terminal:
#   lumen          abrir          lumen stop     parar
#   lumen update   actualizar     lumen status   estado
# El modelo, Composio, Brave, agentes y skills se configuran EN LA UI.
set -eu

LUMEN_CLI_URL="${LUMEN_CLI_URL:-https://raw.githubusercontent.com/devwspito/lumen-runtime/main/lumen}"

command -v curl >/dev/null 2>&1 || { echo "✗ Necesitas curl."; exit 1; }

# Elegir un directorio de PATH escribible SIN sudo. Preferimos uno que ya esté en
# PATH; si ninguno lo está, caemos a ~/.local/bin y avisamos de cómo añadirlo.
BIN=""
for d in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin" "$HOME/bin"; do
  case ":$PATH:" in
    *":$d:"*) if mkdir -p "$d" 2>/dev/null && [ -w "$d" ]; then BIN="$d"; break; fi ;;
  esac
done
if [ -z "$BIN" ]; then
  BIN="$HOME/.local/bin"
  mkdir -p "$BIN" 2>/dev/null || { echo "✗ No pude crear $BIN."; exit 1; }
fi

echo "▸ Instalando el comando 'lumen' en $BIN…"
if ! curl -fsSL "$LUMEN_CLI_URL" -o "$BIN/lumen"; then
  echo "✗ No se pudo descargar el CLI ($LUMEN_CLI_URL)."
  echo "  Si el repo aún no es público, exporta LUMEN_CLI_URL a una URL accesible."
  exit 1
fi
chmod +x "$BIN/lumen"

case ":$PATH:" in
  *":$BIN:"*) : ;;
  *)
    echo "  ⚠ $BIN no está en tu PATH. Para usar 'lumen' directamente, añádelo:"
    echo "      echo 'export PATH=\"$BIN:\$PATH\"' >> ~/.zshrc  &&  source ~/.zshrc"
    ;;
esac

# Primer arranque: descarga la imagen, la corre con la jaula y abre el navegador.
# (Reenvía LUMEN_IMAGE / LUMEN_PORT / LUMEN_SECCOMP_URL si los exportaste.)
exec "$BIN/lumen" update
