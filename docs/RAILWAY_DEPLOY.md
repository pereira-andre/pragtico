# Deploy no Railway

Este repositório já inclui `Dockerfile`, `Procfile`, `railway.toml` e o endpoint `/healthz`, por isso não precisa de refactor extra para arrancar no Railway.

## Topologia recomendada

- 1 serviço web ligado ao repositório GitHub
- 1 base de dados PostgreSQL compatível com `pgvector`

## Passo a passo

### 1. Publicar o repositório no GitHub

Confirma que o estado local que queres publicar está num branch remoto. O Railway vai buscar o código a partir do GitHub.

### 2. Criar o projeto no Railway

1. Entra no dashboard do Railway.
2. Cria um novo projeto a partir do teu repositório GitHub.
3. Seleciona este repositório.

O Railway vai detetar o `Dockerfile` presente na raiz e usá-lo no build do serviço.

### 3. Adicionar a base de dados

Preferência recomendada:

- Usa um serviço PostgreSQL com `pgvector`, porque o projeto suporta índice vetorial em base de dados (`RAG_INDEX_BACKEND=pgvector`).

Notas:

- A documentação oficial do Railway indica que o template PostgreSQL base não inclui extensões por defeito.
- A mesma documentação lista `pgvector` como opção disponível no template marketplace.

Se preferires arrancar primeiro sem índice vetorial em base de dados, podes usar PostgreSQL normal e definir `RAG_INDEX_BACKEND=local`, mas aí o índice RAG fica dependente do filesystem do serviço.

### 4. Configurar variáveis do serviço web

No serviço da aplicação, define pelo menos:

```bash
FLASK_ENV=production
FLASK_SECRET_KEY=<gera uma chave segura>
APP_STORAGE_BACKEND=postgres
RAG_INDEX_BACKEND=pgvector
DATABASE_URL=${{Postgres.DATABASE_URL}}
MIGRATE_LOCAL_DATA_ON_START=1
```

Variáveis adicionais conforme o que quiseres ativar:

```bash
GEMINI_API_KEY=<opcional, mas necessária para geração Gemini>
OPENROUTER_API_KEY=<alternativa ao Gemini>
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash
WEATHERAPI_KEY=<opcional>
WEATHERAPI_LOCATION=Setubal
ADMIN_EMAIL=admin@porto.pt
ADMIN_PASSWORD=<password inicial temporária>
```

Notas:

- Substitui `Postgres` no template `${{Postgres.DATABASE_URL}}` pelo nome real do serviço de base de dados no canvas, se for diferente.
- Se usares `pgvector`, mantém `RAG_INDEX_BACKEND=pgvector`.
- Se usares PostgreSQL sem extensão vetorial, muda para `RAG_INDEX_BACKEND=local`.

### 5. Garantir o schema da base de dados

O storage PostgreSQL e o índice `pgvector` criam o seu schema quando a aplicação arranca, desde que a base de dados aceite a extensão `vector`.

Se precisares de preparar a base manualmente:

1. Liga-te à base de dados Railway com um cliente PostgreSQL.
2. Executa, por esta ordem:
   - `sql/init_extensions.sql`
   - `sql/postgres_schema.sql`
   - `sql/pgvector_schema.sql`

### 6. Fazer o primeiro deploy

1. Volta ao serviço web.
2. Abre o deployment mais recente.
3. Confirma nos logs que o `gunicorn` arrancou sem erro.
4. Confirma que o healthcheck `/healthz` fica verde.

### 7. Gerar domínio público

1. Abre o serviço web.
2. Vai a `Settings`.
3. Em `Networking -> Public Networking`, escolhe `Generate Domain`.

Quando o Railway detetar que a app está a ouvir corretamente, também costuma sugerir este passo no canvas.

### 8. Verificação funcional mínima

Depois do primeiro deploy:

1. Abre o domínio gerado.
2. Verifica `GET /healthz`.
3. Faz login com o admin inicial.
4. Confirma no painel admin:
   - backend de storage `postgres`
   - backend RAG `pgvector` ou `local`, consoante a escolha
   - estado do índice documental sem erro bloqueante

## O que eu recomendo para este projeto

Para produção, a configuração mais coerente é:

- `APP_STORAGE_BACKEND=postgres`
- `RAG_INDEX_BACKEND=pgvector`
- serviço de base de dados Railway com `pgvector`
- domínio público gerado pelo Railway logo após o primeiro deploy saudável

Essa combinação evita depender de ficheiros locais para dados operacionais e mantém o índice RAG persistente entre deploys.
