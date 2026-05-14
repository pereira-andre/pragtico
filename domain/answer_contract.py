from __future__ import annotations

import re


RESPONSE_CONTRACT_VERSION = "2026-05-13"

AUTHORITY_LAYERS: tuple[dict[str, str], ...] = (
    {
        "key": "deterministic_rules",
        "label": "Regras determinísticas e validações do portal",
        "description": "capacidade de cais, limites rígidos, comandos, cálculos e validações estruturadas",
    },
    {
        "key": "live_data",
        "label": "Dados vivos do portal e serviços externos",
        "description": "navios em porto, planeamento, marés, meteorologia, ondulação e avisos",
    },
    {
        "key": "work_instructions",
        "label": "IT, perfis de cais e knowledge curada",
        "description": "limites, janelas, calados, comprimentos, notas práticas e regras documentais",
    },
    {
        "key": "operator_memory",
        "label": "Memória validada por operador",
        "description": "apenas feedback explicitamente promovido para memória reutilizável",
    },
    {
        "key": "qa_memory",
        "label": "Memória QA curada",
        "description": "casos de regressão para orientar raciocínio e evitar deslizes, nunca texto final pronto",
    },
    {
        "key": "style_contract",
        "label": "Contrato de estilo e prudência",
        "description": "tom cordial, rigoroso, fundamentado, com um emoji contextual no máximo",
    },
)

RESPONSE_OBLIGATIONS: tuple[str, ...] = (
    "Começar por uma conclusão prática quando a pergunta pede decisão, compatibilidade, hora, quantidade ou risco.",
    "Dar pelo menos uma frase de fundamentação: premissas, regra aplicada, cálculo ou dado consultado.",
    "Nunca responder só com um número em temas críticos como calado, maré, rebocadores, cais, reponto, meteorologia ou compatibilidade.",
    "Quando faltar dado crítico, dizer exatamente o que falta e dar uma resposta condicionada por cenários claros.",
    "Não copiar literalmente Q&A, feedback, respostas antigas ou excertos; sintetizar e reconciliar com fontes mais fortes.",
    "Se houver conflito entre fontes, privilegiar a hierarquia e explicar a diferença sem inventar valores.",
    "Manter português europeu, tom cordial e operacional; usar no máximo um emoji contextual por resposta.",
)

FEEDBACK_PIPELINE_RULES: tuple[str, ...] = (
    "Feedback bruto ou em triagem não é fonte de verdade.",
    "Destino eval serve para regressão/teste; não deve influenciar a resposta como memória.",
    "Destino source_update ou rule_update exige alteração da fonte ou regra estruturada antes de ser reutilizado.",
    "Só feedback com destino memory e estado aprovado/corrigido pode entrar na síntese como memória operacional.",
)

CRITICAL_TOPIC_RE = re.compile(
    r"\b("
    r"calado|loa|comprimento|boca|cais|terminal|slot|duque|d['’]alba|"
    r"reponto|mar[eé]|preia|baixa|fundeadouro|rebocador|reboque|"
    r"vento|nevoeiro|ondula[cç][aã]o|visibilidade|imo|lisnave|"
    r"tanquisado|eco[-\s]?oil|teporset|secil|sapec|tms|autoeuropa"
    r")\b",
    flags=re.IGNORECASE,
)


def _clean_text(value: object, *, max_chars: int = 360) -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 1].rstrip() + "..."


def is_critical_operational_question(question: str) -> bool:
    return bool(CRITICAL_TOPIC_RE.search(question or ""))


def format_authority_hierarchy() -> str:
    lines = ["Hierarquia de autoridade da resposta:"]
    for index, layer in enumerate(AUTHORITY_LAYERS, start=1):
        lines.append(f"{index}. {layer['label']}: {layer['description']}.")
    return "\n".join(lines)


def format_response_contract(question: str = "") -> str:
    lines = [
        f"Contrato de resposta operacional PRAGtico v{RESPONSE_CONTRACT_VERSION}.",
        format_authority_hierarchy(),
        "Obrigações de resposta:",
    ]
    lines.extend(f"- {item}" for item in RESPONSE_OBLIGATIONS)
    lines.append("Governação de feedback:")
    lines.extend(f"- {item}" for item in FEEDBACK_PIPELINE_RULES)
    if is_critical_operational_question(question):
        lines.append(
            "Pergunta classificada como crítica: exigir conclusão, fundamento e limites/condições; "
            "não responder de forma telegráfica."
        )
    return "\n".join(lines)


def build_response_contract_source(question: str) -> dict:
    snippet = "\n".join(
        [
            format_response_contract(question),
            f"Pergunta atual: {_clean_text(question, max_chars=300)}",
        ]
    )
    return {
        "source_id": "CONTRACT1",
        "document": "contrato_resposta_operacional",
        "chunk_id": 1,
        "score": 1.0,
        "retrieval_mode": "response_contract",
        "snippet": snippet,
        "text": snippet,
    }


def answer_contract_trace(question: str = "") -> dict:
    return {
        "version": RESPONSE_CONTRACT_VERSION,
        "critical_question": is_critical_operational_question(question),
        "authority_order": [layer["key"] for layer in AUTHORITY_LAYERS],
        "feedback_reuse": "only destination=memory with approved/corrected status",
    }
