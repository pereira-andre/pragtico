from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import List

from domain.document_processing import extract_text_from_path, is_allowed_document


PORTUGUESE_STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "com",
    "como",
    "da",
    "das",
    "de",
    "dentro",
    "do",
    "dos",
    "durante",
    "e",
    "em",
    "essa",
    "esse",
    "esta",
    "este",
    "isso",
    "isto",
    "mais",
    "me",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "ou",
    "para",
    "por",
    "qual",
    "quais",
    "que",
    "se",
    "sem",
    "sff",
    "sobre",
    "uma",
    "um",
    "vai",
}
SUMMARY_REQUEST_RE = re.compile(
    r"\b(o que diz|o que sabes|que sabes|o que conheces|o que me podes dizer|o que podes dizer|fala me|fala-me|resume|resumo|sumario|sumário|"
    r"explica|diz me|diz-me|da me|dá me|da-me|dá-me|"
    r"detalhes|mais detalhes|informacao|informação|informacoes|informações|visao geral|visão geral|"
    r"termos gerais|em geral|quais sao|quais são|qual e a regra|qual é a regra|"
    r"regras|restricoes|restrições|limites|condicoes operacionais|condições operacionais)\b",
    flags=re.IGNORECASE,
)
QUESTION_LINE_RE = re.compile(r"^Pergunta:\s*(.+)$", flags=re.IGNORECASE)
ANSWER_LINE_RE = re.compile(r"^Resposta:\s*(.+)$", flags=re.IGNORECASE)
LOW_SIGNAL_MATCH_TOKENS = {
    "cais",
    "documento",
    "entrada",
    "entrar",
    "estaleiro",
    "estaleiros",
    "lisnave",
    "manobra",
    "manobrar",
    "manobras",
    "mitrena",
    "navio",
    "navios",
    "pode",
    "podem",
    "porto",
    "regra",
    "regras",
    "saida",
    "setubal",
    "teporset",
}
DEPTH_TERMS = {
    "calado",
    "calados",
    "profundidade",
    "profundidades",
    "soleira",
    "sonda",
    "sondas",
    "zh",
}
LENGTH_TIME_TERMS = {
    "comprimento",
    "loa",
    "noite",
    "noturna",
    "noturno",
}
RULE_OR_RESTRICTION_TERMS = {
    "condicao",
    "condicoes",
    "especifica",
    "especificas",
    "limite",
    "limites",
    "operacional",
    "operacionais",
    "regra",
    "regras",
    "restricao",
    "restricoes",
}
OVERVIEW_TERMS = {
    "conheces",
    "detalhe",
    "detalhes",
    "dizer",
    "explica",
    "fala",
    "gerais",
    "geral",
    "informacao",
    "informacoes",
    "resumo",
    "sabes",
    "sumario",
    "termos",
    "visao",
}
SCALAR_FACT_TERMS = (
    DEPTH_TERMS
    | LENGTH_TIME_TERMS
    | {
        "altura",
        "boca",
        "calado",
        "maximo",
        "maxima",
        "metro",
        "metros",
        "minimo",
        "minima",
    }
)
DISTANCE_TERMS = {
    "barra",
    "distancia",
    "distancias",
    "milha",
    "milhas",
}
TIME_PLANNING_TERMS = {
    "antecedencia",
    "horas",
    "notificacao",
    "notificado",
    "prazo",
    "tempo",
    "quando",
}
CONTACT_REFERENCE_TERMS = {
    "canal",
    "telefone",
    "vhf",
}
VALIDATION_TERMS = {
    "quem",
    "requisição",
    "requisições",
    "requisicao",
    "requisicoes",
    "valida",
    "validacao",
    "validação",
}
FACILITY_TERMS = {
    "berco",
    "bercos",
    "cais",
    "doca",
    "docas",
    "instalacao",
    "instalacoes",
    "plataforma",
    "plataformas",
    "terminal",
    "terminais",
}
ANCHORAGE_TERMS = {
    "fundeadouro",
    "fundeadouros",
    "fundeio",
    "ancoradouro",
    "ancoradouros",
}
SPECIFIC_FACILITY_SCOPE_TERMS = {
    "alstom",
    "autoeuropa",
    "eco",
    "ecooil",
    "europa",
    "fundeadouro",
    "fundeadouros",
    "lisnave",
    "mitrena",
    "praias",
    "sado",
    "sapec",
    "secil",
    "tanquisado",
    "tepor",
    "teporset",
    "termitrena",
    "tgl",
    "tms1",
    "tms2",
    "tps",
    "troia",
}
BERTH_INVENTORY_RE = re.compile(
    r"\b(quais|quantos|quantas|existem|lista|listar|enumera|inventario|inventário)\b",
    flags=re.IGNORECASE,
)


