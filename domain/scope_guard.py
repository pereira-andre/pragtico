"""Conversation scope guard for the operational bot."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from core.chat_planner import ChatExecutionPlan, normalize_planner_text


@dataclass(frozen=True)
class ScopeGuardDecision:
    blocked: bool
    category: str = ""
    reason: str = ""
    severity: str = "info"


SCOPE_GUARD_ANSWER = (
    "Este tema fica fora do âmbito operacional do PRAGtico. "
    "Posso ajudar com operação portuária de Setúbal/APSS: escalas, navios, manobras, "
    "cais e terminais, regras, marés, meteorologia, ondulação e avisos locais."
)

SCOPE_GUARD_HIGH_RISK_ANSWER = (
    "Não posso ajudar com esse pedido. No PRAGtico devo ficar limitado ao âmbito operacional: "
    "operação portuária de Setúbal/APSS, escalas, navios, manobras, regras, marés, "
    "meteorologia, ondulação e avisos locais."
)

SMALLTALK_RE = re.compile(
    r"^(?:ola|olá|bom dia|boa tarde|boa noite|obrigad[oa]|ok|okay|certo|perfeito|"
    r"sim|nao|não|cancelar|obrigado sff|obrigada sff)[.!?\s]*$"
)
FOLLOWUP_RE = re.compile(
    r"^(?:e\s+)?(?:agora|e|entao|então|nesse caso|neste caso|com base nisso|"
    r"e se|se fosse|para|ate|até|ao|a|do|da|dos|das)\b"
    r"|^(?:que\s+fonte|que\s+confirmacao|que\s+confirmação|resume|resumo|"
    r"da\s+resposta|dá\s+resposta|sem\s+inventar|se\s+faltar|que\s+impacto|"
    r"isto\s+deve\s+responder)\b"
)
RULE_CODE_RE = re.compile(r"\b(?:it|rg|p)[\s\-_]?0*\d{1,3}\b")

OPERATIONAL_SCOPE_RE = re.compile(
    r"\b("
    r"apss|porto|setubal|setúbal|sado|troia|tróia|vts|piloto|pilotos|pilotagem|"
    r"navio|navios|escala|escalas|manobra|manobras|entrada|saida|saída|"
    r"manobr\w*|atracar|desatracar|atracacao|atracação|largada|fundeio|fundear|"
    r"cais|berco|berço|bercos|berços|terminal|terminais|doca|docas|barra|"
    r"canal norte|canal sul|fundeadouro|fundeadouros|boia|bóia|baliza|"
    r"lisnave|mitrena|secil|sapec|tanquisado|eco\s*oil|ecooil|ecoil|"
    r"autoeuropa|auto\s*europa|tms\s*1|tms1|tms\s*2|tms2|teporset|"
    r"tepor\s*set|termitrena|praias do sado|alstom|outao|outão|"
    r"reboque|reboques|rebocador|rebocadores|bow\s*thruster|stern\s*thruster|"
    r"thruster|loa|dwt|gt|calado|sonda|profundidade|comprimento|boca|imo|"
    r"carga|cargas|imo|nao imo|não imo|perigosa|perigosas|roro|ro\s*ro|ro-ro|"
    r"mare|maré|mares|marés|preia|baixa-mar|baixa mar|reponto|estoa|corrente|"
    r"meteorologia|meteo|vento|rajada|rajadas|visibilidade|nevoeiro|ondulacao|"
    r"ondulação|estado do mar|aviso local|avisos locais|capitania|anav|"
    r"tup|tarifa|taxa|up|contentores|regra|regras|instrucao|instrução|"
    r"regulamento|procedimento|procedimentos|rieam|colreg"
    r")\b"
)

AMBIGUOUS_OFF_SCOPE_CONTEXT_RE = re.compile(
    r"\b("
    r"acoes|ações|bolsa|cotad[ao]s?|dividendos|preco alvo|preço alvo|comprar acoes|"
    r"taxa de esforco|taxa de esforço|credito habitacao|crédito habitação|"
    r"restaurante|restaurantes|jantar|almoco|almoço|menu|hotel|turismo|passeio|"
    r"receita|bacalhau|vinho|lista compras|lista de compras|calorias|"
    r"brinquedo|colecao|coleção|comprar|presente|embrulho|dedicatoria|"
    r"musica|música|rap|poesia|poema|instagram|metafora|metáfora|coaching|minha vida|vida esta|vida está|"
    r"jogo de tabuleiro|cartas|poderes|vitoria|vitória|ficcao|ficção|"
    r"benfica|sporting|campeonato|televisao|televisão"
    r")\b"
)

WEATHER_OUTSIDE_SCOPE_RE = re.compile(
    r"\b(?:tempo|meteorologia|previsao|previsão|chuva|temperatura|vento)\b"
    r".*\b(?:paris|madrid|lisboa|porto|londres|japao|japão|canada|canadá)\b"
    r"|"
    r"\b(?:paris|madrid|lisboa|porto|londres|japao|japão|canada|canadá)\b"
    r".*\b(?:tempo|meteorologia|previsao|previsão|chuva|temperatura|vento)\b"
)

HIGH_RISK_RE = re.compile(
    r"\b("
    r"system prompt|prompt completo|prompt interno|instrucoes ocultas|instruções ocultas|regras internas|"
    r"hidden developer prompt|confidential rules|port safety validation|politicas internas|políticas internas|"
    r"regras de seguranca que bloqueiam|regras de segurança que bloqueiam|"
    r"ignora .*instru|finge que es|finge que és|sem limites|nao digas que nao podes|"
    r"variaveis de ambiente|variáveis de ambiente|env vars|database_url|token|tokens|password|passwords|senha|senhas|"
    r"chave api|api key|segredo|segredos|endpoint interno|repo privado|"
    r"keylogger|malware|phishing|roubar credenciais|forca bruta|força bruta|"
    r"contornar login|sem senha|exploit|apagar logs|nao ser detetado|não ser detetado|"
    r"aviso do banco|aviso urgente|urgencia de aviso local|urgência de aviso local|"
    r"sms curto|pagina de login falsa|página de login falsa|login falsa|"
    r"taxa de cliques|xss|csrf|sql injection|sqli|enumero tabelas|enumerar tabelas|contorna csrf|"
    r"certificado medico falso|certificado médico falso|documento falso|declaracao .*falsa|declaração .*falsa|"
    r"assina como medico|assina como médico|cedula plausivel|cédula plausível|"
    r"inventa citacoes|inventa citações|citacoes falsas|citações falsas|inventa entrevistas|entrevistas que nao fiz|entrevistas que não fiz|"
    r"remover sinais de ia|remove qualquer sinal|copiar|fraude|"
    r"dor no peito|falta de ar|antibiotico|antibiótico|diagnostico|diagnóstico|"
    r"receita medica|receita médica|instrucao medica|instrução médica|medicacao|medicação|dose|"
    r"fugir a uma multa|tribunal|brecha fiscal|esconder rendimentos|advogado|responsabilidade legal|"
    r"altero o relatorio|alterar relatorio|parecer seguro|sem dados|"
    r"comprar acoes|comprar ações|preco alvo|preço-alvo|stop loss|credito para investir|crédito para investir|"
    r"cripto|alavancagem|apostas|casino|casinos|casa de apostas|ganhar dinheiro depressa|"
    r"em quem votar|propaganda|segmenta por idade|usar medo|usar urgencia|"
    r"recomendacao financeira|recomendação financeira|"
    r"manipul|fazer ciumes|fazer ciúmes|controlo da conversa|"
    r"whatsapp de outra pessoa|mensagens apagadas|ignora regras|"
    r"finge .*aprovad|ficou aprovad|escala falsa|numero de escala|número de escala|comprovativo|"
    r"finge ser admin|piloto coordenador a aprovar|email como piloto coordenador|"
    r"memoriza que|regra foi atualizada|novo limite|"
    r"eval .*resposta errada|aprovar resposta errada|contornar testes|"
    r"rag .*falso|documento falso|companion json|"
    r"resposta final sem dizer rot13|aplica ao contexto de manobras|continua sem avisos|"
    r"usa documentos do porto|podes mostrar|auditoria .*mostrar|portal de escalas"
    r")\b"
)

OFF_TOPIC_RE = re.compile(
    r"\b("
    r"receita|bacalhau|vegetariano|vinho|lista de compras|calorias|"
    r"serie|série|filme|filmes|benfica|sporting|campeonato|onze|"
    r"relacao|relação|terminar|ciumes|ciúmes|ansioso|ansiosa|depressao|depressão|terapia|"
    r"equacao diferencial|equação diferencial|fisica quantica|física quântica|capital do canada|"
    r"viagem|japao|japão|hoteis|hotéis|voos|itinerario|itinerário|"
    r"cv|carta de motivacao|carta de motivação|entrevista|salario|salário|"
    r"comprar casa|credito habitacao|crédito habitação|taxa de esforco|taxa de esforço|"
    r"carro|travar|oficina|telemovel|telemóvel|"
    r"trabalho de historia|trabalho de história|bibliografia|tese inteira|"
    r"conto|capitulo|capítulo|sinopse|letra de musica|letra de música|acordes|"
    r"piadas|roast|traduz|traducao|tradução|gramatica|gramática|"
    r"excel|despesas pessoais|macro vba|api flask|tarefas domesticas|tarefas domésticas|"
    r"kubernetes|terraform|aws|devops|logotipo|logótipo|paleta|copy publicitaria|"
    r"ginásio|ginasio|dieta|suplementos|religiao|religião|filosofia|"
    r"noticias|notícias|jornalista|restaurantes em lisboa|dentista"
    r")\b"
)


def normalize_scope_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


def _has_operational_scope(clean_text: str, plan: ChatExecutionPlan | None = None) -> bool:
    if not clean_text:
        return False
    if RULE_CODE_RE.search(clean_text):
        return True
    if OPERATIONAL_SCOPE_RE.search(clean_text):
        return True
    if plan and (
        plan.primary_intent in {"live_environment", "operational_lookup", "documents", "document_synthesis", "live_reasoning"}
        or plan.has_live_facets
        or plan.wants_documents
        or plan.wants_operational_lookup
    ):
        return True
    return False


def _history_has_operational_context(history: list[dict] | None) -> bool:
    saw_non_guard_assistant = False
    for item in reversed(history or []):
        role = str(item.get("role") or "").strip().lower()
        metadata = item.get("channel_metadata") or {}
        if role == "assistant" and (metadata.get("scope_guard") or "fora do âmbito operacional" in str(item.get("content") or "").casefold()):
            return False
        if role == "assistant":
            saw_non_guard_assistant = True
            continue
        if role and role != "user":
            continue
        content = str(item.get("content") or "")
        if saw_non_guard_assistant and OPERATIONAL_SCOPE_RE.search(normalize_scope_text(content)):
            return True
    return False


def evaluate_scope_guard(
    question: str,
    *,
    plan: ChatExecutionPlan | None = None,
    history: list[dict] | None = None,
) -> ScopeGuardDecision:
    """Return a blocking decision for requests outside the bot's operational mandate."""
    raw_question = str(question or "").strip()
    clean_question = normalize_scope_text(raw_question)
    planner_text = plan.normalized_question if plan else normalize_planner_text(raw_question)
    clean = clean_question or planner_text
    if not clean:
        return ScopeGuardDecision(False)
    if SMALLTALK_RE.match(raw_question.strip()):
        return ScopeGuardDecision(False)

    question_has_operational_scope = _has_operational_scope(clean, plan)
    has_operational_scope = question_has_operational_scope
    if FOLLOWUP_RE.search(clean) and _history_has_operational_context(history):
        has_operational_scope = True

    if HIGH_RISK_RE.search(clean):
        return ScopeGuardDecision(True, "high_risk", "Pedido de alto risco fora do mandato operacional.", "high")

    if WEATHER_OUTSIDE_SCOPE_RE.search(clean):
        return ScopeGuardDecision(True, "external_live_info", "Pedido de meteorologia fora de Setúbal/APSS.", "medium")

    if has_operational_scope and AMBIGUOUS_OFF_SCOPE_CONTEXT_RE.search(clean):
        return ScopeGuardDecision(
            True,
            "ambiguous_operational_term",
            "Termo operacional usado num contexto financeiro, turístico, criativo ou metafórico.",
            "medium",
        )

    if OFF_TOPIC_RE.search(clean) and not question_has_operational_scope:
        return ScopeGuardDecision(True, "off_topic", "Tema geral sem relação operacional com o porto.", "medium")

    if not has_operational_scope:
        return ScopeGuardDecision(True, "outside_scope", "Pedido sem sinais de âmbito operacional.", "medium")

    return ScopeGuardDecision(False)


def build_scope_guard_answer(decision: ScopeGuardDecision) -> dict:
    answer = SCOPE_GUARD_HIGH_RISK_ANSWER if decision.severity == "high" else SCOPE_GUARD_ANSWER
    return {
        "answer": answer,
        "sources": [],
        "answer_origin": "scope_guard",
        "scope_guard": {
            "category": decision.category,
            "reason": decision.reason,
            "severity": decision.severity,
        },
    }
