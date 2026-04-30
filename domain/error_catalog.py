from __future__ import annotations

import json
import re
from typing import Any


_ERROR_REF_RE = re.compile(r"^#ERR-\d{4}\b")


ERROR_DEFINITIONS: dict[str, dict[str, Any]] = {
    # =====================================================================
    # 1xxx — Validação de input e formulários
    # =====================================================================
    "EMPTY_QUESTION": {
        "code": 1001,
        "category": "validation",
        "message": "Pergunta vazia.",
        "user_message": "Escreve a pergunta ou comando que queres enviar.",
    },
    "REQUIRED_FIELD": {
        "code": 1010,
        "category": "validation",
        "message": "Campo obrigatório em falta.",
        "user_message": "Preenche o campo indicado.",
    },
    "TEXT_TOO_LONG": {
        "code": 1011,
        "category": "validation",
        "message": "Texto excede o limite de caracteres.",
        "user_message": "Reduz o texto para o limite permitido.",
    },
    "INVALID_NUMBER": {
        "code": 1020,
        "category": "validation",
        "message": "Valor numérico inválido.",
        "user_message": "Usa um valor numérico válido.",
    },
    "NUMBER_OUT_OF_RANGE": {
        "code": 1021,
        "category": "validation",
        "message": "Valor fora do intervalo permitido.",
        "user_message": "Corrige o valor para o intervalo válido.",
    },
    "INVALID_TUG_COUNT": {
        "code": 1022,
        "category": "validation",
        "message": "Número de rebocadores inválido.",
        "user_message": "Número de rebocadores deve ser inteiro entre 0 e 10.",
    },
    "INVALID_DATETIME": {
        "code": 1030,
        "category": "validation",
        "message": "Data/hora inválida.",
        "user_message": "Usa data e hora válidas.",
    },
    "DATETIME_IN_PAST": {
        "code": 1031,
        "category": "validation",
        "message": "Data/hora no passado.",
        "user_message": "Usa uma data futura.",
    },
    "DATETIME_RANGE_INVALID": {
        "code": 1032,
        "category": "validation",
        "message": "Fim deve ser posterior ao início.",
        "user_message": "Corrige a ordem dos tempos.",
    },
    "INVALID_THRUSTER": {
        "code": 1040,
        "category": "validation",
        "message": "Valor de thruster inválido.",
        "user_message": "Usa sim, não ou desconhecido.",
    },
    "INVALID_EMAIL": {
        "code": 1050,
        "category": "validation",
        "message": "Email inválido.",
        "user_message": "Usa um email válido.",
    },
    "INVALID_PHONE": {
        "code": 1051,
        "category": "validation",
        "message": "Telefone inválido.",
        "user_message": "Usa um telefone válido.",
    },
    "INVALID_WHATSAPP_PHONE": {
        "code": 1052,
        "category": "validation",
        "message": "Número WhatsApp inválido.",
        "user_message": "Usa número internacional válido (ex.: 351912345678).",
    },
    "WHATSAPP_PHONE_REQUIRED": {
        "code": 1053,
        "category": "validation",
        "message": "WhatsApp ativo sem número.",
        "user_message": "Se ativares WhatsApp, tens de indicar o respetivo número.",
    },
    "INVALID_PASSWORD": {
        "code": 1060,
        "category": "validation",
        "message": "Password demasiado curta.",
        "user_message": "A password deve ter pelo menos 6 caracteres.",
    },
    "INVALID_IMO": {
        "code": 1070,
        "category": "validation",
        "message": "IMO inválido.",
        "user_message": "O IMO deve ter 7 dígitos.",
    },
    "INVALID_BERTH": {
        "code": 1080,
        "category": "validation",
        "message": "Cais/fundeadouro não reconhecido.",
        "user_message": "Usa um dos cais/fundeadouros conhecidos do porto.",
    },
    "BERTH_SAME_AS_ORIGIN": {
        "code": 1081,
        "category": "validation",
        "message": "Destino igual à origem.",
        "user_message": "O cais destino tem de ser diferente do local atual do navio.",
    },
    "BERTH_OCCUPIED": {
        "code": 1082,
        "category": "validation",
        "message": "Cais ocupado.",
        "user_message": "O cais destino está ocupado por outro navio.",
    },
    "INVALID_JSON": {
        "code": 1090,
        "category": "validation",
        "message": "JSON inválido.",
        "user_message": "Verifica a sintaxe do JSON.",
    },
    "JSON_MISSING": {
        "code": 1091,
        "category": "validation",
        "message": "JSON em falta.",
        "user_message": "Indica o JSON ou carrega um ficheiro .json.",
    },
    "JSON_WRONG_STRUCTURE": {
        "code": 1092,
        "category": "validation",
        "message": "Estrutura JSON incorreta.",
        "user_message": "Verifica o formato esperado do JSON.",
    },
    "INVALID_GT": {
        "code": 1100,
        "category": "validation",
        "message": "GT inválido.",
        "user_message": "GT tem de ser um número positivo.",
    },
    "MISSING_VESSEL_PROFILE": {
        "code": 1094,
        "category": "validation",
        "message": "Dados do navio incompletos.",
        "user_message": "Preenche todos os dados obrigatórios do navio.",
    },
    "MISSING_OPERATIONAL_PROFILE": {
        "code": 1095,
        "category": "validation",
        "message": "Dados operacionais incompletos.",
        "user_message": "Preenche cais, último porto e próximo destino.",
    },
    "INVALID_LOCATION": {
        "code": 1110,
        "category": "validation",
        "message": "Coordenadas de localização inválidas.",
        "user_message": "Envia localização GPS válida.",
    },

    # =====================================================================
    # 2xxx — Autenticação, sessão e permissões
    # =====================================================================
    "INVALID_CREDENTIALS": {
        "code": 2001,
        "category": "auth",
        "message": "Credenciais inválidas.",
        "user_message": "Credenciais inválidas.",
    },
    "SESSION_EXPIRED": {
        "code": 2002,
        "category": "auth",
        "message": "Sessão expirada.",
        "user_message": "Sessão expirada. Inicia sessão novamente.",
    },
    "PROFILE_INCOMPLETE": {
        "code": 2003,
        "category": "auth",
        "message": "Perfil incompleto.",
        "user_message": "Completa o teu perfil operacional antes de continuar.",
    },
    "PROFILE_FIELDS_REQUIRED": {
        "code": 2004,
        "category": "auth",
        "message": "Campos do perfil obrigatórios.",
        "user_message": "Nome, agência/entidade, email e telefone são obrigatórios.",
    },
    "PERMISSION_DENIED": {
        "code": 2010,
        "category": "permission",
        "message": "Sem permissão.",
        "user_message": "Não tens permissão para esta ação.",
    },
    "AGENCY_NOT_SET": {
        "code": 2011,
        "category": "permission",
        "message": "Agência não definida.",
        "user_message": "O perfil do agente tem de ter uma agência definida.",
    },
    "AGENCY_MISMATCH": {
        "code": 2012,
        "category": "permission",
        "message": "Escala de outra agência.",
        "user_message": "Esta escala pertence a outra agência.",
    },
    "ADMIN_REGISTER_BLOCKED": {
        "code": 2013,
        "category": "permission",
        "message": "Registo admin bloqueado.",
        "user_message": "O perfil admin deve ser atribuído fora do registo público.",
    },
    "CSRF_FAILED": {
        "code": 2020,
        "category": "security",
        "message": "Validação CSRF falhou.",
        "user_message": "Pedido inválido. Recarrega a página e tenta novamente.",
    },
    "RATE_LIMITED": {
        "code": 2021,
        "category": "security",
        "message": "Rate limit excedido.",
        "user_message": "Demasiados pedidos. Aguarda e tenta novamente.",
    },

    # =====================================================================
    # 3xxx — Regras de negócio (escalas e manobras)
    # =====================================================================
    "SCALE_NOT_FOUND": {
        "code": 3001,
        "category": "business_rule",
        "message": "Escala não encontrada.",
        "user_message": "Escala não encontrada.",
    },
    "SCALE_MISSING_ENTRY": {
        "code": 3002,
        "category": "business_rule",
        "message": "Escala sem entrada associada.",
        "user_message": "Escala sem manobra de entrada associada.",
    },
    "DUPLICATE_IMO_ACTIVE": {
        "code": 3003,
        "category": "business_rule",
        "message": "IMO duplicado em escala ativa.",
        "user_message": "Já existe uma escala ativa com o IMO",
    },
    "DUPLICATE_CALLSIGN_ACTIVE": {
        "code": 3004,
        "category": "business_rule",
        "message": "Indicativo duplicado em escala ativa.",
        "user_message": "Já existe uma escala ativa com o indicativo",
    },
    "APPROVE_ALREADY_EXECUTED": {
        "code": 3010,
        "category": "business_rule",
        "message": "Manobra já executada.",
        "user_message": "Só podes aprovar manobras ainda não executadas.",
    },
    "NO_PENDING_APPROVAL": {
        "code": 3011,
        "category": "business_rule",
        "message": "Sem manobra pendente.",
        "user_message": "Não existe manobra pendente para aprovar.",
    },
    "SHIFT_APPROVE_INVALID": {
        "code": 3012,
        "category": "business_rule",
        "message": "Mudança não aprovável.",
        "user_message": "Só podes aprovar mudanças ainda não executadas.",
    },
    "ABORT_AGENT_ON_APPROVED": {
        "code": 3020,
        "category": "business_rule",
        "message": "Agente não pode abortar aprovada.",
        "user_message": "Só o piloto ou admin pode abortar uma manobra já aprovada.",
    },
    "ABORT_PILOT_ON_PENDING": {
        "code": 3021,
        "category": "business_rule",
        "message": "Piloto não pode cancelar pendente.",
        "user_message": "Manobra ainda pendente. Só o agente ou admin pode cancelar antes da aprovação.",
    },
    "ABORT_AGENT_ON_APPROVED_DEP": {
        "code": 3022,
        "category": "business_rule",
        "message": "Agente não pode abortar saída aprovada.",
        "user_message": "Só o piloto ou admin pode abortar uma saída já aprovada.",
    },
    "ABORT_PILOT_ON_PENDING_DEP": {
        "code": 3023,
        "category": "business_rule",
        "message": "Piloto não pode cancelar saída pendente.",
        "user_message": "Saída ainda pendente. Só o agente ou admin pode cancelar antes da aprovação.",
    },
    "ABORT_AGENT_ON_APPROVED_SHIFT": {
        "code": 3024,
        "category": "business_rule",
        "message": "Agente não pode abortar mudança aprovada.",
        "user_message": "Só o piloto ou admin pode abortar uma mudança já aprovada.",
    },
    "ABORT_PILOT_ON_PENDING_SHIFT": {
        "code": 3025,
        "category": "business_rule",
        "message": "Piloto não pode cancelar mudança pendente.",
        "user_message": "Mudança ainda pendente. Só o agente ou admin pode cancelar antes da aprovação.",
    },
    "ABORT_ALREADY_EXECUTED": {
        "code": 3026,
        "category": "business_rule",
        "message": "Manobra já executada.",
        "user_message": "Só podes abortar manobras ainda não executadas.",
    },
    "ABORT_TOO_LATE_ENTRY": {
        "code": 3027,
        "category": "business_rule",
        "message": "Aborto demasiado tardio.",
        "user_message": "A manobra só pode ser abortada com pelo menos 2 horas de antecedência.",
    },
    "NO_DEPARTURE_TO_ABORT": {
        "code": 3028,
        "category": "business_rule",
        "message": "Sem saída para abortar.",
        "user_message": "Não existe manobra de saída planeada para este navio.",
    },
    "ABORT_TOO_LATE_DEP": {
        "code": 3029,
        "category": "business_rule",
        "message": "Aborto de saída demasiado tardio.",
        "user_message": "A saída só pode ser abortada com pelo menos 1 hora de antecedência.",
    },
    "NO_SHIFT_TO_ABORT": {
        "code": 3030,
        "category": "business_rule",
        "message": "Sem mudança para abortar.",
        "user_message": "Não existe manobra de mudança planeada para este navio.",
    },
    "ABORT_TOO_LATE_SHIFT": {
        "code": 3031,
        "category": "business_rule",
        "message": "Aborto de mudança demasiado tardio.",
        "user_message": "A mudança só pode ser abortada com pelo menos 1 hora de antecedência.",
    },
    "DEPARTURE_NOT_IN_PORT": {
        "code": 3040,
        "category": "business_rule",
        "message": "Navio não está em porto.",
        "user_message": "Só podes planear saída para escalas previstas ou navios em porto.",
    },
    "DEPARTURE_ALREADY_ACTIVE": {
        "code": 3041,
        "category": "business_rule",
        "message": "Já existe saída ativa.",
        "user_message": "Já existe uma saída ativa para esta escala.",
    },
    "SHIFT_NOT_IN_PORT": {
        "code": 3042,
        "category": "business_rule",
        "message": "Navio não está em porto.",
        "user_message": "Só podes planear mudança para escalas previstas ou navios em porto.",
    },
    "SHIFT_ALREADY_ACTIVE": {
        "code": 3043,
        "category": "business_rule",
        "message": "Já existe mudança ativa.",
        "user_message": "Já existe uma mudança ativa para esta escala.",
    },
    "ENTRY_AUTO_CREATED": {
        "code": 3044,
        "category": "business_rule",
        "message": "Entrada é automática.",
        "user_message": "A entrada inicial já fica criada quando registas a escala.",
    },
    "PILOT_HOUR_CAPACITY": {
        "code": 3045,
        "category": "business_rule",
        "message": "Limite de pilotos atingido.",
        "user_message": "Já existem 4 manobras aprovadas para esta hora. Ajusta a hora ou valida outra manobra.",
    },
    "DEPARTURE_APPROVAL_BEFORE_ENTRY": {
        "code": 3046,
        "category": "business_rule",
        "message": "Saída antes de entrada concluída.",
        "user_message": "A saída só pode ser aprovada depois da entrada estar concluída.",
    },
    "SHIFT_APPROVAL_BEFORE_ENTRY": {
        "code": 3047,
        "category": "business_rule",
        "message": "Mudança antes de entrada concluída.",
        "user_message": "A mudança só pode ser aprovada depois da entrada estar concluída.",
    },
    "ARRIVAL_NOT_APPROVED": {
        "code": 3050,
        "category": "business_rule",
        "message": "Entrada não aprovada.",
        "user_message": "Só podes confirmar entrada de manobras previstas.",
    },
    "DEPARTURE_NOT_APPROVED": {
        "code": 3051,
        "category": "business_rule",
        "message": "Saída não aprovada.",
        "user_message": "Só podes registar saída de navios que estão em porto e com manobra aprovada.",
    },
    "SHIFT_NOT_APPROVED": {
        "code": 3052,
        "category": "business_rule",
        "message": "Mudança não aprovada.",
        "user_message": "A mudança tem de estar aprovada antes de ser concluída.",
    },
    "REPORT_NOT_READY": {
        "code": 3060,
        "category": "business_rule",
        "message": "Manobra não pronta para registo.",
        "user_message": "Só podes registar depois da manobra estar aprovada ou concluída.",
    },
    "REPORT_WRONG_TYPE": {
        "code": 3061,
        "category": "business_rule",
        "message": "ID não corresponde ao tipo.",
        "user_message": "O ID indicado não corresponde ao tipo de manobra esperado.",
    },
    "REPORT_ALREADY_EXISTS": {
        "code": 3062,
        "category": "business_rule",
        "message": "Registo já existe.",
        "user_message": "Essa manobra já tem registo. Usa editar registo.",
    },
    "REPORT_EDIT_NOT_COMPLETED": {
        "code": 3063,
        "category": "business_rule",
        "message": "Manobra não concluída.",
        "user_message": "Só podes editar o registo de manobras já concluídas.",
    },
    "MANEUVER_NOT_FOUND": {
        "code": 3070,
        "category": "business_rule",
        "message": "Manobra não encontrada.",
        "user_message": "Manobra não encontrada na escala.",
    },
    "EDIT_COMPLETED_NOT_ADMIN": {
        "code": 3071,
        "category": "business_rule",
        "message": "Edição de manobra concluída.",
        "user_message": "A manobra concluída já só pode ser ajustada no registo.",
    },
    "EDIT_APPROVED_NOT_PILOT": {
        "code": 3072,
        "category": "business_rule",
        "message": "Edição de manobra aprovada.",
        "user_message": "Depois de validada, esta manobra só pode ser editada por piloto.",
    },
    "MULTIPLE_MANEUVERS_NEED_ID": {
        "code": 3090,
        "category": "business_rule",
        "message": "Múltiplas manobras — ID necessário.",
        "user_message": "Há várias manobras deste tipo nesta escala. Indica o ID da manobra.",
    },
    "TARGET_MANEUVER_MISSING": {
        "code": 3091,
        "category": "business_rule",
        "message": "Manobra alvo não encontrada.",
        "user_message": "A proposta não identifica a manobra.",
    },
    "TARGET_SCALE_MISSING": {
        "code": 3092,
        "category": "business_rule",
        "message": "Escala alvo não encontrada.",
        "user_message": "A proposta não tem escala associada.",
    },
    "UNSUPPORTED_ACTION": {
        "code": 3093,
        "category": "business_rule",
        "message": "Ação não suportada.",
        "user_message": "Ação operacional não suportada.",
    },
    "CHANGE_REASON_REQUIRED": {
        "code": 3094,
        "category": "business_rule",
        "message": "Motivo da alteração obrigatório.",
        "user_message": "O motivo da alteração é obrigatório.",
    },

    # =====================================================================
    # 4xxx — Documentos, knowledge, RAG
    # =====================================================================
    "DOC_FORMAT_UNSUPPORTED": {
        "code": 4001,
        "category": "document",
        "message": "Formato de documento não suportado.",
        "user_message": "Usa .pdf, .md, .txt, .docx ou .csv.",
    },
    "DOC_PROCESSING_FAILED": {
        "code": 4002,
        "category": "document",
        "message": "Falha ao processar documento.",
        "user_message": "Verifica a integridade do ficheiro.",
    },
    "DOC_EMPTY_CONTENT": {
        "code": 4003,
        "category": "document",
        "message": "Sem texto extraível.",
        "user_message": "Não foi possível extrair texto útil do ficheiro.",
    },
    "DOC_NOT_FOUND": {
        "code": 4004,
        "category": "document",
        "message": "Documento não encontrado.",
        "user_message": "Documento não encontrado.",
    },
    "DOC_CONTENT_EMPTY": {
        "code": 4005,
        "category": "document",
        "message": "Conteúdo vazio.",
        "user_message": "O conteúdo não pode estar vazio.",
    },
    "DOC_NOT_EDITABLE": {
        "code": 4006,
        "category": "document",
        "message": "Ficheiro não editável.",
        "user_message": "Este tipo de ficheiro não pode ser editado no browser.",
    },
    "DOC_NO_FILES_SELECTED": {
        "code": 4010,
        "category": "document",
        "message": "Nenhum ficheiro selecionado.",
        "user_message": "Seleciona pelo menos um ficheiro.",
    },
    "RAG_EMBEDDINGS_UNAVAILABLE": {
        "code": 4020,
        "category": "rag",
        "message": "Embeddings indisponíveis.",
        "user_message": "O motor de embeddings não está configurado.",
    },
    "RAG_REINDEX_IN_PROGRESS": {
        "code": 4022,
        "category": "rag",
        "message": "Reindexação em curso.",
        "user_message": "Já existe uma reindexação em curso.",
    },

    # =====================================================================
    # 5xxx — Bot e chat
    # =====================================================================
    "PENDING_ACTION_FAILED": {
        "code": 5001,
        "category": "business_rule",
        "message": "Não foi possível aplicar a ação operacional.",
        "user_message": "Não foi possível aplicar a ação operacional. Confirma os dados e tenta novamente.",
    },
    "CONVERSATION_NOT_FOUND": {
        "code": 5010,
        "category": "chat",
        "message": "Conversa não encontrada.",
        "user_message": "Conversa não encontrada.",
    },
    "CONVERSATION_INVALID_USER": {
        "code": 5011,
        "category": "chat",
        "message": "Conversa inválida para este utilizador.",
        "user_message": "Conversa inválida para este utilizador.",
    },
    "CONVERSATION_TITLE_EMPTY": {
        "code": 5012,
        "category": "chat",
        "message": "Título vazio.",
        "user_message": "O título da conversa não pode ficar vazio.",
    },
    "NO_PENDING_ACTION_CANCEL": {
        "code": 5020,
        "category": "chat",
        "message": "Sem ação pendente.",
        "user_message": "Não existe ação pendente para cancelar.",
    },
    "NO_PENDING_ACTION_CONFIRM": {
        "code": 5021,
        "category": "chat",
        "message": "Sem ação pendente.",
        "user_message": "Não existe ação pendente para confirmar.",
    },
    "PENDING_ACTION_INCOMPLETE": {
        "code": 5022,
        "category": "chat",
        "message": "Dados obrigatórios em falta.",
        "user_message": "Ainda faltam dados obrigatórios antes de confirmar esta ação.",
    },
    "MESSAGE_NOT_FOUND": {
        "code": 5030,
        "category": "chat",
        "message": "Mensagem não encontrada.",
        "user_message": "Mensagem não encontrada.",
    },
    "INVALID_FEEDBACK_STATE": {
        "code": 5031,
        "category": "chat",
        "message": "Estado de feedback inválido.",
        "user_message": "Estado de feedback inválido.",
    },
    "FEEDBACK_NOT_ASSISTANT": {
        "code": 5032,
        "category": "chat",
        "message": "Feedback só para respostas.",
        "user_message": "Só podes classificar respostas do assistente.",
    },
    "FEEDBACK_REVIEW_NO_REASON": {
        "code": 5033,
        "category": "chat",
        "message": "Revisão sem motivo.",
        "user_message": "Para rever sem reutilizar indica o motivo. Para reutilizar uma correção, usa Corrigir.",
    },
    "CONVERSATION_ID_MISSING": {
        "code": 5060,
        "category": "chat",
        "message": "conversation_id em falta.",
        "user_message": "conversation_id em falta.",
    },
    "LLM_NOT_CONFIGURED": {
        "code": 5050,
        "category": "chat",
        "message": "Provider de geração não configurado.",
        "user_message": "Define a API key do provider antes de usar o chatbot.",
    },

    # =====================================================================
    # 6xxx — WhatsApp
    # =====================================================================
    "WA_WEBHOOK_DISABLED": {
        "code": 6001,
        "category": "whatsapp",
        "message": "Webhook WhatsApp indisponível.",
        "user_message": "WhatsApp webhook indisponível.",
    },
    "WA_VERIFY_FAILED": {
        "code": 6002,
        "category": "whatsapp",
        "message": "Verificação WhatsApp inválida.",
        "user_message": "Verificação inválida.",
    },
    "WA_CREDENTIALS_MISSING": {
        "code": 6010,
        "category": "whatsapp",
        "message": "Credenciais WhatsApp em falta.",
        "user_message": "Credenciais de envio WhatsApp não configuradas.",
    },
    "WA_INVALID_RECIPIENT": {
        "code": 6011,
        "category": "whatsapp",
        "message": "Número de destino inválido.",
        "user_message": "Número de destino inválido.",
    },
    "WA_INVALID_TEMPLATE": {
        "code": 6012,
        "category": "whatsapp",
        "message": "Template WhatsApp inválido.",
        "user_message": "Template WhatsApp inválido.",
    },
    "WA_MEDIA_ID_MISSING": {
        "code": 6013,
        "category": "whatsapp",
        "message": "Media ID em falta.",
        "user_message": "Media ID WhatsApp em falta.",
    },
    "WA_PROFILE_IMAGE_FORMAT": {
        "code": 6015,
        "category": "whatsapp",
        "message": "Formato de imagem inválido.",
        "user_message": "A imagem de perfil deve ser PNG ou JPEG.",
    },

    # =====================================================================
    # 7xxx — Integrações externas
    # =====================================================================
    "LLM_PROVIDER_UNAVAILABLE": {
        "code": 7001,
        "category": "integration",
        "message": "Provider de geração indisponível.",
        "user_message": "Serviço de resposta temporariamente indisponível.",
    },
    "TIDES_FETCH_FAILED": {
        "code": 7010,
        "category": "integration",
        "message": "Falha ao obter marés.",
        "user_message": "Não foi possível obter dados de marés.",
    },
    "WEATHER_FETCH_FAILED": {
        "code": 7011,
        "category": "integration",
        "message": "Falha ao obter meteorologia.",
        "user_message": "Não foi possível obter dados meteorológicos.",
    },
    "WAVE_FETCH_FAILED": {
        "code": 7012,
        "category": "integration",
        "message": "Falha ao obter ondulação.",
        "user_message": "Não foi possível obter dados de ondulação.",
    },
    "WARNINGS_FETCH_FAILED": {
        "code": 7013,
        "category": "integration",
        "message": "Falha ao obter avisos locais.",
        "user_message": "Não foi possível obter avisos à navegação.",
    },

    # =====================================================================
    # 8xxx — Admin e sistema
    # =====================================================================
    "USER_NOT_FOUND": {
        "code": 8001,
        "category": "admin",
        "message": "Utilizador não encontrado.",
        "user_message": "Utilizador não encontrado.",
    },
    "USER_ALREADY_EXISTS": {
        "code": 8002,
        "category": "admin",
        "message": "Utilizador já existe.",
        "user_message": "Esse utilizador já existe.",
    },
    "LAST_ADMIN_DELETE": {
        "code": 8003,
        "category": "admin",
        "message": "Último admin.",
        "user_message": "Não podes apagar o último admin.",
    },
    "SELF_DELETE_BLOCKED": {
        "code": 8004,
        "category": "admin",
        "message": "Auto-eliminação bloqueada.",
        "user_message": "Não podes apagar a tua própria conta enquanto estás autenticado.",
    },
    "INVALID_ROLE": {
        "code": 8005,
        "category": "admin",
        "message": "Role inválido.",
        "user_message": "Role inválido. Usa admin, piloto ou agente.",
    },
    "BACKUP_FORMAT_UNSUPPORTED": {
        "code": 8010,
        "category": "admin",
        "message": "Formato de backup não suportado.",
        "user_message": "Tipo de backup não suportado.",
    },
    "BACKUP_NO_ADMIN": {
        "code": 8011,
        "category": "admin",
        "message": "Backup sem admin.",
        "user_message": "O backup de sistema não contém nenhum utilizador admin.",
    },
    "FEEDBACK_NOTE_REQUIRED": {
        "code": 8030,
        "category": "admin",
        "message": "Nota de justificação obrigatória.",
        "user_message": "Indica uma nota para justificar esta decisão.",
    },
    "INVALID_REPORT_TAG": {
        "code": 8040,
        "category": "admin",
        "message": "Tag/estado de reporte inválido.",
        "user_message": "Escolhe uma tag ou estado válido para o reporte.",
    },
    "FILE_TOO_LARGE": {
        "code": 8080,
        "category": "system",
        "message": "Ficheiro demasiado grande.",
        "user_message": "O ficheiro excede o tamanho máximo permitido.",
    },
    "DB_CONNECTION_FAILED": {
        "code": 8060,
        "category": "system",
        "message": "Falha na ligação à base de dados.",
        "user_message": "Serviço temporariamente indisponível.",
    },
    "EVENT_REPORT_NOT_FOUND": {
        "code": 8075,
        "category": "admin",
        "message": "Reporte de evento não encontrado.",
        "user_message": "Reporte de evento não encontrado.",
    },
    "CASE_NOT_FOUND": {
        "code": 8074,
        "category": "admin",
        "message": "Caso operacional não encontrado.",
        "user_message": "Caso operacional não encontrado.",
    },

    # =====================================================================
    # 9xxx — Erros internos
    # =====================================================================
    "UNHANDLED_EXCEPTION": {
        "code": 9000,
        "category": "internal",
        "message": "Erro inesperado.",
        "user_message": "Erro inesperado. Contacta o suporte com este código.",
    },
    "CHAT_RUNTIME_FAILED": {
        "code": 9001,
        "category": "internal",
        "message": "Não foi possível processar a mensagem.",
        "user_message": "Erro inesperado ao processar a mensagem. Contacta o suporte com este código.",
    },
    "UNEXPECTED_PORTAL_ERROR": {
        "code": 9010,
        "category": "internal",
        "message": "Falha inesperada no portal.",
        "user_message": "Falha inesperada. Tenta novamente.",
    },
    "UNEXPECTED_BOT_ACTION": {
        "code": 9020,
        "category": "internal",
        "message": "Falha na execução de ação do bot.",
        "user_message": "Falha inesperada na execução. Tenta novamente.",
    },
    "MESSAGE_SAVE_FAILED": {
        "code": 9050,
        "category": "internal",
        "message": "Falha ao gravar mensagem.",
        "user_message": "Falha ao gravar mensagem.",
    },
}

