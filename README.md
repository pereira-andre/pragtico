# PRAGtico

<p align="center">
  <img src="img/icon.png" alt="Mascote PRAGtico" width="220">
</p>

PRAGtico e uma plataforma operacional para planeamento, validacao e acompanhamento
de escalas e manobras no Porto de Setubal. Junta o portal web, o bot tecnico, a
base documental, o historico operacional, WhatsApp, backups, auditoria e testes
de qualidade num unico sistema.

## Estado atual

- Portal Flask com PostgreSQL e indice documental em pgvector.
- Gestao de utilizadores com roles `admin`, `piloto` e `agente`.
- Escalas, navios frequentes, manobras de entrada, saida e mudanca.
- Validacao operacional de manobras com checklist, mare, meteorologia, regras de
  cais, rebocadores e historico de casos.
- Bot tecnico no site e por WhatsApp, com comandos slash e respostas baseadas no
  conhecimento operacional.
- Base documental com instrucoes, regulamentos, COLREG/RIEAM, balizagem,
  rebocadores, fundeadouros, unidades nauticas, cultura local e praticas de
  manobra.
- Backups completos em ZIP com `backup.json` e `README.md`.
- Auditoria JSONL para acoes sensiveis, permissoes, backups, imports, wipe,
  configuracao e operacao.
- Catalogo de erros em `domain/error_catalog.py` e guia operacional em
  `docs/CATALOGO_ERROS_E_DEBUG.md`.

## Funcionalidades principais

### Portal operacional

- Dashboard com chegadas, navios em porto, fundeadouros, cais, planeamento,
  arquivo e meteorologia.
- Registo de escala com navio, ETA, cais previsto, ultimo porto, proximo destino,
  dimensoes, tipo de navio e restricoes.
- Planeamento e aprovacao de manobras:
  - entrada;
  - saida;
  - mudanca;
  - cancelamento/aborto;
  - registo executado com inicio, fim, calado e notas.
- Arquivo de escalas e manobras com exportacao e relatorios.
- Catalogo de navios frequentes com import/export.
- Estimativa de custos de pilotagem e TUP.

### Bot tecnico

O bot responde a perguntas operacionais e tambem consegue preparar acoes no
portal. Sem `/`, responde em modo consulta tecnica. Com `/`, entra em modo
comando e pode consultar ou preparar alteracoes de acordo com a role do
utilizador.

Comandos principais:

- `/help`
- `/planeamento`
- `/manobras-planeadas`
- `/manobras-previstas`
- `/consultar-escala REF`
- `/consultar-manobra ID`
- `/validar-manobra ID`
- `/registar-escala`
- `/criar-manobra`
- `/editar-manobra`
- `/aprovar`
- `/registar-manobra`
- `/abortar`
- `/regra 015`
- `/colreg-lista`
- `/colreg 19`
- `/mares hoje`
- `/meteorologia hoje`
- `/ondulacao`
- `/avisos-locais`
- `/reportar-evento TAG. LOCAL. DESCRICAO`

No WhatsApp, o modo SOS usa:

- `SOS`
- `CANCELAR SOS`

### Conhecimento operacional

A pasta `knowledge/` contem as fontes textuais e dados estruturados usados pelo
motor de consulta:

- instrucoes operacionais por cais e terminal;
- regras de entrada/saida, canal norte, fundeadouros e pilotagem;
- regras de rebocadores e posicionamento;
- perfis de cais em `knowledge/berth_profiles.json`;
- luzes e balizagem de Setubal;
- nota de sistema IALA A;
- RIEAM/COLREG;
- meteorologia, nevoeiro, emergencias e boas praticas;
- unidades nauticas, escala Beaufort, milhas, jardas e manilhas;
- notas praticas de shiphandling;
- historia e cultura local de Setubal.

Os ficheiros `knowledge/companions/*.json` complementam documentos criticos com
resumos, entidades, regras e casos de teste. Os ficheiros `knowledge/evals/*.json`
servem para validacao de respostas.

### Admin

Paginas administrativas principais:

- `/admin/status` - estado da plataforma, base, indice, integracoes e alertas.
- `/admin/documents` - documentos e reindexacao.
- `/admin/bot` - qualidade, ajustes, feedback e monitorizacao.
- `/admin/bot/monitor` - sinais do pipeline e excecoes.
- `/admin/casebooks` - casos, mensagens e experiencia pratica.
- `/admin/tests` - matriz de testes operacionais.
- `/admin/users` - utilizadores, roles e perfis.
- `/admin/backups` - backups, reposicao e wipe da base.
- `/admin/auditoria` - consulta e exportacao do audit log.
- `/admin/event-reports` - reportes operacionais vindos do portal/WhatsApp.

## Arquitetura

```text
app.py                  entrada Flask
blueprints/             rotas web, admin, chat, API, auth, WhatsApp
core/                   runtime, seguranca, bot, validacao e operacao
domain/                 regras de dominio, custos, COLREG, cais, erros
integrations/           providers externos, meteorologia, ondulacao, WhatsApp
storage/                persistencia PostgreSQL
knowledge/              base documental e dados operacionais
resources/tides/        tabela de mares
templates/              paginas Jinja
static/                 CSS, JS e assets
sql/                    schemas PostgreSQL e pgvector
tests/                  testes automatizados
docs/                   documentacao tecnica e auditorias
scripts/                auditorias e utilitarios locais
```