def companion_directory(knowledge_dir: str) -> str:
    return os.path.join(knowledge_dir, "companions")


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", without_accents.lower())).strip()


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in _normalize_text(value).split()
        if len(token) > 2 and token not in PORTUGUESE_STOPWORDS and not token.isdigit()
    }


def _numeric_mentions(value: object) -> set[str]:
    numbers: set[str] = set()
    for match in re.finditer(r"\b\d+(?:[.,]\d+)*\b", str(value or "")):
        raw = match.group(0)
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
            normalized = re.sub(r"[.,]", "", raw)
        else:
            normalized = raw.replace(",", ".")
        numbers.add(normalized)
    return numbers


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_answer_text(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    cleaned_lines = [_clean_text(line) for line in raw.splitlines()]
    compact_lines: list[str] = []
    previous_blank = False
    for line in cleaned_lines:
        if not line:
            if compact_lines and not previous_blank:
                compact_lines.append("")
            previous_blank = True
            continue
        compact_lines.append(line)
        previous_blank = False
    return "\n".join(compact_lines).strip()


def _clean_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in (_clean_text(item) for item in value) if item]
    clean = _clean_text(value)
    return [clean] if clean else []


def _document_stem(document_name: str) -> str:
    return os.path.splitext(str(document_name or ""))[0]


def _candidate_companion_paths(document_name: str, knowledge_dir: str) -> list[str]:
    companions_dir = companion_directory(knowledge_dir)
    stem = _document_stem(document_name)
    candidates = [os.path.join(companions_dir, f"{stem}.json")]
    code_match = re.match(r"([A-Z]{1,3})-(\d{3})_", str(document_name or ""), flags=re.IGNORECASE)
    if code_match:
        prefix = code_match.group(1).upper()
        code = code_match.group(2)
        candidates.append(os.path.join(companions_dir, f"{prefix}-{code}.json"))
    return list(dict.fromkeys(candidates))


def _normalize_faq_entry(item: dict) -> dict | None:
    question = _clean_text(item.get("question"))
    answer = _clean_answer_text(item.get("answer"))
    if not question or not answer:
        return None
    keywords = _clean_list(item.get("keywords"))
    if not keywords:
        keywords = sorted(_tokenize(question))[:8]
    source_text = _clean_answer_text(item.get("source_text") or item.get("facts") or answer)
    return {
        "question": question,
        "answer": answer,
        "source_text": source_text,
        "keywords": keywords,
    }


def _derive_aliases(document_name: str, title: str) -> list[str]:
    code_match = re.match(r"([A-Z]{1,3})-(\d{3})", document_name, flags=re.IGNORECASE)
    aliases = [
        document_name,
        _document_stem(document_name),
        title,
    ]
    if code_match:
        prefix = code_match.group(1).upper()
        code = code_match.group(2)
        aliases.append(f"{prefix}-{code}")
        aliases.append(f"{prefix}-{int(code)}")
        aliases.append(code)
    if code_match and "—" in title:
        _left, right = title.split("—", 1)
        aliases.append(right.strip())
    if code_match and "-" in title:
        left, right = title.split("-", 1)
        if re.match(r"^[A-Z]{1,3}\s*\d+", left.strip(), flags=re.IGNORECASE):
            aliases.append(right.strip())
    return [item for item in dict.fromkeys(_clean_text(alias) for alias in aliases) if item]


