# PRAGtico — Portal de Coordenação Portuária

Sistema web de coordenação portuária para pilotagem no Porto de Setúbal,
com chatbot RAG, gestão de escalas e manobras, estimativa de custos e
monitorização operacional em tempo real.

## Funcionalidades

### Quadro Operacional
- Dashboard com visão geral: navios em porto, chegadas previstas, saídas recentes
- Meteorologia contínua (48h) com ícones de estado do tempo
- Mapa AIS embebido (VesselFinder) com coordenadas náuticas
- Navios por cais em ordem geográfica (Secil → Teporset)
- Planeamento operacional com tabela de marcações/validações/fechos

### Gestão de Escalas e Manobras
- Registo de escalas com ficha completa do navio (IMO, GT, LOA, etc.)
- Ciclo completo: registar → aprovar → concluir → registar pilotagem
- Manobras: entrada, saída, mudança de cais, fundeadouro
- Planeamento e edição de manobras com histórico de alterações
- Arquivo operacional tipo Excel com pesquisa e exportação CSV

### Motor de Cálculo de Custos
- Fórmula oficial de pilotagem: T = UP × GT (tarifário Setúbal 2024)
- UP normal: 9.2578 €/GT | mudança: 3.3628 €/GT
- Agravamentos (+25%): sem propulsão, assistência especial
- Reduções: -25% linha regular, -10% cabotagem, -30% escala técnica
- TUP estimada, pilotagem à ordem, cancelamentos
- Simulador interativo no arquivo de manobras
- API: `POST /api/cost/estimate` e `GET /api/cost/quick`

### Chatbot RAG
- Assistente com pesquisa semântica na base documental
- Widget flutuante disponível em todas as páginas
- Contexto operacional automático (escalas, manobras, custos, marés, meteo)
- Ações operacionais via chat (criar escalas, aprovar manobras)
- Feedback operacional (aprovar/rever respostas)
- Arquivo de conversas
- Respeita privilégios de cada perfil (admin, agente, piloto)

### Autenticação e Segurança
- 3 perfis: admin (único), agente de navegação, piloto
- Passwords com scrypt (Werkzeug)
- Session cookies: HTTPOnly, SameSite=Lax, Secure em produção
- Security headers: HSTS, X-Frame-Options, X-Content-Type-Options
- Agente vê apenas escalas da sua agência
- Sessão expira em 8 horas

### Base Documental
- Upload e indexação de documentos (PDF, DOCX, TXT, MD, CSV)
- Embeddings semânticos via Gemini
- Backend pgvector para pesquisa vetorial
- Reindexação incremental com progresso em tempo real
- Página admin dedicada para gestão documental

## Arquitetura

```
app.py              — Flask application (routes, controllers)
cost_engine.py      — Pilotage cost calculation engine
rag_engine.py       — RAG engine (embeddings, retrieval, generation)
chat_actions.py     — Chatbot operational actions (create, approve, etc.)
storage.py          — Data storage (JSON local + PostgreSQL)
auth_service.py     — Authentication service
weather_service.py  — WeatherAPI integration
tide_service.py     — Tide data from CSV
ais_service.py      — VesselFinder AIS embed
vector_store.py     — pgvector index store
knowledge/          — RAG knowledge base documents
templates/          — Jinja2 HTML templates
static/styles.css   — Professional dark nautical theme
```

## Como Correr

### Desenvolvimento local

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
# Editar .env com GEMINI_API_KEY, WEATHERAPI_KEY, etc.
python3 app.py
```

Abrir `http://127.0.0.1:5000`.

### Docker Compose (com PostgreSQL + pgvector)

```bash
cp .env.example .env
docker compose up --build
```

### Deploy no Railway

O projecto inclui `Procfile`, `railway.toml` e `Dockerfile` prontos.

Variáveis de ambiente necessárias no Railway:
- `FLASK_SECRET_KEY` — chave secreta (gerar com `python -c "import secrets; print(secrets.token_hex(32))"`)
- `DATABASE_URL` — PostgreSQL connection string
- `GEMINI_API_KEY` — Google Gemini API key
- `WEATHERAPI_KEY` — WeatherAPI.com key (opcional)
- `FLASK_ENV` — `production`

## Criar Conta Admin

```bash
python scripts/create_admin.py
```

Ou via Docker:
```bash
docker compose exec web python scripts/create_admin.py
```

## Alternativas ao Gemini

Ver `docs/LLM_ALTERNATIVES.md` para análise detalhada de custos e
opções de migração (DeepSeek, embeddings locais, multi-provider).

## Testes

```bash
python -m pytest tests/ -v
```

## Tecnologias

- **Backend:** Flask, gunicorn, PostgreSQL, pgvector
- **LLM:** Google Gemini (configurável)
- **Frontend:** HTML/CSS/JS vanilla, tema dark náutico
- **Tipografia:** DM Serif Display + DM Sans (Google Fonts)
- **Deploy:** Docker, Railway, Heroku-compatible