## Requisitos

- Python 3.11 em producao; Python 3.10+ funciona nos testes locais atuais.
- PostgreSQL com extensao `vector`.
- Dependencias Python em `requirements.txt`.
- Variaveis de ambiente configuradas.

Instalacao local:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Configuracao

Criar um `.env` local ou configurar as variaveis no ambiente de deploy. Nao
guardar segredos no Git.

### Essenciais

| Variavel | Uso |
|---|---|
| `DATABASE_URL` | Ligacao PostgreSQL. Obrigatoria para arrancar. |
| `FLASK_SECRET_KEY` | Chave de sessao Flask. Obrigatoria em producao. |
| `ADMIN_EMAIL` | Admin inicial. Default: `admin@porto.com`. |
| `ADMIN_PASSWORD` | Password inicial/admin seed. Default local: `123456`. |
| `ADMIN_PHONE` | Telefone opcional do admin principal. |
| `ADMIN_WHATSAPP_NUMBER` | WhatsApp do admin principal; se vazio usa `WHATSAPP_TEST_TO` ou o primeiro autorizado. |
| `FLASK_ENV` | Usar `production` em deploy. |
| `MAX_UPLOAD_MB` | Limite de upload. Default: `64`. |
| `SESSION_IDLE_MINUTES` | Tempo de sessao. Default: `45`. |

### Motor de resposta e indice

| Variavel | Uso |
|---|---|
| `LLM_PROVIDER` | Provider principal. Default: `gemini`. |
| `LLM_MODEL` | Modelo de resposta. |
| `LLM_FALLBACK_PROVIDER` | Provider alternativo, se configurado. |
| `LLM_FALLBACK_MODEL` | Modelo alternativo. |
| `LLM_API_KEY` | Chave para provider custom compativel. |
| `LLM_BASE_URL` | URL para provider custom compativel. |
| `GEMINI_API_KEY` | Chave quando `LLM_PROVIDER=gemini`. |
| `EMBEDDING_PROVIDER` | Provider de embeddings. Default: `gemini`. |
| `EMBEDDING_MODEL` | Modelo de embeddings. |
| `EMBEDDING_REQUESTS_PER_MINUTE` | Limite de pedidos por minuto. |
| `EMBEDDING_REQUESTS_PER_DAY` | Limite diario. |
| `RAG_REINDEX_ON_START` | `1` para reconstruir indice ao arrancar localmente. |

Para providers custom, usar `LLM_PROVIDER`, `LLM_API_KEY` e `LLM_BASE_URL`.
Chaves com nomes fora da configuracao acima nao sao lidas automaticamente pelo
runtime atual.

### Dados externos

| Variavel | Uso |
|---|---|
| `TIDE_CSV_PATH` | Caminho da tabela de mares. |
| `WEATHERAPI_KEY` | Chave da meteorologia. |
| `WEATHERAPI_LOCATION` | Local da previsao. Default: `Setubal`. |
| `WAVE_API_URL` | Endpoint da ondulacao. |
| `WAVE_STATION_NAME` | Nome da estacao. Default: `Sines`. |
| `LOCAL_WARNING_API_URL` | Endpoint de avisos locais. |

### WhatsApp

| Variavel | Uso |
|---|---|
| `WHATSAPP_VERIFY_TOKEN` | Token de verificacao do webhook. |
| `WHATSAPP_ACCESS_TOKEN` | Token de envio. |
| `WHATSAPP_PHONE_NUMBER_ID` | ID do numero de telefone. |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | ID da conta business. |
| `WHATSAPP_ALLOWED_NUMBERS` | Numeros autorizados, separados por virgula. |
| `WHATSAPP_DEFAULT_ROLE` | Role default para novos contactos. |
| `WHATSAPP_SOS_ENABLED` | Ativar/desativar SOS. |
| `WHATSAPP_SOS_ALERT_NUMBERS` | Numeros que recebem alertas SOS. |
| `WHATSAPP_SOS_PENDING_TTL_MINUTES` | Tempo maximo de pedido SOS pendente. |

### Backups e auditoria

| Variavel | Uso |
|---|---|
| `BACKUP_DIR` | Diretoria dos ZIPs de backup. Default: `data/backups`. |
| `BACKUP_AUTO_ENABLED` | Ativa rotina automatica. Default: `1`. |
| `BACKUP_AUTO_INTERVAL_HOURS` | Intervalo entre backups automaticos. Default: `24`. |
| `BACKUP_RETENTION_COUNT` | Numero maximo de pacotes mantidos. Default: `30`. |
| `AUDIT_LOG_DIR` | Diretoria dos logs JSONL. Default: `data/audit`. |
| `EVENT_REPORTS_DIR` | Diretoria de reportes/eventos com anexos. |

## Execucao local

Com `.env` configurado:

```bash
python3 app.py
```

Ou:

