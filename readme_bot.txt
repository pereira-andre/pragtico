PRAGTICO BOT - README OPERACIONAL
Atualizado: 2026-04-09

1. OBJETIVO

O bot do PRAGtico e um assistente operacional orientado a contexto portuario.
Serve para:
- responder a perguntas documentais e operacionais
- consultar estado da operacao
- apoiar validacoes e planeamento
- no canal web, preparar e confirmar algumas acoes sobre o portal
- aprender com feedback aprovado e com correcoes supervisionadas


2. CANAIS ATUAIS

2.1 Web
- widget flutuante de chat
- arquivo de conversas
- feedback estruturado por mensagem: aprovado ou em revisao
- se a resposta for posta em revisao, o operador pode deixar:
  - nota
  - resposta corrigida
  - documento base opcional

2.2 WhatsApp
- webhook inbound/outbound
- resposta textual com citacoes persistidas no backend
- welcome controlada pelo backend
- reacoes:
  - 👍 aprova a resposta
  - 👎 marca revisao e abre um passo seguinte para recolher a resposta correta
- a mensagem seguinte do utilizador pode ser associada como correcao supervisionada


3. FLUXO BASE DE UMA PERGUNTA

O fluxo principal esta em core/chat_runtime.py, na funcao handle_chat_turn(...).
O planeamento da pergunta esta em core/chat_planner.py.

A ordem de decisao atual e esta:

3.1 Preparacao do turno
- garante ou cria a conversa
- carrega historico recente
- refresca estado do conhecimento
- projeta o utilizador/role atual no contexto do turno

3.2 Planner de execucao
- normaliza a pergunta
- identifica a intencao principal
- decide se a pergunta quer:
  - dados live diretos
  - dados live com horizonte temporal
  - consulta documental
  - lookup operacional deterministico
  - sintese entre live + documentos
  - raciocinio operacional com dados live como input
- este passo evita que perguntas de decisao operacional caiam num simples snapshot live

3.3 Memoria por feedback anterior
- procura respostas aprovadas semelhantes
- procura respostas revistas semelhantes
- calcula tres mecanismos:
  - approved_memory
  - review_guard
  - review_correction_memory

3.4 Comandos slash e acoes operacionais
- /help
- /query
- /validar-...
- /aprovar
- /registar-manobra
- /editar-manobra
- /abortar
- outros templates e propostas guiadas

No canal web, quando a mutacao e permitida:
- o bot pode propor uma acao
- pedir campos em falta
- guardar a acao pendente
- confirmar execucao
- aplicar a alteracao no portal

No WhatsApp:
- mutacoes continuam bloqueadas
- o bot responde em modo consulta, validacao e apoio

3.5 Resposta operacional direta
- antes de ir para LLM, tenta consultas operacionais deterministicas
- exemplos:
  - IDs de manobras
  - marés para uma data
  - meteorologia atual
  - previsao meteo ate uma hora pedida
  - ondulacao e avisos locais
- se a pergunta usar dados live para decidir algo operacional
  - por exemplo "a que horas deve embarcar piloto?"
  - o planner ja nao devolve logo o snapshot; encaminha para sintese com LLM

3.6 Targeting documental
- tenta identificar o documento certo quando a pergunta aponta para:
  - codigo tipo IT-036, RG-14, P-13
  - titulo/alias do documento
  - follow-up tipo "esse documento" / "o que diz o documento"
  - pistas vindas de respostas aprovadas com citacoes

