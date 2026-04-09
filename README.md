<p align="center">
  <img src="img/icon.png" alt="PRAGtico Bot" width="180">
</p>

# PRAGtico — Portal de Coordenação Portuária

<p align="center"><strong>PRAGtico Bot</strong> junta quadro operacional, RAG documental e canal WhatsApp para apoiar a coordenação portuária no Porto de Setúbal.</p>

Sistema web para coordenação portuária no Porto de Setúbal, com gestão de escalas e manobras, dashboard operacional, chatbot RAG e motor de estimativa de custos de pilotagem.

## PRAGtico Bot

- Assistente operacional com respostas contextuais dentro do portal e por WhatsApp
- Consulta documentação, estado operacional e dados vivos para apoiar decisões rápidas
- Executa ações com confirmação, cancelamento e feedback no fluxo de trabalho diário

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

- Planner leve de execução para decidir primeiro que fontes consultar antes de responder
- Extração de estado conversacional para follow-ups de avaliação e recomendação
- Pesquisa documental com contexto vivo do portal
- Ações operacionais via chat com confirmação, cancelamento e feedback
- Prioridade a dados live para meteorologia, marés, ondulação e avisos, com síntese por LLM quando a pergunta exige raciocínio operacional
- Segunda passagem interna curta em perguntas de decisão quando a primeira resposta sai como simples dump de dados live
- Canal WhatsApp via Cloud API com webhook, respostas inbound e feedback por reação `👍/👎`
- Welcome automática no primeiro contacto WhatsApp, configurável por `.env`
- Suporte multi-provider para geração LLM
- Embeddings locais em desenvolvimento com `BAAI/bge-m3`
- Perfil Railway com `OpenAI` para chat e embeddings, com fallback de geração por `OpenRouter`, sem `sentence-transformers` no container

O CSV operacional de marés fica em `resources/tides/` e não em `knowledge/`, para não entrar na indexação documental do RAG.
As horas do CSV são assumidas em `UTC` e apresentadas no fuso operacional `Europe/Lisbon`, incluindo mudança de hora.
Companions estruturados podem ser guardados em `knowledge/companions/*.json` para FAQs canónicas e resumos operacionais por documento. Se não existir JSON, o portal tenta gerar um companion-base a partir do próprio texto do documento.

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
- `knowledge/companions/`: sidecars JSON opcionais com resumo e FAQ canónica por documento

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

A app guarda snapshots locais de ondulação e avisos em `data/wave_conditions_cache.json` e `data/local_warnings_cache.json`, com refresh periódico configurável por `WAVE_REFRESH_INTERVAL_SECONDS` e `LOCAL_WARNING_REFRESH_INTERVAL_SECONDS`.

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
Para a integração WhatsApp e geração do token Meta, ver também [Guia WhatsApp](readme_whatsapp.txt).

Variáveis mínimas para produção:

```bash
FLASK_ENV=production
FLASK_SECRET_KEY=<chave-segura>
APP_STORAGE_BACKEND=postgres
RAG_INDEX_BACKEND=pgvector
DATABASE_URL=<ligação PostgreSQL>
OPENAI_API_KEY=<api-key principal>
OPENROUTER_API_KEY=<api-key de fallback>
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-mini
LLM_FALLBACK_PROVIDER=openrouter
LLM_FALLBACK_MODEL=openai/gpt-4.1-mini
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_LOCAL_ENABLED=0
MANEUVER_CASE_CAPTURE_ENVIRONMENT=1
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

Gerar companion automático para um documento:

```bash
python3 scripts/generate_knowledge_companion.py IT-036_RegulacaoAgulhas.txt
```

Gerar ou refrescar companions para todos os documentos:

```bash
python3 scripts/generate_knowledge_companion.py --all --force
```

Teste simples de envio WhatsApp:

```bash
python3 scripts/test_whatsapp_send.py --to 351962063664
```

Pré-requisitos na `.env`: `WHATSAPP_ENABLED=1`, `WHATSAPP_ACCESS_TOKEN` e `WHATSAPP_PHONE_NUMBER_ID`.

Atualizar a foto do perfil do número WhatsApp com `img/icon.png`:

```bash
python3 scripts/update_whatsapp_profile.py --show-profile
```

Opcionalmente podes incluir `--about`, `--description` e até duas vezes `--website`.

Com `make`:

```bash
make test
make create-admin EMAIL=admin@porto.pt PASSWORD=password-segura
```

## Testes

```bash
python3 -m unittest discover tests -v
```

A suite cobre testes unitários e de integração para portal, storage, custos, WhatsApp, companions e fluxo conversacional.

## Documentação

- [Estrutura do projeto](docs/PROJECT_STRUCTURE.md)
- [Deploy no Railway](docs/RAILWAY_DEPLOY.md)
- [Guia WhatsApp](readme_whatsapp.txt)
- [Bot operacional](readme_bot.txt)
- [Base de dados](readme_database.txt)
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
