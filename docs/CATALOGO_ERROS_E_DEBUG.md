# Catalogo de erros e debug operacional

Data da revisao: 2026-05-04

Este documento serve para o admin diagnosticar erros do portal, bot, backups,
auditoria, base de dados e integracoes. A fonte tecnica dos codigos fica em
`domain/error_catalog.py`; este ficheiro explica como usar esses codigos em
operacao e enumera as falhas ainda relevantes.

## Como usar

1. Quando aparecer `#ERR-XXXX`, procurar o codigo neste documento e nos logs.
2. Abrir `Admin > Auditoria` e filtrar por utilizador, acao, resultado ou hora.
3. Se o erro envolver servidor, confirmar logs da plataforma pelo mesmo horario.
4. Se envolver dados, criar backup antes de qualquer reposicao, importacao ou limpeza.
5. Se envolver o bot, validar tambem `Admin > Bot`, `Admin > Documentos` e a cobertura do indice documental.

## Mapa de numeracao

| Faixa | Area | Exemplos |
|---:|---|---|
| `#ERR-1xxx` | Validacao de formularios e input | campos obrigatorios, numeros, datas, IMO, cais |
| `#ERR-2xxx` | Autenticacao, sessao e seguranca | login, perfil, permissoes, CSRF, rate limit |
| `#ERR-3xxx` | Regras de negocio | escalas, manobras, aprovacoes, abortos, duplicados |
| `#ERR-4xxx` | Documentos e indice documental | uploads, reindexacao, embeddings |
| `#ERR-5xxx` | Chat, feedback e acoes pendentes | conversa, mensagem, feedback, runtime do bot |
| `#ERR-6xxx` | WhatsApp | webhook, credenciais, envio, media, perfil |
| `#ERR-7xxx` | Integracoes externas | mare, meteorologia, ondulacao, avisos locais |
| `#ERR-8xxx` | Admin e sistema | users, backups, wipe, DB, pgvector, ficheiros grandes |
| `#ERR-9xxx` | Erros internos | falhas inesperadas e excecoes nao tratadas |

## Estado do catalogo

Foi feito um levantamento estatico das mensagens `raise ValueError/RuntimeError`
com texto fixo no codigo.

| Medida | Antes | Depois desta revisao |
|---|---:|---:|
| Mensagens estaticas encontradas | 289 | 289 |
| Mensagens resolvidas para `#ERR-XXXX` | 87 | 127 |
| Mensagens ainda sem mapeamento direto | 202 | 162 |
| Definicoes no catalogo | 124 | 164 |

As novas numeracoes cobrem sobretudo backups, wipe, runtime, feedback e
WhatsApp, porque sao as areas em que o admin mais precisa de diagnostico rapido.

## Codigos criticos para debug