# ---- Mapa inverso: mensagem de ValueError → chave do catálogo ----
# Usado para ligar raise ValueError(...) ao catálogo sem alterar cada raise.
_MESSAGE_TO_KEY: dict[str, str] = {}
for _key, _def in ERROR_DEFINITIONS.items():
    _msg = _def.get("user_message", "")
    if _msg and _msg not in _MESSAGE_TO_KEY:
        _MESSAGE_TO_KEY[_msg] = _key


def error_definition(error_key: str) -> dict[str, Any]:
    return ERROR_DEFINITIONS.get(error_key, ERROR_DEFINITIONS["UNHANDLED_EXCEPTION"])


def error_ref(error_key: str) -> str:
    return f"#ERR-{int(error_definition(error_key)['code']):04d}"


def error_payload(
    error_key: str,
    *,
    detail: str = "",
    expose_detail: bool = False,
) -> dict[str, Any]:
    definition = error_definition(error_key)
    payload = {
        "error": definition["message"],
        "error_code": definition["code"],
        "error_ref": error_ref(error_key),
    }
    if detail and expose_detail:
        payload["detail"] = detail
    return payload


def user_error_message(error_key: str, *, detail: str = "", channel: str = "web") -> str:
    definition = error_definition(error_key)
    message = definition.get("user_message") or definition["message"]
    ref = error_ref(error_key)
    if channel == "whatsapp":
        text = f"*{ref}*\n{message}"
    else:
        text = f"{ref} {message}"
    if detail:
        text = f"{text}\nDetalhe: {detail}"
    return text