def _normalize_companion(payload: dict, document_name: str) -> dict:
    title = _clean_text(payload.get("title")) or _document_stem(document_name).replace("_", " ")
    aliases = _clean_list(payload.get("aliases"))
    aliases.extend(_derive_aliases(document_name, title))
    faq_items = []
    for raw_item in payload.get("faq", []) or []:
        if not isinstance(raw_item, dict):
            continue
        normalized = _normalize_faq_entry(raw_item)
        if normalized:
            faq_items.append(normalized)
    return {
        "document": document_name,
        "title": title,
        "aliases": list(dict.fromkeys(item for item in aliases if item)),
        "summary": _clean_text(payload.get("summary")),
        "key_points": _clean_list(payload.get("key_points")),
        "faq": faq_items,
    }


def _read_document_text(document_name: str, knowledge_dir: str) -> str:
    path = os.path.join(knowledge_dir, document_name)
    if not os.path.isfile(path):
        return ""
    return extract_text_from_path(path)


def _parse_document_title(text: str, document_name: str) -> str:
    for raw_line in text.splitlines():
        line = _clean_text(raw_line)
        if not line:
            continue
        if line.lower().startswith("documento:"):
            return line.split(":", 1)[1].strip()
        return line
    return _document_stem(document_name).replace("_", " ")


def _is_separator_line(line: str) -> bool:
    clean = _clean_text(line)
    return bool(clean) and len(set(clean)) == 1 and clean[0] in {"=", "-", "_"}


def _is_heading_line(line: str) -> bool:
    clean = _clean_text(line)
    if not clean or _is_separator_line(clean):
        return False
    alpha_chars = [char for char in clean if char.isalpha()]
    return bool(alpha_chars) and clean == clean.upper()


def _extract_section_paragraph(text: str, heading_pattern: str) -> str:
    lines = [_clean_text(line) for line in text.splitlines()]
    capture = False
    paragraph_lines: list[str] = []
    for line in lines:
        if not line:
            if capture and paragraph_lines:
                break
            continue
        if re.search(heading_pattern, line, flags=re.IGNORECASE):
            capture = True
            if ":" in line:
                _label, remainder = line.split(":", 1)
                remainder = _clean_text(remainder)
                if remainder:
                    paragraph_lines.append(remainder)
            continue
        if not capture:
            continue
        if _is_separator_line(line):
            continue
        if _is_heading_line(line):
            break
        paragraph_lines.append(line)
    return _clean_text(" ".join(paragraph_lines))


def _extract_intro_summary(text: str) -> str:
    summary = _extract_section_paragraph(text, r"\bÂMBITO\b|\bAMBITO\b")
    if summary:
        return summary

    lines = [_clean_text(line) for line in text.splitlines()]
    collected: list[str] = []
    title_line = _parse_document_title(text, "")
    for line in lines:
        if not line or _is_separator_line(line):
            continue
        if line == title_line:
            continue
        if line.lower().startswith(("documento:", "fonte:", "entidade", "revisão", "revisao", "natureza:", "unidades:")):
            continue
        if _is_heading_line(line):
            continue
        collected.append(line)
        if len(" ".join(collected)) >= 260:
            break
    return _clean_text(" ".join(collected))


def _first_sentence(value: str) -> str:
    clean = _clean_text(value)
    if not clean:
        return ""
    match = re.match(r"(.+?[.!?])(?:\s|$)", clean)
    if match:
        return match.group(1).strip()
    return clean


def _best_key_point(value: str) -> str:
    first = _first_sentence(value)
    if len(first) >= 18 and len(first.split()) >= 4:
        return first
    clean = _clean_text(value)
    if len(clean) <= 180:
        return clean
    return clean[:177].rstrip() + "..."


def _extract_plaintext_points(text: str, limit: int = 5) -> list[str]:
    points: list[str] = []
    for raw_line in text.splitlines():
        line = _clean_text(raw_line)
        if not line:
            continue
        if re.match(r"^(?:\d+[\.\)]|[a-z]\.)\s+", line, flags=re.IGNORECASE):
            point = re.sub(r"^(?:\d+[\.\)]|[a-z]\.)\s+", "", line, count=1, flags=re.IGNORECASE).strip()
            if point and not point.endswith(":"):
                points.append(point)
        elif line.startswith(("—", "-", "*")):
            point = line[1:].strip()
            if point and not point.endswith(":"):
                points.append(point)
        if len(points) >= limit:
            break
    return points