| Codigo | Area | Sintoma provavel | Onde confirmar | Acao imediata |
|---|---|---|---|---|
| `#ERR-2020` | Seguranca | Pagina "Acesso negado" apos botao/formulario | Auditoria por `request.*`, endpoint e `status_code=403` | Recarregar pagina, repetir com sessao valida e confirmar CSRF/permissao |
| `#ERR-2021` | Seguranca | Pedidos bloqueados por excesso | Logs HTTP e auditoria | Aguardar janela do rate limit; rever automacoes/repeticoes |
| `#ERR-4020` | Indice | Motor sem embeddings | Admin > Estado / Documentos | Confirmar provider, chave, modelo e reindexacao |
| `#ERR-4022` | Indice | Reindexacao ja em curso | Admin > Documentos | Aguardar ou rever se ficou presa em erro |
| `#ERR-4023` | Indice | Cliente sem suporte para embeddings | Logs do servidor | Corrigir provider/modelo de embeddings |
| `#ERR-4024` | Indice | Falha ao gerar embeddings | Admin > Documentos e logs | Rever quota, chave, modelo e resposta remota |
| `#ERR-4025` | Indice | Pesquisa semantica bloqueada por quota | Estado do indice | Aguardar renovacao de quota ou trocar provider |
| `#ERR-5001` | Bot | Acao operacional nao aplicada | Conversa + auditoria | Confirmar dados da proposta antes de repetir |
| `#ERR-5034` | Feedback | Resposta nao encontrada | Conversa/casebook | Confirmar `message_id` e conversa original |
| `#ERR-5035` | Feedback | Pergunta original nao localizada | Conversa/casebook | Rever historico da conversa antes de reutilizar feedback |
| `#ERR-5036` | Feedback | Sem resposta anterior para rever | WhatsApp/conversa | Pedir nova resposta ou indicar melhor contexto |
| `#ERR-5037` | WhatsApp/chat | Limite de divisao demasiado baixo | Configuracao runtime | Repor limite seguro de mensagens WhatsApp |
| `#ERR-6001` | WhatsApp | Webhook desligado | Configuracao WhatsApp | Confirmar `WHATSAPP_WEBHOOK_ENABLED` e credenciais |
| `#ERR-6002` | WhatsApp | Verificacao de webhook falhou | Logs webhook | Confirmar verify token configurado na Meta |
| `#ERR-6010` | WhatsApp | Envio sem credenciais | Admin > Estado / logs | Configurar credenciais de envio |
| `#ERR-6016` | WhatsApp | Media recebida nao descarrega | Logs WhatsApp | Confirmar token/permissoes de media |
| `#ERR-6017` | WhatsApp | Meta nao devolve URL de media | Logs WhatsApp | Repetir pedido e confirmar validade do media id |
| `#ERR-6018` | WhatsApp | Perfil business sem credenciais | Logs WhatsApp | Configurar credenciais de perfil |
| `#ERR-6022` | WhatsApp | Envio bloqueado por credenciais | Logs WhatsApp | Confirmar numero, token e phone number id |
| `#ERR-7010` | Mares | Falha ao obter mares | Admin > Estado | Confirmar ficheiro/fonte de mares |
| `#ERR-7011` | Meteorologia | Falha de meteorologia | Admin > Estado | Confirmar `WEATHERAPI_KEY` e localizacao |
| `#ERR-7012` | Ondulacao | Falha da fonte de ondulacao | Admin > Estado | Usar cache ou rever endpoint |
| `#ERR-7013` | Avisos locais | Falha dos avisos | Admin > Estado | Usar cache ou rever endpoint |
| `#ERR-8012` | Backups | JSON de backup em falta | Admin > Backups | Carregar ZIP/JSON valido |
| `#ERR-8013` | Backups | ZIP invalido | Admin > Backups | Recriar pacote ou extrair `backup.json` |
| `#ERR-8014` | Backups | ZIP sem JSON | Conteudo do ZIP | Confirmar que existe `backup.json` |
| `#ERR-8017` | Backups | Tipo de backup de sistema nao suportado | `backup.json` | Confirmar `kind` e versao do backup |
| `#ERR-8018` | Backups | Diretoria knowledge indisponivel | Logs servidor | Confirmar caminho e permissoes da pasta |
| `#ERR-8019` | Backups | Importacao sem backend PostgreSQL | Admin > Estado | Confirmar `DATABASE_URL` e backend |
| `#ERR-8020` | Wipe | Password admin em falta | Admin > Backups | Preencher password atual |
| `#ERR-8021` | Wipe | Password invalida/sem admin | Auditoria | Confirmar utilizador autenticado e role admin |
| `#ERR-8022` | Wipe | Checkbox de confirmacao em falta | Admin > Backups | Marcar confirmacao explicita |
| `#ERR-8023` | Wipe | Frase de confirmacao errada | Admin > Backups | Escrever exatamente a frase pedida |
| `#ERR-8025` | Wipe | Wipe completo sem PostgreSQL | Admin > Estado | Usar backend PostgreSQL |
| `#ERR-8061` | Sistema | `DATABASE_URL` em falta | Arranque/logs | Definir `DATABASE_URL` |
| `#ERR-8063` | Sistema | Dependencias pgvector em falta | Arranque/logs | Instalar dependencias ou corrigir imagem |
| `#ERR-8064` | Sistema | Extensao pgvector em falta | Base de dados | Usar PostgreSQL com pgvector ativo |
| `#ERR-8080` | Sistema | Upload demasiado grande | UI/logs | Reduzir ficheiro ou ajustar `MAX_UPLOAD_MB` |
| `#ERR-9000` | Interno | Erro inesperado geral | Logs servidor | Procurar stack trace no horario do erro |
| `#ERR-9001` | Bot | Mensagem nao processada | Logs + conversa | Confirmar provider, indice e payload da pergunta |
| `#ERR-9020` | Bot | Falha ao aplicar acao | Logs + conversa | Rever proposta e estado da escala/manobra |

