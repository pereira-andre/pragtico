PRAGTICO DATABASE - README DE PERSISTENCIA
Atualizado: 2026-04-09

1. OBJETIVO

Este ficheiro descreve o que o PRAGtico guarda, onde guarda, e qual e o papel de cada entidade.
O sistema suporta dois modos de persistencia:
- local JSON
- PostgreSQL

O backend applicacional tenta manter a mesma semantica nos dois modos.


2. BACKENDS SUPORTADOS

2.1 Local JSON
- usado em desenvolvimento/testes ou ambientes simples
- ficheiros principais em data/

2.2 PostgreSQL
- usado em Railway/producao ou ambientes persistentes
- schema definido em sql/postgres_schema.sql
- o schema e aplicado no arranque da app


3. O QUE FICA FORA DA BASE DE DADOS

Nem tudo vive em JSON/Postgres.
Os seguintes artefactos continuam no filesystem:

- knowledge/
  Documentos fonte do conhecimento

- knowledge/companions/
  Companions estruturados por documento

- knowledge/evals/
  Evals estaticas versionadas no repositorio

- data/exports/
  Exportacoes geradas manualmente, quando existirem

Nota:
- a tabela documents guarda metadados dos documentos
- mas o ficheiro fisico continua em knowledge/


4. MAPA RAPIDO LOCAL JSON <-> POSTGRES

data/users.json                 <-> app_users
data/documents.json             <-> documents
data/port_calls.json            <-> port_calls
data/maneuver_cases.json        <-> maneuver_cases
data/conversations.json         <-> conversations
data/messages.json              <-> messages
data/channel_events.json        <-> channel_events
data/runtime_state.json         <-> app_runtime_state
data/feedback_eval_cases.json   <-> feedback_eval_cases


5. ENTIDADES PRINCIPAIS

5.1 app_users / users.json

Guarda os utilizadores da plataforma.

Campos relevantes:
- username
  Identificador principal da conta
- password_hash
  Password com hash
- role
  admin, agente ou piloto
- full_name
- organization
- email
- phone
- whatsapp_number
- whatsapp_opt_in
- whatsapp_opt_in_at
- profile_completed_at

Notas:
- utilizadores vindos do WhatsApp podem nascer com username tecnico tipo whatsapp-<numero>@pragtico.local
- o admin pode depois renomear a conta e completar o perfil


5.2 documents / documents.json

Guarda metadados dos documentos de conhecimento.

Campos relevantes:
- name
  Nome tecnico do ficheiro
- original_name
- doc_type
- size_bytes
- updated_at
- created_at
- uploaded_by
- preview
- file_path (em Postgres)

Notas:
- o texto real do documento continua a ser lido do ficheiro em knowledge/
- a aplicacao sincroniza metadados com a pasta knowledge/


5.3 port_calls / port_calls.json

E a entidade central das escalas/navios.

Guarda:
- identidade do navio
  vessel_name, imo, call_sign, flag, vessel_type
- dimensoes e caracteristicas
  loa, beam, gt, dwt, draft maximo, bow/stern thruster
- estado da escala
  scheduled, in_port, departed
- estados de aprovacao
  approval_status, shift_approval_status, notas e motivos
- tempos
  eta, ata, planned_departure_at, departure_at, planned_shift_at, shift_at
- origem/destino
  berth, last_port, next_port
- movimentacao de shift
  shift_origin_berth, shift_destination_berth
- historico sintetico
  maneuver_history
- autoria
  created_by, created_at, updated_at
- observacoes agregadas
  notes

Notas:
- custos, estadia e alguns indicadores nao sao guardados como tabela propria; sao calculados a partir da escala e das manobras


5.4 maneuver_cases / maneuver_cases.json

Guarda cada manobra como caso operacional autonomo.

Campos relevantes:
- maneuver_id
- port_call_id
- reference_code
- vessel_name
- maneuver_type
  entrada, saida, mudanca, etc.
- current_state
- origin_label
- destination_label
- planned_at
- decided_at
- completed_at
- reported_at
- latest_event_at
- case_summary

Snapshots JSON:
- vessel_snapshot
- scale_snapshot
- planning_snapshot
- decision_snapshot
- execution_snapshot
- outcome_snapshot
- environment_snapshot
- feature_snapshot
- change_log

Feedback sobre a manobra:
- feedback_status
- feedback_note
- feedback_updated_by
- feedback_updated_at

Notas:
- esta tabela existe para arquivo, analise, historico e comparacao de casos semelhantes
- o portal pode reconstruir relatorios e historico favoravel a partir destes snapshots


5.5 conversations / conversations.json

Guarda os topicos de conversa do bot por utilizador.

Campos:
- id
- username
- title
- created_at
- updated_at


5.6 messages / messages.json

Guarda cada mensagem de chat.

Campos base:
- id
- conversation_id
- role
  user, assistant ou system
- content
- citations

Feedback:
- feedback_status
  approved ou review
- feedback_note
- feedback_correction
- feedback_correction_document
- feedback_updated_by
- feedback_updated_at

