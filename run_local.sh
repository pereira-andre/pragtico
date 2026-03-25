#!/usr/bin/env bash
# ─────────────────────────────────────────────────
# PRAGtico — arranque local para desenvolvimento
# ─────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Cores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "${GREEN}  PRAGtico — Arranque Local${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"

# 1) Verificar Python
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}Python3 não encontrado. Instala antes de continuar.${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Python3: $(python3 --version)"

# 2) Criar venv se não existir
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}→ A criar ambiente virtual .venv ...${NC}"
    python3 -m venv .venv
fi
source .venv/bin/activate
echo -e "${GREEN}✓${NC} Ambiente virtual activado"

# 3) Instalar dependências
echo -e "${YELLOW}→ A instalar dependências ...${NC}"
pip install -q -r requirements.txt 2>&1 | tail -3
echo -e "${GREEN}✓${NC} Dependências instaladas"

# 3b) Verificar sentence-transformers (embeddings locais)
if python3 -c "import sentence_transformers" 2>/dev/null; then
    echo -e "${GREEN}✓${NC} Embeddings locais disponíveis (sentence-transformers)"
else
    echo -e "${YELLOW}⚠  sentence-transformers não instalado — embeddings via API (gasta quota)${NC}"
    echo -e "${YELLOW}   pip install sentence-transformers${NC}"
fi

# 4) Copiar .env se não existir
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${YELLOW}→ .env criado a partir de .env.example${NC}"
        echo -e "${YELLOW}  Edita o .env com as tuas API keys antes de continuar.${NC}"
    else
        echo -e "${RED}✗ Não encontro .env nem .env.example${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}✓${NC} .env presente"

# 5) Criar directórios necessários
mkdir -p data knowledge
echo -e "${GREEN}✓${NC} Directórios data/ e knowledge/ prontos"

# 6) Criar admin se não existir
echo -e "${YELLOW}→ A verificar conta admin ...${NC}"
python3 scripts/seed_admin.py 2>&1
echo -e "${GREEN}✓${NC} Admin verificado"

# 7) Verificar API keys
source .env 2>/dev/null || true
if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -z "${GEMINI_API_KEY:-}" ]; then
    echo -e "${YELLOW}⚠  Sem API key LLM definida no .env${NC}"
    echo -e "${YELLOW}   Define OPENROUTER_API_KEY ou GEMINI_API_KEY${NC}"
    echo -e "${YELLOW}   O site arranca mas o chatbot não funciona.${NC}"
else
    if [ -n "${OPENROUTER_API_KEY:-}" ]; then
        echo -e "${GREEN}✓${NC} OpenRouter API key configurada"
    fi
    if [ -n "${GEMINI_API_KEY:-}" ]; then
        echo -e "${GREEN}✓${NC} Gemini API key configurada"
    fi
fi

# 7) Arrancar
echo ""
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "${GREEN}  A arrancar em http://127.0.0.1:5000${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo ""

export FLASK_HOST="${FLASK_HOST:-127.0.0.1}"
export FLASK_PORT="${FLASK_PORT:-5000}"
export FLASK_DEBUG="${FLASK_DEBUG:-1}"
export FLASK_ENV="${FLASK_ENV:-development}"
export APP_STORAGE_BACKEND="${APP_STORAGE_BACKEND:-json}"
export RAG_INDEX_BACKEND="${RAG_INDEX_BACKEND:-json}"

python3 app.py