## Achados numerados

| ID | Risco | Estado | Descricao | Recomendacao |
|---|---|---|---|---|
| `DBG-001` | Alto | Parcialmente corrigido | Havia muitas mensagens de backup/wipe sem `#ERR`. | Numeracao adicionada em `#ERR-8012` a `#ERR-8026`; manter testes. |
| `DBG-002` | Alto | Aberto | Ainda existem 162 mensagens estaticas sem mapeamento direto. A maior parte esta em regras operacionais e validadores dinamicos. | Criar helper unico para erros de formulario/manobra e expandir testes de catalogo. |
| `DBG-003` | Medio | Aberto | `#ERR-2020` cobre CSRF e tambem 403 generico. Isto ajuda o utilizador, mas dificulta saber se foi permissao ou token. | Registar motivo interno no audit log e, se util, separar CSRF de permissao em codigos distintos. |
| `DBG-004` | Medio | Aberto | `/healthz` devolve `ok: true` sem fazer prova profunda de DB, indice, geracao ou integracoes. | Criar health check profundo para admin/plataforma e manter `/healthz` simples se a plataforma exigir resposta rapida. |
| `DBG-005` | Medio | Aberto | Backups e auditoria ficam em ficheiros locais (`BACKUP_DIR`, `AUDIT_LOG_DIR` ou `data/`). Em plataforma com disco nao persistente, podem perder-se em redeploy. | Confirmar volume persistente e, idealmente, descarregar backups regularmente. |
| `DBG-006` | Medio | Aberto | O backup completo inclui tabelas e ficheiros `.txt`, `.md`, `.json` de conhecimento; nao inclui PDFs/DOCX originais nem audit logs JSONL. | Decidir se audit logs e fontes originais tambem devem entrar no ZIP. |
| `DBG-007` | Medio | Aberto | Exportar detalhes de erro em `/api/chat` facilita debug, mas pode mostrar detalhes tecnicos a utilizadores autenticados. | Em producao, reduzir detalhe externo e manter detalhe completo nos logs. |
| `DBG-008` | Medio | Aberto | `CEREBRAS_API_KEY` nao e reconhecida diretamente pelo runtime atual. | Se for usado Cerebras, criar provider proprio ou configurar provider compativel com `LLM_API_KEY` e `LLM_BASE_URL`. |
| `DBG-009` | Baixo | Aberto | Alguns endpoints de API de custos devolvem texto simples sem `#ERR`. | Usar `error_payload`/`flash_error_message` tambem nesses endpoints. |
| `DBG-010` | Baixo | Aberto | Ha mensagens antigas sem acentos ou com variacoes (`Role invalido`, `Esse utilizador ja existe`). | Normalizar mensagens quando essas areas forem mexidas. |

## Areas ainda com mensagens sem mapeamento