3.7 Companions
- se houver companion aplicavel, o bot tenta responder antes do LLM
- isto torna a resposta muito mais previsivel
- ha dois niveis:
  - companion manual, curada em knowledge/companions/*.json
  - companion auto-gerada a partir do proprio texto do documento

3.8 Guardas de feedback
- se existir correcao supervisionada suficientemente semelhante, ela pode ser reutilizada
- se existir apenas revisao sem resposta corrigida, o review_guard bloqueia a repeticao cega
- se houver grounding documental, o bot prefere responder com base nos documentos em vez de se refugiar no review_guard

3.9 LLM + RAG
- se os passos anteriores nao resolverem a pergunta:
  - o motor RAG recupera contexto documental
  - injeta tambem contexto suplementar operacional
  - quando a pergunta e mista, injeta tambem contexto live recolhido pelo planner
  - passa trusted_answers e reviewed_answers para o prompt
  - tenta reconciliar respostas antigas, documentos e correcoes do operador

3.10 Persistencia
- guarda a mensagem do utilizador
- guarda respostas intermédias quando existirem
- guarda resposta final do assistente
- persiste citacoes, origem da resposta e metadados do canal


4. ORIGENS DE RESPOSTA MAIS IMPORTANTES

O campo answer_origin permite perceber de onde veio a resposta.
Exemplos relevantes:
- slash_help
- slash_template
- slash_proposal
- pending_action_confirmed
- operational_live
- operational_lookup
- document_companion
- document_companion_global
- approved_memory
- review_correction_memory
- review_guard
- llm
- whatsapp_mutations_blocked


5. CONHECIMENTO DOCUMENTAL

5.1 Pasta knowledge/
- contem os documentos fonte
- estes ficheiros sao a base oficial usada pelo sistema

5.2 Pasta knowledge/companions/
- JSONs estruturados por documento
- campos tipicos:
  - title
  - aliases
  - summary
  - key_points
  - faq

5.3 Pasta knowledge/evals/
- casos de avaliacao versionados
- servem para verificar se os companions ainda respondem corretamente

5.4 RAG
- usado como fallback quando nao ha resposta deterministicamente suficiente
- o indice depende do backend configurado
- quando documentos mudam, o caminho RAG beneficia de reindexacao
- os companions nao dependem do indice vetorial para existir


6. MEMORIA E FEEDBACK

6.1 Approved memory
- se uma resposta aprovada for muito semelhante a uma pergunta nova
- o bot pode reutilizar a resposta e as citacoes

6.2 Review guard
- se uma resposta muito semelhante foi marcada para revisao
- e nao houver correcao nem grounding documental suficiente
- o bot nao repete a mesma resposta como se estivesse validada

6.3 Review correction memory
- se uma resposta em revisao tiver resposta corrigida
- o bot pode reutilizar essa correcao em perguntas muito parecidas

6.4 Feedback positivo como pista
- respostas aprovadas com citacoes documentais podem ajudar a sugerir o documento certo em perguntas futuras semelhantes


7. APRENDIZAGEM SUPERVISIONADA

O sistema nao faz fine-tuning automatico do modelo.
O que faz e:
- guardar feedback aprovado
- guardar respostas revistas
- guardar resposta corrigida pelo operador
- transformar correcoes em casos de avaliacao supervisionados

Fluxo atual:
- site ou WhatsApp marcam uma resposta com 👎
- o operador fornece a resposta correta
- o backend guarda:
  - nota
  - resposta corrigida
  - documento base
- isso cria ou atualiza um registo persistente em feedback_eval_cases
- o runner de evals passa a incluir esse caso


8. EVALS E TRACKING

8.1 Evals estaticas
- ficheiros em knowledge/evals/*.json

8.2 Evals de operador
- geradas a partir de correcoes supervisionadas
- persistidas no storage da aplicacao

8.3 Runner
- scripts/run_knowledge_evals.py
- combina evals estaticas com evals persistidas
- faz dedupe
- mede o companion atual contra a resposta esperada

8.4 Painel admin
- pagina /admin/bot
- mostra:
  - pass rate
  - numero de correcoes persistidas
  - companions resolvidos
  - origem das correcoes
  - falhas atuais
  - correcoes recentes
  - cobertura por documento


9. WHATSAPP - COMPORTAMENTO ESPECIFICO

9.1 Conta inbound
- cada numero pode originar um utilizador local tipo whatsapp-<numero>@pragtico.local
- depois pode ser convertido/ajustado pelo admin

9.2 Welcome
- pode ser enviada uma welcome automatica uma vez por contacto

9.3 Feedback
- reacao 👍 aprova
- reacao 👎 marca revisao e abre captura de resposta correta
- a correcao passa para a mesma memoria supervisionada usada pelo site

9.4 Limite importante
- se estiver a ser usado numero de teste da Meta
- a whitelist/allowed list da Meta continua externa ao portal


10. O QUE O BOT JA FAZ BEM

- perguntas sobre regras com documento bem identificado
- follow-ups do tipo "o que diz o IT-036?"
- respostas baseadas em companions curados
- planeamento de fontes antes de responder
- distinguir live direto de live usado como input para raciocinio
- combinar contexto live com documento quando a pergunta pede os dois
- reutilizacao de respostas aprovadas
- bloqueio de respostas revistas sem validacao
- aproveitamento de correcoes do operador
- unificacao do aprendizado entre site e WhatsApp


11. O QUE AINDA DEPENDE DE MELHORIAS CONTINUAS

- documentos sem companion manual continuam mais dependentes do fallback automatico
- perguntas muito ambiguas podem precisar de mais aliases/FAQ
- algumas perguntas complexas ainda podem beneficiar de mais afinacao do planner
- quando um documento muda de conteudo, o companion manual pode precisar de ser revisto
- o caminho RAG continua a depender de indice atualizado para o fallback semantico
- correcoes do operador devem continuar reconciliadas com o documento base


12. FICHEIROS-CHAVE

- core/chat_runtime.py
  Orquestracao principal do turno de chat

- blueprints/chat.py
  Fluxo web do chat e feedback

- blueprints/whatsapp.py
  Fluxo WhatsApp, reacoes, welcome, correcoes

- integrations/rag_engine.py
  Recuperacao documental e chamada LLM

- domain/knowledge_companions.py
  Carregamento, matching e resposta por companions

- core/chat_feedback.py
  Conversao de correcoes supervisionadas em evals

- domain/knowledge_evals.py
  Runner logico das avaliacoes

- scripts/run_knowledge_evals.py
  Execucao local do conjunto de evals

- templates/admin_bot.html
  Painel de acompanhamento do bot


13. COMO AVALIAR O ESTADO ATUAL

Para avaliar o bot hoje, o caminho mais util e:
1. ver /admin/bot
2. correr scripts/run_knowledge_evals.py
3. rever conversas com 👍 e 👎
4. transformar falhas reais em correcoes supervisionadas
5. acompanhar se o documento passa no ciclo seguinte


14. RESUMO EXECUTIVO

O bot atual ja nao depende apenas de "perguntar ao LLM".
Ele cruza:
- memoria aprovada
- bloqueio de respostas revistas
- correcoes supervisionadas
- targeting documental
- companions estruturados
- RAG com contexto operacional

Isto torna o comportamento muito mais controlavel, auditavel e melhoravel ao longo do tempo.
