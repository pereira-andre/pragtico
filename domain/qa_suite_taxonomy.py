from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QASuiteDefinition:
    suite_type: str
    label: str
    runtime_policy: str


QA_SUITES: dict[str, QASuiteDefinition] = {
    "command_flow": QASuiteDefinition(
        "command_flow",
        "Comandos e fluxos administrativos",
        "Validar parsing, aliases, autocomplete e respostas determinísticas; não usar como memória factual estática.",
    ),
    "live_data": QASuiteDefinition(
        "live_data",
        "Dados vivos e estado do portal",
        "Testar integração live; não treinar factos estáticos com resultados que mudam.",
    ),
    "tide_transit": QASuiteDefinition(
        "tide_transit",
        "Marés, repontos e tempos de trânsito",
        "Preservar regras temporais e raciocínio de marcação; reconciliar sempre com maré atual.",
    ),
    "berth_capacity": QASuiteDefinition(
        "berth_capacity",
        "Capacidade de cais, slots e dimensões",
        "Validar limites, ocupação multi-slot, folgas e exceções estruturadas.",
    ),
    "tug_guidance": QASuiteDefinition(
        "tug_guidance",
        "Rebocadores e assistência",
        "Usar como regressão de recomendação operacional; exigir condições e dados em falta.",
    ),
    "safety_colreg": QASuiteDefinition(
        "safety_colreg",
        "Segurança, nevoeiro e COLREG/RIEAM",
        "Validar respostas prudentes e fundamentadas; priorizar regras de segurança.",
    ),
    "document_rules": QASuiteDefinition(
        "document_rules",
        "IT, regras documentais e resumos",
        "Testar fidelidade documental; não omitir limites críticos como calados e dimensões.",
    ),
    "answer_quality": QASuiteDefinition(
        "answer_quality",
        "Qualidade, tom e estrutura de resposta",
        "Aplicar contrato de resposta; evitar copy-paste e respostas telegráficas.",
    ),
    "port_culture": QASuiteDefinition(
        "port_culture",
        "Cultura geral de Setúbal",
        "Responder com contexto básico e útil sem interferir com decisões operacionais.",
    ),
    "static_knowledge": QASuiteDefinition(
        "static_knowledge",
        "Conhecimento estático geral",
        "Usar como memória factual estável quando suportado pela knowledge.",
    ),
    "operational_reasoning": QASuiteDefinition(
        "operational_reasoning",
        "Raciocínio operacional geral",
        "Usar como experiência prática, sempre subordinada a regras e fontes atuais.",
    ),
}


def _field(record: Any, name: str) -> str:
    if isinstance(record, dict):
        return str(record.get(name) or "")
    return str(getattr(record, name, "") or "")


def _normalize(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9/]+", " ", normalized.lower()).strip()


def _contains(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def classify_qa_record(record: Any) -> dict[str, str]:
    question = _field(record, "question")
    group = _field(record, "group")
    source = _field(record, "source")
    expected = " ".join(_field(record, name) for name in ("expected", "expected_summary", "answer_origin"))
    question_norm = _normalize(question)
    haystack = _normalize(" ".join([question, group, source, expected]))

    suite_type = "operational_reasoning"
    evidence = "fallback_operational"
    if _contains(question, r"^\s*/|\bautocomplete\b|\bcomandos?\b|\balias(?:es)?\b"):
        suite_type = "command_flow"
        evidence = "slash_or_command"
    elif _contains(haystack, r"\b(it[-\s]?\d{3}|regra\s+\d{3}|resumo\s+da\s+it|instrucao\s+de\s+trabalho)\b"):
        suite_type = "document_rules"
        evidence = "document_rule_terms"
    elif _contains(haystack, r"\b(navios?\s+em\s+porto|porto\s+agora|planeamento|manobras?\s+(planeadas|previstas)|eta|escala|quadro|avisos?|meteorologia|ondulacao|ondulacao|hoje|amanha)\b"):
        suite_type = "live_data"
        evidence = "live_or_time_variant"
    elif _contains(haystack, r"\b(reponto|mare|mares|preia|baixa|fundeadouro|troia|tempo\s+de\s+transito|tempo\s+a\s+chegar)\b"):
        suite_type = "tide_transit"
        evidence = "tide_or_transit"
    elif _contains(haystack, r"\b(tms|cais|slot|comprimento|loa|duque|d alba|autoeuropa|lisnave|tanquisado|eco\s*oil|teporset|secil|sapec|alstom|terminal)\b"):
        suite_type = "berth_capacity"
        evidence = "berth_or_dimension"
    elif _contains(haystack, r"\b(rebocador|rebocadores|reboque|reboques|thruster|dwt)\b"):
        suite_type = "tug_guidance"
        evidence = "tug_terms"
    elif _contains(haystack, r"\b(colreg|rieam|nevoeiro|visibilidade|abalroamento|colisao|ferro|suspensao|emergencia|vento)\b"):
        suite_type = "safety_colreg"
        evidence = "safety_or_colreg_terms"
    elif _contains(haystack, r"\b(resposta\s+direta|so\s+um\s+numero|telegr[aá]fica|emoji|fundamenta|cordial|rigorosa|copy\s*paste)\b"):
        suite_type = "answer_quality"
        evidence = "answer_quality_terms"
    elif _contains(haystack, r"\b(setubal|outao|forte|cidade|historia|comida|choco\s+frito|sado)\b"):
        suite_type = "port_culture"
        evidence = "port_culture_terms"
    elif _contains(question_norm, r"\b(jardas?|milhas?|conversao|conversoes)\b"):
        suite_type = "static_knowledge"
        evidence = "static_conversion"

    definition = QA_SUITES[suite_type]
    return {
        "suite_type": definition.suite_type,
        "suite_label": definition.label,
        "runtime_policy": definition.runtime_policy,
        "classification_evidence": evidence,
    }


def qa_suite_options() -> list[dict[str, str]]:
    return [
        {
            "suite_type": item.suite_type,
            "suite_label": item.label,
            "runtime_policy": item.runtime_policy,
        }
        for item in QA_SUITES.values()
    ]
