# Portal RAG Marítimo

Plataforma web com:

- autenticação com perfis `admin`, `agente`, `piloto`
- chatbot com RAG e citações explícitas
- base documental local com upload de ficheiros
- indexação semântica com embeddings do Gemini e backend `pgvector`
- conversas separadas por sessão
- feedback operacional nas respostas, com reutilização de respostas aprovadas
- marés locais a partir de CSV
- meteorologia ao vivo via WeatherAPI
- mapa AIS de Setúbal com embed publico via VesselFinder
- frontend web simples para servir localmente

## Como correr

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 app.py
```

Depois abre `http://127.0.0.1:5000`.

## Como correr com Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Isto sobe:

- `web`: Flask
- `db`: PostgreSQL com extensão `pgvector`

Depois abre `http://127.0.0.1:5000`.

Se a porta `5000` já estiver ocupada no macOS, define por exemplo:

```bash
WEB_PORT=5001 docker compose up --build
```

e depois abre `http://127.0.0.1:5001`.

O `docker-compose.yml` usa agora `WEB_PORT` tanto no host como dentro do container, por isso os logs do Flask também passam a mostrar a mesma porta.

## Comandos rápidos

```bash
make install
make run
make up
make down
make health
```

## Criar acessos

1. cria a conta no registo do portal com o email que vais usar no login
2. inicia sessão com esse email e a password da conta
3. para promover uma conta existente a `admin` ou alterar o perfil:

```bash
make admin EMAIL=operador@porto.pt
make agente EMAIL=operador@porto.pt
make piloto EMAIL=operador@porto.pt
```

4. para criar logo uma conta `admin` por linha de comando:

```bash
make create-admin EMAIL=admin@porto.pt PASSWORD='troca-esta-password'
```

O perfil `admin` não é criado pelo registo público.

## Stack atual

- `Supabase Auth` para login
- `PostgreSQL` como storage principal
- `pgvector` como índice vetorial principal
- `Gemini` está ligado via `google-genai`.
- `Cerebras`, APIs de marés e meteorologia e storage documental externo ficam como extensão futura.

## RAG documental

- a gestão documental (`upload`, edição, remoção e reindexação) fica reservada ao utilizador com papel `admin`
- `agente` e `piloto` podem consultar os documentos e usar o chat, mas não alteram a base RAG
- `RAG_REINDEX_ON_START=0` por omissão para evitar reindexações automáticas no arranque

## Tipos de ficheiro suportados

- `.pdf`
- `.md`
- `.txt`
- `.docx`
- `.csv`

A pasta `knowledge/` é a fonte principal do RAG. Qualquer ficheiro suportado que coloques lá passa a ser sincronizado automaticamente pelo portal e entra no índice RAG sem precisares de o duplicar via upload.

Os ficheiros carregados no portal também são guardados em `knowledge/`. Se já existir um ficheiro com o mesmo nome, o portal substitui-o em vez de criar cópias `-1`, `-2`, ...

O ficheiro `knowledge/mares.2026.201.9_setubal_troia.csv` é tratado também como fonte estruturada de marés para o dashboard e para o contexto do chat.

## Meteorologia

Para ativar meteorologia ao vivo:

```bash
WEATHERAPI_KEY=...
WEATHERAPI_LOCATION=Setubal
```

O portal usa a WeatherAPI para forecast em tempo real e injeta esse contexto no chatbot.

## AIS

Para ativar o mapa AIS no dashboard:

```bash
AIS_MAP_CENTER=[38.459517,-8.868642]
AIS_MAP_ZOOM=11
AIS_VESSELFINDER_NAMES=1
AIS_PORT_LABEL="Porto de Setubal"
```

O portal usa o embed oficial do VesselFinder centrado em Setúbal e mostra-o no dashboard logo abaixo da faixa meteorológica.
Nao ha chave, polling nem geracao local de ficheiro AIS: o mapa e carregado diretamente a partir do script publico `https://www.vesselfinder.com/aismap.js`.
Se quiseres reposicionar a vista, ajusta `AIS_MAP_CENTER` e `AIS_MAP_ZOOM`.

Se quiseres validar a configuracao AIS isoladamente:

```bash
python3 ais/ais.py
```

## Configuração principal

```bash
APP_STORAGE_BACKEND=postgres
RAG_INDEX_BACKEND=pgvector
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag_portal
EMBEDDING_BATCH_SIZE=32
EMBEDDING_REQUESTS_PER_MINUTE=90
EMBEDDING_REQUESTS_PER_DAY=900
```

Para contas Gemini em free tier, estes valores deixam margem para perguntas normais do chat sem esgotar facilmente a quota durante uma reindexação. Se tiveres faturação ativa ou um tier superior, podes subir `EMBEDDING_REQUESTS_PER_MINUTE` e `EMBEDDING_REQUESTS_PER_DAY`.

Schemas incluídos:

- `sql/postgres_schema.sql`
- `sql/pgvector_schema.sql`

## Estrutura

- `app.py`: rotas web, sessão e API de chat
- `auth_service.py`: autenticação local com hashing seguro de passwords
- `document_processing.py`: upload, sanitização e extração de texto
- `storage.py`: persistência aplicacional
- `vector_store.py`: índice vetorial
- `rag_engine.py`: chunking, embeddings, retrieval e geração com citações
- `knowledge/`: documentos para o RAG
- `data/`: dados locais auxiliares
- `docker-compose.yml`: stack local com `web + postgres + pgvector`

## Conversas por sessão

- cada conversa tem um `conversation_id`
- o título é criado a partir da primeira pergunta do utilizador
- o histórico fica separado por conversa e reaproveitado no prompt seguinte

## Citações

- o RAG gera fontes com ids `[S1]`, `[S2]`, ...
- a interface guarda e mostra os chunks usados em cada resposta

## Feedback e fiabilidade

- cada resposta do assistente pode ser marcada como `Aprovar` ou `Pedir revisão`
- podes deixar uma nota operacional para explicar quando aquela resposta deve ser reutilizada
- se a mesma pergunta voltar a aparecer, o portal reaproveita automaticamente a resposta aprovada
- para perguntas parecidas, o feedback aprovado entra no prompt como memória operacional validada

## Página admin

- `admin` passa a ter acesso a `Estado da plataforma`
- mostra backend ativo, estado da base, `pgvector` e estado do índice RAG