def _extract_faq_items(text: str) -> list[dict]:
    faq: list[dict] = []
    current_question = ""
    current_answer_lines: list[str] = []
    collecting_answer = False

    def flush() -> None:
        nonlocal current_question, current_answer_lines, collecting_answer
        answer = _clean_text(" ".join(current_answer_lines))
        if current_question and answer:
            faq.append(
                {
                    "question": current_question,
                    "answer": answer,
                    "keywords": sorted(_tokenize(current_question))[:8],
                }
            )
        current_question = ""
        current_answer_lines = []
        collecting_answer = False

    for raw_line in text.splitlines():
        line = _clean_text(raw_line)
        if not line:
            if collecting_answer and current_answer_lines:
                current_answer_lines.append("")
            continue
        if _is_separator_line(line):
            continue
        if line.upper().startswith("FIM DO DOCUMENTO"):
            break
        question_match = QUESTION_LINE_RE.match(line)
        if question_match:
            flush()
            current_question = question_match.group(1).strip()
            continue
        answer_match = ANSWER_LINE_RE.match(line)
        if answer_match:
            collecting_answer = True
            current_answer_lines.append(answer_match.group(1).strip())
            continue
        if collecting_answer:
            current_answer_lines.append(line)
    flush()
    return faq


def auto_build_document_companion(document_name: str, knowledge_dir: str) -> dict | None:
    text = _read_document_text(document_name, knowledge_dir)
    if not text:
        return None
    title = _parse_document_title(text, document_name)
    faq = _extract_faq_items(text)
    key_points = []
    if faq:
        key_points = [_best_key_point(item["answer"]) for item in faq[:6] if _best_key_point(item["answer"])]
    if not key_points:
        key_points = _extract_plaintext_points(text, limit=6)
    payload = {
        "document": document_name,
        "title": title,
        "aliases": _derive_aliases(document_name, title),
        "summary": _extract_intro_summary(text),
        "key_points": key_points,
        "faq": faq,
    }
    return _normalize_companion(payload, document_name)


def load_document_companion(document_name: str, knowledge_dir: str) -> dict | None:
    for path in _candidate_companion_paths(document_name, knowledge_dir):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return _normalize_companion(payload, document_name)
    return auto_build_document_companion(document_name, knowledge_dir)


def list_document_companions(knowledge_dir: str) -> list[dict]:
    companions: list[dict] = []
    if not os.path.isdir(knowledge_dir):
        return companions
    for entry in sorted(os.listdir(knowledge_dir)):
        if not is_allowed_document(entry):
            continue
        companion = load_document_companion(entry, knowledge_dir)
        if companion:
            companions.append(companion)
    return companions


def build_companion_scaffold(document_name: str, *, title: str = "") -> dict:
    clean_title = _clean_text(title) or _document_stem(document_name).replace("_", " ")
    return {
        "document": document_name,
        "title": clean_title,
        "aliases": [_document_stem(document_name)],
        "summary": "",
        "key_points": [""],
        "faq": [{"question": "", "answer": "", "keywords": []}],
    }


def is_companion_summary_request(question: str) -> bool:
    return bool(SUMMARY_REQUEST_RE.search(str(question or "")))


def _faq_match_signal_tokens(
    question_tokens: set[str],
    faq_tokens: set[str],
    keyword_tokens: set[str],
) -> set[str]:
    overlap = question_tokens & (faq_tokens | keyword_tokens)
    return {
        token
        for token in overlap
        if token not in LOW_SIGNAL_MATCH_TOKENS and not token.isdigit()
    }


def _faq_candidate_text(item: dict) -> str:
    parts = [item.get("question", ""), item.get("answer", "")]
    parts.extend(item.get("keywords", []) or [])
    return " ".join(str(part or "") for part in parts)


