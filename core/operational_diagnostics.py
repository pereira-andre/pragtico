"""Structured operational diagnostics for bot answers."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

from domain.route_transit import route_transit_answer
from domain.tug_guidance import build_tug_operational_guidance_source


LOA_RE = re.compile(
    r"\b(?:loa|comprimento)\s*(?:de|:|=)?\s*(\d{2,3}(?:[.,]\d+)?)\s*m\b"
    r"|\b(\d{2,3}(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?(?:loa|comprimento)\b"
    r"|\b(?:navio|roro|ro\s*ro|ro-ro|ro/ro|graneleiro|reefer|estilha|contentores?)\b"
    r"[^\n.;,]{0,80}?\b(?:de\s*)?(\d{2,3}(?:[.,]\d+)?)\s*m\b"
    r"(?!\s*(?:de\s*)?(?:boca|beam|largura|calado|draft))",
    flags=re.IGNORECASE,
)
BEAM_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?(?:boca|beam|largura)\b"
    r"|\b(?:boca|beam|largura)\s*(?:de|:|=)?\s*(\d+(?:[.,]\d+)?)\s*m\b",
    flags=re.IGNORECASE,
)
DRAFT_RE = re.compile(
    r"\b(?:calado|draft)\b[^\n.;,]{0,40}?\b(\d+(?:[.,]\d+)?)\s*m\b"
    r"|\b(\d+(?:[.,]\d+)?)\s*m(?:etros?)?\s*(?:de )?(?:calado|draft)\b",
    flags=re.IGNORECASE,
)
TUG_RE = re.compile(r"\b(\d{1,2})\s*(?:reboques?|rebocadores?)\b", flags=re.IGNORECASE)
TIME_RE = re.compile(
    r"\b(?:as|às|para as|para às|para|pelas)\s*(\d{1,2}(?::\d{2}|h\d{0,2}))\b"
    r"|\b(\d{1,2}(?::\d{2}|h\d{2}))\b",
    flags=re.IGNORECASE,
)
DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
WIND_KTS_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(?:kt|kts|n[oó]s)\b"
    r"|\bvento\s*(?:de|a|=|:)?\s*(\d+(?:[.,]\d+)?)\b",
    flags=re.IGNORECASE,
)
ROUTE_PAIR_RE = re.compile(
    r"\b(?:de|do|da)\s+(.{2,60}?)\s+(?:para|ate|até|->)\s+(.{2,80}?)(?:[?.!,;]|$)",
    flags=re.IGNORECASE,
)


def _normalize(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", without_accents.lower()).strip()


def _display_number(value: float | int | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value}".replace(".", ",")


def _safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _last_float(pattern: re.Pattern[str], text: str) -> float | None:
    value = None
    for match in pattern.finditer(text or ""):
        groups = [group for group in match.groups() if group]
        if groups:
            value = _safe_float(groups[-1])
    return value


def _last_int(pattern: re.Pattern[str], text: str) -> int | None:
    value = None
    for match in pattern.finditer(text or ""):
        try:
            value = int(match.group(1))
        except (TypeError, ValueError):
            continue
    return value


def _last_match_text(pattern: re.Pattern[str], text: str) -> str:
    value = ""
    for match in pattern.finditer(text or ""):
        groups = [group for group in match.groups() if group]
        value = (groups[0] if groups else match.group(0)).strip()
    return value


def _recent_user_text(history: Iterable[dict] | None, limit: int = 4) -> str:
    if not history:
        return ""
    chunks: list[str] = []
    for item in list(history)[-limit * 2:]:
        if str((item or {}).get("role") or "").strip().lower() != "user":
            continue
        content = str((item or {}).get("content") or "").strip()
        if content:
            chunks.append(content)
    return " ".join(chunks[-limit:])


def _infer_operation(clean: str) -> str:
    if re.search(r"\b(saida|sair|desatracar|desatracacao|largada)\b", clean):
        return "saida/desatracacao"
    if re.search(r"\b(mudanca|reatracacao|shift)\b", clean):
        return "mudanca"
    if re.search(r"\b(entrada|entrar|chegada|atracar|atracacao)\b", clean):
        return "entrada/atracacao"
    return ""


def _infer_facility(clean: str) -> str:
    if re.search(r"\b(hidrolift|eclusa|d31|d32|d33|doca 31|doca 32|doca 33)\b", clean):
        return "LISNAVE / Hidrolift"
    if re.search(r"\b(lisnave|mitrena|estaleiro|estaleiros|doca 20|doca 21|doca 22|d20|d21|d22)\b", clean):
        return "LISNAVE / Mitrena"
    if re.search(r"\b(eco oil|ecooil|ecoil)\b", clean):
        return "Eco-Oil"
    if re.search(r"\btanquisado\b", clean):
        return "Tanquisado"
    if re.search(r"\b(auto europa|autoeuropa|roro|ro ro|ro ro|cais 10|cais 11)\b", clean):
        return "Autoeuropa / Ro-Ro"
    if re.search(r"\btms\s*2\b|\btms2\b", clean):
        return "TMS 2"
    if re.search(r"\btms\s*1\b|\btms1\b", clean):
        return "TMS 1"
    return ""


def _infer_dock(clean: str) -> str:
    match = re.search(r"\b(?:doca\s*(20|21|22|31|32|33)|d(20|21|22|31|32|33))\b", clean)
    if not match:
        return ""
    return f"Doca {match.group(1) or match.group(2)}"


def _extract_route(text: str) -> dict:
    for match in ROUTE_PAIR_RE.finditer(text or ""):
        origin = re.sub(r"\s+", " ", match.group(1)).strip(" .,:;")
        destination = re.sub(r"\s+", " ", match.group(2)).strip(" .,:;")
        destination = re.split(
            r"\b(?:deve|quando|para chegar|a tempo|com hora|com chegada)\b",
            destination,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" .,:;")
        destination = re.sub(r"^(?:a|ao|aos|as|às|para)\s+", "", destination, flags=re.IGNORECASE)
        if origin and destination:
            return {"origin": origin, "destination": destination}
    return {}


def _extract_tug_rules(question: str, knowledge_dir: str) -> list[str]:
    source = build_tug_operational_guidance_source(question, knowledge_dir)
    if not source:
        return []
    rules: list[str] = []
    in_rules = False
    for raw_line in str(source.get("snippet") or "").splitlines():
        line = raw_line.strip()
        if line == "Regras diretamente aplicaveis:":
            in_rules = True
            continue
        if in_rules and not line.startswith("- "):
            break
        if in_rules and line.startswith("- "):
            rules.append(line[2:].strip())
    return list(dict.fromkeys(rules))


def _max_tugs_from_rules(rules: Iterable[str]) -> int | None:
    maximum = None
    for rule in rules:
        match = re.search(r"\b(\d{1,2})\s+rebocador(?:es)?\b", rule, flags=re.IGNORECASE)
        if not match:
            continue
        count = int(match.group(1))
        maximum = count if maximum is None else max(maximum, count)
    return maximum


def _add_unique_rule(rules: list[dict], title: str, detail: str, severity: str = "info") -> None:
    clean_detail = str(detail or "").strip()
    if not clean_detail:
        return
    if any(item.get("detail") == clean_detail for item in rules):
        return
    rules.append({"title": title, "detail": clean_detail, "severity": severity})


def _answer_covers_tug_minimum(answer: dict | None, minimum_tugs: int | None) -> bool | None:
    if not answer or minimum_tugs is None:
        return None
    text = str(answer.get("answer") or "")
    if not text:
        return None
    return bool(re.search(rf"\b{minimum_tugs}\s+rebocador(?:es)?\b", text, flags=re.IGNORECASE))


def build_operational_diagnostic(
    question: str,
    *,
    history: Iterable[dict] | None = None,
    answer: dict | None = None,
    knowledge_dir: str = "knowledge",
) -> dict:
    """Build a compact operational case card for user-facing diagnostics."""
    recent_text = _recent_user_text(history)
    current_text = str(question or "").strip()
    combined_text = " ".join(part for part in (recent_text, current_text) if part).strip()
    clean = _normalize(combined_text)
    current_clean = _normalize(current_text)
    if not combined_text:
        return {"present": False}

    case = {
        "facility": _infer_facility(clean),
        "dock": _infer_dock(clean),
        "operation": _infer_operation(clean),
        "loa_m": _last_float(LOA_RE, combined_text),
        "beam_m": _last_float(BEAM_RE, combined_text),
        "draft_m": _last_float(DRAFT_RE, combined_text),
        "requested_tugs": _last_int(TUG_RE, combined_text),
        "time": _last_match_text(TIME_RE, combined_text),
        "date": _last_match_text(DATE_RE, combined_text),
        "route": _extract_route(combined_text),
    }
    wind_kts = _last_float(WIND_KTS_RE, combined_text)
    if wind_kts is not None:
        case["wind_kts"] = wind_kts
    if re.search(r"\b(nevoeiro|nevoa|neblina|fog|mist)\b", clean):
        case["visibility"] = "nevoeiro/visibilidade reduzida"

    critical_rules: list[dict] = []
    warnings: list[str] = []
    missing_fields: list[str] = []
    case_lines: list[str] = []

    if case["facility"]:
        case_lines.append(f"Local: {case['facility']}")
    if case["dock"]:
        case_lines.append(f"Doca/cais: {case['dock']}")
    if case["operation"]:
        case_lines.append(f"Operacao: {case['operation']}")
    if case["loa_m"] is not None:
        case_lines.append(f"LOA: {_display_number(case['loa_m'])} m")
    if case["beam_m"] is not None:
        case_lines.append(f"Boca: {_display_number(case['beam_m'])} m")
    if case["draft_m"] is not None:
        case_lines.append(f"Calado: {_display_number(case['draft_m'])} m")
    if case["requested_tugs"] is not None:
        case_lines.append(f"Rebocadores indicados: {case['requested_tugs']}")
    if case["time"]:
        case_lines.append(f"Hora referida: {case['time'].replace('h', ':')}")
    if case["route"]:
        case_lines.append(f"Percurso: {case['route']['origin']} -> {case['route']['destination']}")

    wants_tugs = bool(re.search(r"\b(reboque|reboques|rebocador|rebocadores)\b", current_clean))
    tug_rules = _extract_tug_rules(combined_text, knowledge_dir) if wants_tugs or case["requested_tugs"] else []
    for rule in tug_rules:
        _add_unique_rule(critical_rules, "Rebocadores", rule, "critical")
    minimum_tugs = _max_tugs_from_rules(tug_rules)

    if case["facility"] == "LISNAVE / Mitrena" and case["loa_m"] and case["loa_m"] > 250:
        _add_unique_rule(critical_rules, "Rebocadores", "Lisnave acima de 250 m: 6 rebocadores.", "critical")
        minimum_tugs = max(minimum_tugs or 0, 6)
    if case["facility"] == "LISNAVE / Mitrena" and case["dock"] in {"Doca 20", "Doca 21", "Doca 22"}:
        _add_unique_rule(critical_rules, "Lisnave", "Docas 20/21/22 exigem pelo menos 4 rebocadores e manobra junto ao reponto.", "critical")
        minimum_tugs = max(minimum_tugs or 0, 4)
    if case["facility"] == "LISNAVE / Hidrolift":
        _add_unique_rule(critical_rules, "Hidrolift", "D31/D32/D33 entram via Hidrolift: boca maxima 32 m e sonda de acesso 5,5 m ZH.", "critical")
        _add_unique_rule(critical_rules, "Rebocadores", "Entradas em docas Lisnave exigem pelo menos 4 rebocadores.", "critical")
        minimum_tugs = max(minimum_tugs or 0, 4)
        if case["beam_m"] and case["beam_m"] > 32:
            warnings.append(
                f"Bloqueio dimensional: boca {_display_number(case['beam_m'])} m excede a boca maxima 32 m do Hidrolift."
            )
    if case["facility"] in {"Tanquisado", "Eco-Oil"}:
        _add_unique_rule(critical_rules, "Rebocadores", f"{case['facility']}: usar sempre no minimo 3 rebocadores.", "critical")
        minimum_tugs = max(minimum_tugs or 0, 3)
        _add_unique_rule(critical_rules, case["facility"], "Validar chegada ao reponto, calado, vento lateral e posicionamento proa/popa/costado.", "info")

    if case.get("wind_kts") and case["wind_kts"] > 30:
        warnings.append(f"Vento {_display_number(case['wind_kts'])} kt: manobras suspensas acima de 30 kt.")
    elif case.get("wind_kts") and case["wind_kts"] >= 25:
        warnings.append(f"Vento {_display_number(case['wind_kts'])} kt: zona de cautela; confirmar limite local antes de avançar.")
    if case.get("visibility"):
        warnings.append("Nevoeiro/visibilidade reduzida: a regra de segurança prevalece sobre reforço de rebocadores.")

    tide_topic = bool(re.search(r"\b(reponto|preia|baixa|mare|mar[eé]|estofo)\b", clean))
    route_topic = bool(re.search(r"\b(percurso|tempo|demora|transito|viagem|fundeadouro|barra)\b", clean))
    if tide_topic:
        _add_unique_rule(critical_rules, "Mare", "A validacao deve usar a hora da fase critica no cais/doca, nao apenas a hora de largada.", "info")
    if route_topic or case["route"]:
        route_answer = route_transit_answer(combined_text, clean)
        if not route_answer and case["route"]:
            route_question = f"quanto tempo de {case['route']['origin']} para {case['route']['destination']}?"
            route_answer = route_transit_answer(route_question)
        if route_answer:
            snippet = ((route_answer.get("sources") or [{}])[0].get("snippet") or route_answer.get("answer") or "")
            _add_unique_rule(critical_rules, "Percurso/duracao", str(snippet)[:260], "info")
        else:
            _add_unique_rule(critical_rules, "Percurso/duracao", "Confirmar duracao origem -> destino e marcar a largada para chegar ao ponto critico no reponto.", "info")

    if minimum_tugs and case["requested_tugs"] is not None and case["requested_tugs"] < minimum_tugs:
        warnings.append(f"Rebocadores insuficientes: foram indicados {case['requested_tugs']}, mas a regra critica pede {minimum_tugs}.")

    if wants_tugs and not case["loa_m"] and case["facility"] not in {"Tanquisado", "Eco-Oil"}:
        missing_fields.append("LOA/comprimento do navio para escolher o patamar de rebocadores.")
    if wants_tugs and case["facility"] == "":
        missing_fields.append("Cais/doca/terminal para aplicar excecoes locais.")
    if case["facility"] == "LISNAVE / Mitrena" and case["loa_m"] and case["loa_m"] > 250:
        missing_fields.append("DWT, carga perigosa, estado carregado/vazio e thrusters podem agravar os meios.")
    if tide_topic and not case["time"]:
        missing_fields.append("Hora de largada e hora prevista de chegada ao ponto critico.")

    answer_checks: list[dict] = []
    covers_minimum = _answer_covers_tug_minimum(answer, minimum_tugs)
    if covers_minimum is not None:
        answer_checks.append(
            {
                "label": "Resposta cobre minimo de rebocadores",
                "ok": covers_minimum,
                "detail": f"Minimo aplicado: {minimum_tugs} rebocador(es).",
            }
        )

    present = bool(case_lines or critical_rules or warnings or missing_fields)
    summary = "Sem dados operacionais suficientes para ficha estruturada."
    if present:
        summary = "Ficha operacional preparada antes da conclusao."
        if warnings:
            summary = warnings[0]
        elif minimum_tugs:
            summary = f"Minimo critico identificado: {minimum_tugs} rebocador(es)."

    return {
        "present": present,
        "summary": summary,
        "case": {key: value for key, value in case.items() if value not in ("", None, {})},
        "case_lines": list(dict.fromkeys(case_lines)),
        "critical_rules": critical_rules,
        "warnings": list(dict.fromkeys(warnings)),
        "missing_fields": list(dict.fromkeys(missing_fields)),
        "minimum_tugs": minimum_tugs,
        "answer_checks": answer_checks,
    }


def format_operational_diagnostic(diagnostic: dict | None) -> str:
    diagnostic = diagnostic or {}
    if not diagnostic.get("present"):
        return (
            "Diagnóstico operacional\n"
            "Não encontrei dados operacionais suficientes na resposta anterior. "
            "Repete a pergunta com navio, cais/doca, hora e decisão pretendida."
        )

    lines = ["Diagnóstico operacional", str(diagnostic.get("summary") or "").strip()]
    case_lines = diagnostic.get("case_lines") or []
    if case_lines:
        lines.append("")
        lines.append("Ficha do pedido:")
        lines.extend(f"- {item}" for item in case_lines[:8])
    rules = diagnostic.get("critical_rules") or []
    if rules:
        lines.append("")
        lines.append("Regras aplicadas:")
        for item in rules[:6]:
            detail = str(item.get("detail") or "").strip()
            if not detail:
                continue
            title = str(item.get("title") or "").strip()
            lines.append(f"- {title}: {detail}" if title else f"- {detail}")
    warnings = diagnostic.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Alertas:")
        lines.extend(f"- {item}" for item in warnings[:4])
    missing = diagnostic.get("missing_fields") or []
    if missing:
        lines.append("")
        lines.append("Dados a confirmar:")
        lines.extend(f"- {item}" for item in missing[:5])
    checks = diagnostic.get("answer_checks") or []
    if checks:
        lines.append("")
        lines.append("Controlo da resposta:")
        for item in checks[:4]:
            status = "OK" if item.get("ok") else "ATENCAO"
            lines.append(f"- {status}: {item.get('label')} ({item.get('detail')})")
    return "\n".join(line for line in lines if line is not None).strip()