```bash
flask --app app run --host 127.0.0.1 --port 5000
```

Abrir:

```text
http://127.0.0.1:5000
```

Health check:

```text
/healthz
```

Nota: o health check atual confirma apenas que a aplicacao responde e identifica
os backends. A pagina `/admin/status` e a referencia para diagnostico profundo.

## Deploy

O projeto inclui:

- `Dockerfile`
- `railway.toml`
- `gunicorn app:app`
- health check em `/healthz`

Em Railway ou ambiente semelhante:

1. Configurar `DATABASE_URL` com PostgreSQL e extensao `vector`.
2. Configurar variaveis de resposta, embeddings, meteorologia e WhatsApp conforme
   o ambiente.
3. Configurar `FLASK_SECRET_KEY` forte.
4. Configurar volume persistente para backups/auditoria se estes ficheiros forem
   mantidos localmente.
5. Abrir `/admin/status` depois do deploy e confirmar:
   - Persistencia ligada;
   - pgvector ativo;
   - indice documental com cobertura;
   - geracao disponivel;
   - meteorologia/ondulacao/avisos conforme configurado.

## Backups, auditoria e wipe

Backups:

- pagina: `/admin/backups`;
- formato: ZIP com `backup.json` e `README.md`;
- inclui utilizadores, perfis, roles, escalas, manobras, conversas, mensagens,
  eventos WhatsApp, feedback, runtime state, casos, definicoes do bot e ficheiros
  `.txt`, `.md`, `.json` de conhecimento;
- nao inclui, por agora, audit logs JSONL nem fontes originais fora desses
  formatos.

Auditoria:

- pagina: `/admin/auditoria`;
- formato: JSONL;
- redacao automatica de chaves sensiveis;
- exportacao em JSON e JSONL;
- o wipe da base nao apaga estes ficheiros.

Wipe:

- exige admin autenticado;
- exige password atual;
- exige checkbox;
- exige frase exata;
- cria backup pre-limpeza;
- preserva o admin atual;
- apaga dados aplicacionais na base PostgreSQL.

## Catalogo de erros

Os codigos `#ERR-XXXX` ficam em:

```text
domain/error_catalog.py
docs/CATALOGO_ERROS_E_DEBUG.md
```

Faixas principais:

- `#ERR-1xxx` validacao;
- `#ERR-2xxx` sessao, permissoes e seguranca;
- `#ERR-3xxx` regras de negocio;
- `#ERR-4xxx` documentos e indice;
- `#ERR-5xxx` chat e feedback;
- `#ERR-6xxx` WhatsApp;
- `#ERR-7xxx` integracoes;
- `#ERR-8xxx` admin e sistema;
- `#ERR-9xxx` erros internos.

## Testes

Suite completa:

```bash
python3 -m pytest --ignore=REPORT_UAb
```

Testes focados:

```bash
python3 -m pytest tests/test_error_catalog.py tests/test_admin_backups.py tests/test_audit_log.py
python3 -m pytest tests/test_tug_guidance.py tests/test_colreg_commands.py
python3 -m pytest tests/test_chat_runtime_slash_commands.py
```

Auditoria documental:

```bash
python3 scripts/audit_knowledge_chunks.py --sample 0
python3 scripts/run_rag_evals.py
```

Na ultima validacao local, a suite principal passou com `182 passed`.

## Manutencao recomendada

- Confirmar regularmente `/admin/status`.
- Fazer backup manual antes de imports, wipe ou alteracoes grandes.
- Descarregar backups importantes para fora do servidor.
- Rever `/admin/auditoria` depois de acoes sensiveis.
- Correr a suite completa antes de push.
- Reindexar documentos apos alteracoes na pasta `knowledge/`.
- Manter `docs/CATALOGO_ERROS_E_DEBUG.md` atualizado quando surgirem novos
  codigos.
- Evitar editar diretamente dados em producao sem backup recente.

## Pontos conhecidos

- `/healthz` e simples; usar `/admin/status` para diagnostico real.
- Backups e audit logs dependem de storage persistente quando guardados em disco.
- O backup completo ainda nao inclui audit logs JSONL nem todos os formatos de
  fonte original.
- Alguns erros operacionais dinamicos ainda podem aparecer sem `#ERR-XXXX`;
  o catalogo documenta as areas que faltam mapear.
- Providers custom devem usar `LLM_API_KEY` e `LLM_BASE_URL`; nomes de variaveis
  fora da configuracao atual nao sao lidos automaticamente.

## Licenca e integridade academica

Este repositorio e disponibilizado publicamente para avaliacao academica,
demonstracao de portfolio e revisao tecnica do projeto PRAGtico. O codigo e os
materiais especificos do projeto nao sao publicados como open source.

Todos os direitos estao reservados ao autor. Nao e permitida a copia,
redistribuicao, reutilizacao, adaptacao, alojamento ou submissao deste trabalho,
no todo ou em parte, como projeto academico, profissional ou comercial de outra
pessoa. Pequenos excertos podem ser citados para revisao ou discussao academica,
desde que com atribuicao clara.

Ver `LICENSE` e `NOTICE.md` para os termos completos.