def _is_berth_inventory_question(question: str, question_tokens: set[str]) -> bool:
    normalized_question = _normalize_text(question)
    if not BERTH_INVENTORY_RE.search(normalized_question):
        return False
    if not ({"lisnave", "mitrena", "porto", "setubal"} & question_tokens):
        return False
    return bool((FACILITY_TERMS | ANCHORAGE_TERMS) & question_tokens)


def _is_port_wide_facility_inventory_question(question_tokens: set[str]) -> bool:
    return bool({"porto", "setubal"} & question_tokens) and bool(FACILITY_TERMS & question_tokens)


def _is_port_inventory_candidate(candidate_tokens: set[str]) -> bool:
    return bool({"inventario", "instalacoes"} & candidate_tokens) or (
        bool({"porto", "setubal"} & candidate_tokens)
        and bool({"terminal", "terminais"} & candidate_tokens)
        and bool({"cais", "doca", "docas"} & candidate_tokens)
    )


def _is_short_scalar_answer(value: object) -> bool:
    clean = _clean_text(value)
    if not clean or len(clean) > 120 or not re.search(r"\d", clean):
        return False
    return len(_tokenize(clean)) <= 10


def _is_brief_direct_answer(value: object) -> bool:
    clean = _clean_text(value)
    return bool(clean) and len(clean) <= 150 and len(_tokenize(clean)) <= 16


def _ensure_sentence(value: str) -> str:
    clean = _clean_text(value)
    if not clean:
        return ""
    if clean.endswith((".", "!", "?")):
        return clean
    return clean + "."


def _lower_initial_article(value: str) -> str:
    clean = _clean_text(value).rstrip(".")
    if clean.lower().startswith(("o ", "a ")):
        return clean[:1].lower() + clean[1:]
    return clean


def _starts_with_boolean_answer(value: str) -> bool:
    return bool(re.match(r"^(?:sim|não|nao)\b", _normalize_text(value)))


def _declarative_subject_from_question(question: str) -> str:
    clean_question = _clean_text(question).rstrip("?.! ")
    if not clean_question:
        return ""
    subject_match = re.match(r"^qual\s+é\s+(.+)$", clean_question, flags=re.IGNORECASE)
    if subject_match:
        subject = subject_match.group(1).strip()
        return subject[:1].upper() + subject[1:] if subject else ""
    subject_match = re.match(r"^qual\s+o\s+(.+)$", clean_question, flags=re.IGNORECASE)
    if subject_match:
        return f"O {subject_match.group(1).strip()}"
    subject_match = re.match(r"^qual\s+a\s+(.+)$", clean_question, flags=re.IGNORECASE)
    if subject_match:
        return f"A {subject_match.group(1).strip()}"
    return ""


def _polish_declarative_subject(subject: str) -> str:
    clean = _clean_text(subject)
    if not clean:
        return ""
    clean = re.sub(r"\bdurante\s+noite\b", "à noite", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bdurante\s+o\s+dia\b", "de dia", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bpara\s+se\s+manobrar\b", "para manobrar", clean, flags=re.IGNORECASE)
    return clean


def _soften_reused_scalar_explanation(text: str, value: str) -> str:
    clean = _clean_text(text)
    if not clean or not value:
        return clean
    value_pattern = re.escape(_clean_text(value))
    replacements = (
        (rf"\bnavios\s+com\s+loa\s+até\s+{value_pattern}\b", "navios até esse limite de LOA"),
        (rf"\bloa\s+até\s+{value_pattern}\b", "LOA até esse limite"),
        (rf"\bigual\s+ou\s+inferior\s+a\s+{value_pattern}\b", "até esse limite"),
        (rf"\baté\s+{value_pattern}\b", "até esse limite"),
        (rf"\bacima\s+de\s+{value_pattern}\b", "acima desse valor"),
        (rf"\bsuperior(?:es)?\s+a\s+{value_pattern}\b", "acima desse valor"),
        (rf"\bmaior(?:es)?\s+que\s+{value_pattern}\b", "acima desse valor"),
    )
    softened = clean
    for pattern, replacement in replacements:
        softened = re.sub(pattern, replacement, softened, flags=re.IGNORECASE)
    softened = re.sub(r"\btanto\s+de\s+dia\s+como\s+de\s+noite\b", "de dia ou de noite", softened, flags=re.IGNORECASE)
    softened = re.sub(
        r"(^|(?<=[.!?]\s))([a-zà-ÿ])",
        lambda match: f"{match.group(1)}{match.group(2).upper()}",
        softened,
    )
    return softened


