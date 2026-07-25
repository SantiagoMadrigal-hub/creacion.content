#!/usr/bin/env bash
# Guía la rotación de las API keys de LAVOX (Groq, Pexels, OpenAI) tras una
# exposición. No rota las keys por ti (eso se hace en el dashboard de cada
# proveedor); te asegura no olvidar ningún paso ni dejar la key vieja en uso.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "=== Rotación de API keys de LAVOX ==="
echo
echo "1. Groq:   https://console.groq.com/keys       -> revoca la key vieja, genera una nueva"
echo "2. Pexels: https://www.pexels.com/api/          -> revoca/regenera tu key"
echo "3. OpenAI: https://platform.openai.com/api-keys -> revoca la key vieja, genera una nueva"
echo

read -rp "¿Ya rotaste las 3 keys en sus dashboards? [y/N] " confirmado
if [[ "${confirmado}" != "y" && "${confirmado}" != "Y" ]]; then
    echo "Rota las keys primero en los dashboards de cada proveedor, luego vuelve a correr este script."
    exit 1
fi

ENV_FILE="${1:-.env}"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "No existe ${ENV_FILE}; copiando desde .env.example..."
    cp .env.example "${ENV_FILE}"
fi

echo
echo "Abriendo ${ENV_FILE} para que pegues las nuevas keys (editor: \${EDITOR:-nano})..."
"${EDITOR:-nano}" "${ENV_FILE}"

echo
echo "Verificando que ${ENV_FILE} esté ignorado por git..."
if git -C "$(pwd)" check-ignore -q "${ENV_FILE}" 2>/dev/null; then
    echo "OK: ${ENV_FILE} está en .gitignore."
else
    echo "ADVERTENCIA: ${ENV_FILE} NO parece estar ignorado por git. Revisa .gitignore antes de commitear."
fi

echo
echo "Si el config.py viejo con las keys hardcodeadas llegó a commitearse alguna"
echo "vez, las keys quedan en el historial de git aunque el archivo ya no exista."
echo "Considera reescribir el historial (git filter-repo o BFG Repo-Cleaner)"
echo "ademas de haberlas rotado."