| Area | Mensagens sem mapeamento | Exemplos |
|---|---:|---|
| `storage/postgres_port_calls.py` | 44 | campos obrigatorios de escala/manobra, estados pendentes/aprovados |
| `core/validators.py` | 23 | validacoes dinamicas de texto, numeros, datas, email e telefone |
| `blueprints/port_calls.py` | 17 | catalogo de navios, importacao JSON, feedback operacional |
| `core/operational_actions.py` | 12 | propostas de acao do bot sobre manobras |
| `blueprints/admin.py` | 10 | reportes, casebooks e importacoes especificas |
| `domain/practice_experience.py` | 7 | importacao de experiencia pratica |
| `core/operational_test_suite.py` | 7 | fixtures de testes operacionais |
| restantes | 42 | runtime, documentos, SOS, avaliacoes, providers e casos pontuais |

Estas mensagens nao significam necessariamente bug funcional; significam que,
se chegarem ao utilizador/admin, podem aparecer sem referencia `#ERR-XXXX`.

## Procedimentos de diagnostico

### Erro `#ERR-2020`

1. Verificar se a sessao esta autenticada e se o perfil tem role correta.
2. Recarregar a pagina para renovar token CSRF.
3. Em `Admin > Auditoria`, filtrar `result=denied` e hora do erro.
4. Confirmar `request.endpoint`, `method`, `path` e `form_keys`.
5. Se foi POST/DELETE, confirmar se o formulario tem `csrf_token`.

### Backup falha

1. Confirmar `Admin > Backups` e audit events `backup.create`, `backup.download`, `backup.delete`.
2. Confirmar se `BACKUP_DIR` existe e tem escrita.
3. Se for importacao, abrir o ZIP e verificar `backup.json`.
4. Confirmar que o `backup.json` tem `kind=pragtico.system_database_export`.
5. Antes de modo `Substituir`, criar novo backup do estado atual.

### Wipe falha

1. Confirmar que o utilizador autenticado continua a ser admin.
2. Repetir com password atual e frase exata `LIMPAR BASE PRAGTICO`.
3. Confirmar que o backend e PostgreSQL.
4. Verificar audit events `database.wipe`.
5. Se houve erro apos backup pre-limpeza, descarregar esse backup antes de nova tentativa.

### Bot nao responde

1. Confirmar `Admin > Estado`: geracao, embeddings, persistencia e indice documental.
2. Confirmar se existem alertas no topo da pagina de Estado.
3. Verificar logs com `error_ref=#ERR-9001`.
4. Se houver quota de embeddings, aguardar renovacao ou trocar configuracao.
5. Se a resposta falhar no WhatsApp mas funcionar no site, verificar `#ERR-60xx`.

### WhatsApp falha

1. Confirmar webhook: `#ERR-6001` ou `#ERR-6002`.
2. Confirmar credenciais de envio/perfil: `#ERR-6010`, `#ERR-6018`, `#ERR-6022`.
3. Confirmar eventos em `channel_events` e auditoria.
4. Para media/fotos, confirmar `media_id`, token e permissao de descarga.
5. Se for SOS, confirmar numeros configurados e runtime state pendente.

### Base de dados ou indice falha no arranque

1. Confirmar `DATABASE_URL`.
2. Confirmar que a base tem extensao `vector`.
3. Confirmar dependencias `psycopg[binary]` e `pgvector`.
4. Confirmar logs de arranque e pagina `Admin > Estado`.
5. Se o indice ficou parcial, correr reindexacao incremental.

## Comandos uteis em local

```bash
python3 -m pytest --ignore=REPORT_UAb
python3 -m pytest tests/test_error_catalog.py tests/test_admin_backups.py tests/test_audit_log.py
python3 scripts/audit_knowledge_chunks.py --sample 0
```

## Proximos passos recomendados

1. Mapear gradualmente as 162 mensagens restantes para `#ERR-XXXX`.
2. Criar teste que falhe quando novas mensagens user-facing forem adicionadas sem catalogo.
3. Separar internamente `CSRF_FAILED` e `PERMISSION_DENIED` nos eventos de auditoria.
4. Decidir se audit logs e ficheiros originais devem entrar no backup completo.
5. Adicionar suporte direto a Cerebras se essa configuracao for usada em producao.
6. Criar health check profundo para confirmar DB, indice documental e motor de resposta.