def _looks_like_night_loa_limit_case(question: str, answer: str) -> bool:
    combined = _normalize_text(f"{question} {answer}")
    has_length = bool({"comprimento", "loa"} & set(combined.split()))
    has_night = bool({"noite", "noturna", "noturno"} & set(combined.split()))
    return has_length and has_night


def humanize_reused_factual_answer(question: str, answer: str, *, context_label: str = "") -> str:
    clean_answer = _clean_answer_text(answer)
    if not clean_answer:
        return ""

    subject = _polish_declarative_subject(_declarative_subject_from_question(question))
    leading_value_match = re.match(
        r"^([0-9][0-9.,]*(?:\s*(?:m|metros?|milhas(?:\s+náuticas)?|milhas(?:\s+nauticas)?|"
        r"horas?|dias?|minutos?|%|nm)))\.\s+(.+)$",
        clean_answer,
        flags=re.IGNORECASE,
    )
    if leading_value_match and subject:
        value = _clean_text(leading_value_match.group(1))
        if _looks_like_night_loa_limit_case(question, clean_answer):
            profile_reference = (
                f" que consta do perfil operacional da instalação {context_label}"
                if _clean_text(context_label)
                else ""
            )
            return (
                f"{subject} é {value}. "
                f"Esse é o limite noturno de LOA{profile_reference}: "
                "até esse valor, a manobra pode fazer-se em qualquer reponto de maré, "
                "de dia ou de noite. Acima dele, fica limitada ao período diurno."
            )
        explanation = _ensure_sentence(
            _soften_reused_scalar_explanation(leading_value_match.group(2), value)
        )
        return f"{subject} é {value}. {explanation}".strip()

    return clean_answer


def _humanize_companion_faq_answer(question: str, answer: str, *, context_label: str = "") -> str:
    clean_answer = humanize_reused_factual_answer(question, answer, context_label=context_label)
    if not _is_brief_direct_answer(clean_answer):
        return clean_answer

    question_tokens = _tokenize(question)
    answer_sentence = _ensure_sentence(clean_answer)
    if not answer_sentence:
        return clean_answer

    if question_tokens & VALIDATION_TERMS and "piloto coordenador" in _normalize_text(clean_answer):
        return f"A validação fica com {_lower_initial_article(clean_answer)}."

    if _starts_with_boolean_answer(clean_answer):
        return f"Neste caso, a resposta é: {answer_sentence}"

    if question_tokens & CONTACT_REFERENCE_TERMS:
        return f"Usa esta referência: {answer_sentence}"

    if question_tokens & TIME_PLANNING_TERMS:
        return (
            f"Para planeamento, conta com {answer_sentence} "
            "Depois valida os restantes condicionantes da manobra."
        )

    if question_tokens & DISTANCE_TERMS:
        return (
            f"Para planeamento, considera {answer_sentence} "
            "Confirma sempre o ponto de origem e destino da pergunta."
        )

    if question_tokens & (SCALAR_FACT_TERMS | RULE_OR_RESTRICTION_TERMS):
        return (
            f"O valor a reter é {answer_sentence} "
            "Usa-o como referência documental e confirma o resto da regra antes de concluir se a manobra é viável."
        )

    return f"A resposta direta: {answer_sentence}"


