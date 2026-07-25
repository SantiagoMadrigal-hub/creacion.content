#!/usr/bin/env bash
# Corre el pipeline completo localmente con uv (sin Docker).
# Uso: scripts/run_local.sh [argumentos para `lavox-pipeline run`]
# Ejemplo: scripts/run_local.sh --guion guion.txt --dry-run
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -f .env ]]; then
    echo "No existe .env. Copiando .env.example -> .env."
    echo "Completa tus API keys en .env antes de continuar (sin --dry-run)."
    cp .env.example .env
fi

echo "Sincronizando dependencias con uv..."
uv sync --quiet

echo "Ejecutando: lavox-pipeline run $*"
uv run lavox-pipeline run "$@"