Canal:
- channel
  web ou whatsapp
- channel_user_id
- external_message_id
- external_reply_to_id
- channel_metadata

Notas:
- esta tabela e crucial para:
  - historico do chat
  - memoria aprovada
  - review_guard
  - correcoes supervisionadas
  - correlacao com mensagens externas do WhatsApp


5.7 channel_events / channel_events.json

Guarda eventos de transporte/canal.

Exemplos:
- incoming_text
- outgoing_text
- outgoing_welcome
- incoming_reaction

Campos relevantes:
- channel
- event_type
- username
- conversation_id
- local_message_id
- channel_user_id
- external_event_id
- external_message_id
- payload
- created_at

Notas:
- serve para auditoria e para o feed live do portal


5.8 app_runtime_state / runtime_state.json

Armazem de estado tecnico/transitorio que precisa sobreviver ao processo.

Usos tipicos:
- acoes pendentes do chat
- estados de reindexacao
- etapas pendentes do WhatsApp
- ultimos marcadores de runtime

Campos:
- key
- value (JSON)
- updated_at

Notas:
- e pequeno, mas importante para fluxos multi-passo


5.9 feedback_eval_cases / feedback_eval_cases.json

Tabela nova para casos supervisionados derivados de correcoes reais.

Campos:
- id
- source_message_id
- document
- question
- expected_answer
- expected_substrings
- feedback_note
- updated_by
- source
  web, whatsapp, etc.
- created_at
- updated_at

Papel:
- persistir o conhecimento supervisionado vindo do operador
- alimentar o runner de evals
- permitir tracking do progresso do bot ao longo do tempo

Notas:
- esta tabela nao substitui o documento fonte
- funciona como regua adicional e memoria de qualidade


6. INDICES IMPORTANTES EM POSTGRES

Existem indices para:
- conversas por username e updated_at
- mensagens por conversation_id e created_at
- unicidade de external_message_id por canal
- eventos de canal por created_at
- port_calls por status e datas
- maneuver_cases por port_call_id
- feedback_eval_cases por source_message_id e por document+question

Isto ajuda em:
- chat
- WhatsApp
- dashboard
- arquivo
- feedback supervisionado


7. DADOS DERIVADOS VS DADOS FONTE

Dados fonte:
- utilizadores
- escalas
- manobras
- mensagens
- documentos
- correcoes supervisionadas

Dados derivados/calculados:
- custos e estadias
- historico favoravel
- casos semelhantes
- snapshots do dashboard
- feed live
- pass rate dos evals

Importante:
- alguns derivados sao recalculados em runtime com base nas tabelas principais
- por isso, mexer diretamente na base de dados pode criar incoerencias se nao respeitar o modelo todo


8. SCHEMA E MIGRACOES

8.1 Postgres
- o schema esta em sql/postgres_schema.sql
- a app usa CREATE TABLE IF NOT EXISTS e ALTER TABLE ... ADD COLUMN IF NOT EXISTS
- isto permite evoluir o schema sem migration manual formal para muitos casos simples

8.2 Railway
- basta redeploy/restart para a app reaplicar o schema no arranque
- se surgir tabela/coluna nova, o arranque trata disso

8.3 Local JSON
- o storage cria ficheiros default se nao existirem
- alguns dados antigos sao migrados automaticamente quando o store arranca


9. SEGURANCA E DADOS SENSIVEIS

Dados potencialmente sensiveis guardados:
- emails
- telefones
- numeros WhatsApp
- historico conversacional
- feedback de operadores

Cuidados:
- passwords sao guardadas em hash, nao em claro
- continuar a evitar dumps/exports sem necessidade
- quando se fizer debug, preferir o painel admin ou scripts controlados


10. CONSULTAS E OPERACOES QUE MAIS BATEM NESTA PERSISTENCIA

- login e perfil
  app_users

- dashboard e arquivo
  port_calls + maneuver_cases

- chat web
  conversations + messages + app_runtime_state

- WhatsApp
  messages + channel_events + app_runtime_state + app_users

- documentos
  documents + knowledge/

- tracking do bot
  feedback_eval_cases + knowledge/evals + companions


11. COMO LER O ESTADO ATUAL SEM IR DIRETO A SQL

No portal:
- /admin/status
  saude geral da plataforma

- /admin/users
  perfis e diagnostico WhatsApp

- /admin/documents
  base documental e reindexacao

- /admin/bot
  progresso do bot, evals e correcoes supervisionadas

Fora do portal:
- scripts/run_knowledge_evals.py
- scripts/export_feedback_correction_evals.py


12. RESUMO EXECUTIVO

A persistencia do PRAGtico esta dividida em duas camadas:

- documentos e companions no filesystem
- estado operacional, chat, feedback e tracking em JSON/Postgres

O centro da operacao esta em:
- port_calls
- maneuver_cases
- messages
- feedback_eval_cases

Com isto, o sistema consegue:
- operar escalas e manobras
- manter historico completo
- suportar chat web e WhatsApp
- aprender com feedback supervisionado
- medir a qualidade atual do bot