def _faq_intent_conflicts(question: str, question_tokens: set[str], item: dict) -> bool:
    candidate_tokens = _tokenize(_faq_candidate_text(item))

    asks_rules_or_restrictions = bool(question_tokens & RULE_OR_RESTRICTION_TERMS)
    asks_overview = bool(question_tokens & OVERVIEW_TERMS)
    asks_specific_scalar_fact = bool(question_tokens & SCALAR_FACT_TERMS)
    candidate_has_rules_or_restrictions = bool(candidate_tokens & RULE_OR_RESTRICTION_TERMS)
    candidate_has_overview = bool(candidate_tokens & OVERVIEW_TERMS)
    if (
        asks_overview
        and not asks_rules_or_restrictions
        and not asks_specific_scalar_fact
        and not candidate_has_overview
    ):
        return True
    if (
        asks_rules_or_restrictions
        and not asks_specific_scalar_fact
        and not candidate_has_rules_or_restrictions
        and _is_brief_direct_answer(item.get("answer"))
    ):
        return True
    if (
        (asks_overview or (asks_rules_or_restrictions and not asks_specific_scalar_fact))
        and not candidate_has_rules_or_restrictions
        and bool(candidate_tokens & SCALAR_FACT_TERMS)
        and _is_short_scalar_answer(item.get("answer"))
    ):
        return True

    asks_depth = bool(question_tokens & DEPTH_TERMS)
    candidate_has_depth = bool(candidate_tokens & DEPTH_TERMS)
    candidate_is_length_time = bool(candidate_tokens & LENGTH_TIME_TERMS)
    if asks_depth and candidate_is_length_time and not candidate_has_depth:
        return True

    if _is_berth_inventory_question(question, question_tokens):
        asked_facility_terms = FACILITY_TERMS & question_tokens
        asked_anchorage_terms = ANCHORAGE_TERMS & question_tokens
        is_port_inventory_candidate = _is_port_inventory_candidate(candidate_tokens)
        if not is_port_inventory_candidate:
            if asked_facility_terms and candidate_tokens & ANCHORAGE_TERMS and not asked_anchorage_terms:
                return True
            if asked_anchorage_terms and candidate_tokens & FACILITY_TERMS and not asked_facility_terms:
                return True
        if {"cais", "doca"} & asked_facility_terms and not (
            "cais" in candidate_tokens and bool({"doca", "docas"} & candidate_tokens)
        ):
            return True
        if {"terminal", "terminais"} & asked_facility_terms and not (
            {"terminal", "terminais"} & candidate_tokens
        ):
            return True
        if "plataformas" in asked_facility_terms and "plataformas" not in candidate_tokens:
            return True
        if (
            _is_port_wide_facility_inventory_question(question_tokens)
            and not question_tokens & SPECIFIC_FACILITY_SCOPE_TERMS
            and candidate_tokens & SPECIFIC_FACILITY_SCOPE_TERMS
            and not is_port_inventory_candidate
        ):
            return True

    return False


def find_best_companion_faq(question: str, companion: dict) -> dict | None:
    question_tokens = _tokenize(question)
    if not question_tokens:
        return None
    question_numbers = _numeric_mentions(question)

    best_match = None
    best_score = 0.0
    for item in companion.get("faq", []) or []:
        faq_tokens = _tokenize(item.get("question", ""))
        keyword_tokens = set()
        for keyword in item.get("keywords", []) or []:
            keyword_tokens.update(_tokenize(keyword))
        signal_tokens = _faq_match_signal_tokens(question_tokens, faq_tokens, keyword_tokens)
        if not signal_tokens:
            continue
        if _faq_intent_conflicts(question, question_tokens, item):
            continue
        candidate_numbers = _numeric_mentions(item.get("question", ""))
        if question_numbers and candidate_numbers and not (question_numbers & candidate_numbers):
            continue
        overlap_score = len(question_tokens & faq_tokens) / max(len(question_tokens), 1)
        keyword_score = len(question_tokens & keyword_tokens) / max(len(question_tokens), 1) if keyword_tokens else 0.0
        total_score = overlap_score + (0.45 * keyword_score)
        if total_score > best_score:
            best_score = total_score
            best_match = {**item, "score": round(total_score, 3)}

    if not best_match or best_score < 0.34:
        return None
    return best_match


