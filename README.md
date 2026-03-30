# PRAGtico — Portal de Coordenação Portuária

Sistema web para coordenação portuária no Porto de Setúbal, com gestão de escalas e manobras, dashboard operacional, chatbot RAG e motor de estimativa de custos de pilotagem.

## Funcionalidades

### Quadro operacional

- Dashboard com chegadas previstas, navios em porto, saídas recentes e arquivo operacional
- Janela contínua de marés, meteorologia horária e mapa AIS embebido
- Avisos locais, leitura costeira e contexto operacional agregado

### Escalas e manobras

- Registo completo de escalas com dados do navio
- Ciclo operacional por manobra: planeamento, aprovação, registo e arquivo
- Histórico de alterações, notas operacionais e exportação CSV

### Custos

- Fórmula de pilotagem baseada em `UP × √GT`
- Cálculo de entrada, saída, mudança, horas à ordem, cancelamentos e TUP
- API dedicada em `/api/cost/estimate` e `/api/cost/quick`

### Chatbot RAG

- Pesquisa documental com contexto vivo do portal
- Ações operacionais via chat com confirmação, cancelamento e feedback
- Suporte multi-provider para geração LLM
- Embeddings locais em desenvolvimento com `BAAI/bge-m3`
- Perfil Railway com embeddings via API, sem `sentence-transformers` no container

### Segurança e perfis

- Perfis `admin`, `agente` e `piloto`
- Password hashing com `scrypt`
- CSRF, rate limiting e headers de segurança
- Sessão com timeout idle configurável (`SESSION_IDLE_MINUTES`, por omissão `45`)

## Estrutura

```text
app.py
blueprints/
core/
domain/
integrations/
storage/
templates/
static/
docs/
scripts/
sql/
tests/
knowledge/
data/
```

Resumo por pasta:

- `blueprints/`: camada web Flask
- `core/`: segurança, validação, helpers e estado partilhado
- `domain/`: regras de negócio e parsing documental
- `integrations/`: LLM, RAG, AIS, marés, meteo, ondulação e avisos locais
- `storage/`: persistência local/PostgreSQL

Detalhe adicional em [Estrutura do projeto](docs/PROJECT_STRUCTURE.md).

## Arranque local

### Desenvolvimento

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 app.py
```

Abre `http://127.0.0.1:5000` ou a porta definida em `FLASK_PORT`.

`requirements.txt` mantém `sentence-transformers` para desenvolvimento local e reindexação com embeddings no teu PC.

### Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

O compose sobe a app e uma base PostgreSQL local.

## Railway

O repositório já inclui `Dockerfile`, `Procfile` e `railway.toml`.

O `Dockerfile` instala `requirements-prod.txt` por defeito, para o deploy Railway ficar abaixo do limite de imagem. O `docker compose` local continua a usar `requirements.txt`.

O guia completo está em [Deploy no Railway](docs/RAILWAY_DEPLOY.md).

Variáveis mínimas para produção:

```bash
FLASK_ENV=production
FLASK_SECRET_KEY=<chave-segura>
APP_STORAGE_BACKEND=postgres
RAG_INDEX_BACKEND=pgvector
DATABASE_URL=<ligação PostgreSQL>
OPENROUTER_API_KEY=<api-key>
LLM_PROVIDER=openrouter
LLM_MODEL=openrouter/free
EMBEDDING_PROVIDER=openrouter
EMBEDDING_MODEL=nvidia/llama-nemotron-embed-vl-1b-v2:free
EMBEDDING_LOCAL_ENABLED=0
```

No primeiro deploy com documentos novos, podes ativar `RAG_REINDEX_ON_START=1`; depois volta a `0`.

## Scripts úteis

Criar admin:

```bash
python3 scripts/create_admin.py admin@porto.pt password-segura
```

Alterar papel de utilizador:

```bash
python3 scripts/set_user_role.py utilizador@porto.pt admin
```

Com `make`:

```bash
make test
make create-admin EMAIL=admin@porto.pt PASSWORD=password-segura
```

## Testes

```bash
python3 -m unittest discover tests -v
```

A suite atual cobre 238 testes unitários e de integração.

## Documentação

- [Estrutura do projeto](docs/PROJECT_STRUCTURE.md)
- [Deploy no Railway](docs/RAILWAY_DEPLOY.md)
- [Comandos do bot operacional](docs/BOT_COMMANDS.md)
- [Alternativas ao LLM atual](docs/LLM_ALTERNATIVES.md)
  Nota: este documento é exploratório; valida preços e catálogos antes de decisões de compra.

## Tecnologias

- Backend: Flask, gunicorn, psycopg, PostgreSQL
- RAG: retrieval local ou `pgvector`
- LLM: Gemini / OpenRouter, com abstração de provider
- Embeddings: `sentence-transformers` local em desenvolvimento ou provider API em produção
- Frontend: Jinja2, HTML, CSS, JavaScript vanilla
- Deploy: Docker e Railway