def log_error_event(logger, error_key: str, *, detail: str = "", **context: Any) -> None:
    definition = error_definition(error_key)
    payload = {
        "level": "error",
        "error_code": definition["code"],
        "error_ref": error_ref(error_key),
        "error_key": error_key,
        "category": definition.get("category", "internal"),
        "message": detail or definition["message"],
        **{key: value for key, value in context.items() if value not in (None, "")},
    }
    logger.error(json.dumps(payload, ensure_ascii=False, default=str))


def resolve_error_key(message: str) -> str | None:
    """Try to find an error key matching a ValueError message string."""
    clean = message.strip().rstrip(".")
    # Exact match first
    if message in _MESSAGE_TO_KEY:
        return _MESSAGE_TO_KEY[message]
    # Try without trailing period
    if clean + "." in _MESSAGE_TO_KEY:
        return _MESSAGE_TO_KEY[clean + "."]
    # Substring match for dynamic messages
    for msg, key in _MESSAGE_TO_KEY.items():
        if msg in message or message in msg:
            return key
    return None


def flash_error_message(message: str) -> str:
    """Prefix a ValueError message with its #ERR-XXXX code if found in the catalog."""
    if _ERROR_REF_RE.match(message.strip()):
        return message
    key = resolve_error_key(message)
    if key:
        return f"{error_ref(key)} {message}"
    return message