def find_best_global_companion_match(question: str, knowledge_dir: str) -> dict | None:
    scored_matches = []
    question_tokens = _tokenize(question)
    explicit_scope_tokens = question_tokens & SPECIFIC_FACILITY_SCOPE_TERMS
    for companion in list_document_companions(knowledge_dir):
        if explicit_scope_tokens and not _companion_alias_matches_question(question, companion):
            continue
        faq_match = find_best_companion_faq(question, companion)
        if faq_match:
            scored_matches.append(
                {
                    "companion": companion,
                    "faq_match": faq_match,
                    "score": faq_match["score"],
                }
            )
    if not scored_matches:
        return None
    scored_matches.sort(key=lambda item: item["score"], reverse=True)
    best_match = scored_matches[0]
    second_best = scored_matches[1] if len(scored_matches) > 1 else None
    if best_match["score"] < 0.72:
        return None
    if second_best and (best_match["score"] - second_best["score"]) < 0.14:
        return None
    companion = best_match["companion"]
    return {
        "answer": _humanize_companion_faq_answer(question, best_match["faq_match"]["answer"]),
        "sources": build_companion_sources(companion, question),
        "companion": companion,
        "faq_match": best_match["faq_match"],
    }


def _companion_alias_matches_question(question: str, companion: dict) -> bool:
    normalized_question = _normalize_text(question)
    compact_question = re.sub(r"[^a-z0-9]+", "", normalized_question)
    question_tokens = _tokenize(question)
    for alias in companion.get("aliases", []) or []:
        alias_normalized = _normalize_text(alias)
        if not alias_normalized:
            continue
        alias_tokens = _tokenize(alias_normalized)
        signal_tokens = {
            token
            for token in alias_tokens
            if token not in LOW_SIGNAL_MATCH_TOKENS and not token.isdigit()
        }
        if not signal_tokens:
            continue
        if alias_normalized in normalized_question:
            return True
        alias_compact = re.sub(r"[^a-z0-9]+", "", alias_normalized)
        if alias_compact and len(alias_compact) >= 4 and alias_compact in compact_question:
            return True
        if question_tokens & signal_tokens:
            return True
    return False


def build_companion_sources(companion: dict, question: str) -> list[dict]:
    sources: list[dict] = []
    faq_match = find_best_companion_faq(question, companion)
    if faq_match:
        source_text = faq_match.get("source_text") or faq_match["answer"]
        sources.append(
            {
                "source_id": "KC1",
                "document": companion["document"],
                "chunk_id": 0,
                "score": faq_match["score"],
                "retrieval_mode": "document_companion",
                "snippet": (
                    f"Pergunta de referência: {faq_match['question']}\n"
                    f"Factos operacionais validados para síntese: {source_text}"
                ),
            }
        )

    summary_bits = []
    if companion.get("summary"):
        summary_bits.append(companion["summary"])
    if companion.get("key_points"):
        summary_bits.append("Pontos-chave: " + "; ".join(item for item in companion["key_points"] if item))
    if summary_bits:
        sources.append(
            {
                "source_id": "KC2",
                "document": companion["document"],
                "chunk_id": 0,
                "score": 0.9 if is_companion_summary_request(question) else 0.4,
                "retrieval_mode": "document_companion",
                "snippet": " ".join(summary_bits),
            }
        )
    return sources


def build_companion_answer(question: str, companion: dict, *, context_label: str = "") -> str:
    faq_match = find_best_companion_faq(question, companion)
    if faq_match:
        return _humanize_companion_faq_answer(question, faq_match["answer"], context_label=context_label)

    if not is_companion_summary_request(question):
        return ""

    summary = _clean_text(companion.get("summary"))
    key_points = [item for item in companion.get("key_points", []) if _clean_text(item)]
    parts: list[str] = []
    if summary:
        parts.append(f"Segundo o {companion['title']}, {summary}")
    elif companion.get("title"):
        parts.append(f"Segundo o {companion['title']},")
    if key_points:
        parts.append("Pontos principais: " + "; ".join(key_points) + ".")
    return " ".join(part.strip() for part in parts if part.strip()).strip()


def companion_lookup_terms(companion: dict) -> list[str]:
    terms = []
    terms.extend(companion.get("aliases", []) or [])
    terms.extend(item.get("question", "") for item in companion.get("faq", []) or [])
    for item in companion.get("faq", []) or []:
        terms.extend(item.get("keywords", []) or [])
    if companion.get("summary"):
        terms.append(companion["summary"])
    return [item for item in dict.fromkeys(_clean_text(term) for term in terms) if item]
