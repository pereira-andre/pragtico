from __future__ import annotations

import re
from typing import Dict, List

from domain.port_entities import (
    ENTITY_BY_NAME,
    detect_port_entities,
    entity_names_from_matches,
    normalize_entity_text,
    primary_entity,
    specific_entities,
)


SECTION_SEPARATOR_RE = re.compile(r"^[=\-_\s]{6,}$")
PAGE_RE = re.compile(r"^\[P[áa]gina\s+(\d+)\]$", flags=re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", str(line or "")).strip()


def _is_heading(line: str) -> bool:
    clean = _clean_line(line)
    if not clean or SECTION_SEPARATOR_RE.match(clean):
        return False
    if len(clean) > 140:
        return False
    if clean.startswith(("—", "-", "*")):
        return False
    letters = [char for char in clean if char.isalpha()]
    if not letters:
        return False
    uppercase = sum(1 for char in letters if char.upper() == char)
    uppercase_ratio = uppercase / len(letters)
    return uppercase_ratio >= 0.75 and not clean.endswith(".")


def _document_title(lines: List[str], document_name: str) -> str:
    for line in lines[:25]:
        clean = _clean_line(line)
        if clean.upper().startswith("DOCUMENTO:"):
            return clean.split(":", 1)[1].strip()
    return document_name


def _document_version(lines: List[str]) -> str:
    for line in lines[:40]:
        clean = _clean_line(line)
        upper = clean.upper()
        if upper.startswith("REVISÃO") or upper.startswith("REVISAO"):
            return clean
    return ""


def infer_content_type(section: str, text: str) -> str:
    clean = f"{section} {text}".lower()
    if any(token in clean for token in ("quadro", "tabela", "faixa ", "formula", "fórmula")):
        return "tabela"
    if any(token in clean for token in ("proibid", "permitid", "restri", "limite", "maximo", "máximo", "condicionante")):
        return "restricao"
    if any(token in clean for token in ("responsabilidad", "procedimento", "validar", "requisicao", "requisição")):
        return "procedimento"
    if any(token in clean for token in ("definicao", "definição", "significa", "refere-se")):
        return "definicao"
    if any(token in clean for token in ("nota", "observacao", "observação", "recomend")):
        return "nota"
    return "texto"


def infer_content_scope(entity_matches: List[Dict]) -> str:
    specific = [entity for entity in entity_matches if not entity.get("generic")]
    if len(specific) > 1:
        return "comparativo"
    if len(specific) == 1:
        return "entidade_especifica"
    if entity_matches:
        return "grupo_generico"
    return "geral"


def _split_long_paragraph(paragraph: str, max_chars: int) -> List[str]:
    paragraph = _clean_line(paragraph)
    if len(paragraph) <= max_chars:
        return [paragraph] if paragraph else []

    pieces: List[str] = []
    current = ""
    for sentence in SENTENCE_SPLIT_RE.split(paragraph):
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > max_chars:
            pieces.append(current)
            current = sentence
        elif current:
            current = f"{current} {sentence}"
        else:
            current = sentence
    if current:
        pieces.append(current)

    if all(len(piece) <= max_chars for piece in pieces):
        return pieces

    word_chunks: List[str] = []
    for piece in pieces:
        current_words: List[str] = []
        current_len = 0
        for word in piece.split():
            next_len = current_len + len(word) + (1 if current_words else 0)
            if current_words and next_len > max_chars:
                word_chunks.append(" ".join(current_words))
                current_words = [word]
                current_len = len(word)
            else:
                current_words.append(word)
                current_len = next_len
        if current_words:
            word_chunks.append(" ".join(current_words))
    return word_chunks


def _split_section_body(body: str, max_chars: int) -> List[str]:
    paragraphs = [
        _clean_line(part)
        for part in re.split(r"\n\s*\n", body)
        if _clean_line(part)
    ]
    if not paragraphs:
        return []

    chunks: List[str] = []
    current = ""
    for paragraph in paragraphs:
        paragraph_pieces = _split_long_paragraph(paragraph, max_chars)
        for piece in paragraph_pieces:
            if current and len(current) + 2 + len(piece) > max_chars:
                chunks.append(current)
                current = piece
            elif current:
                current = f"{current}\n\n{piece}"
            else:
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _contextual_text(metadata: Dict, body: str) -> str:
    header_parts = [
        f"Documento: {metadata.get('document_title') or metadata.get('document_name')}",
        f"Revisao: {metadata.get('document_version')}" if metadata.get("document_version") else "",
        f"Pagina: {metadata.get('page')}" if metadata.get("page") else "",
        f"Seccao: {metadata.get('section')}" if metadata.get("section") else "",
        f"Entidade: {', '.join(metadata.get('entity_names') or [])}" if metadata.get("entity_names") else "",
        f"Tipo: {metadata.get('content_type')}" if metadata.get("content_type") else "",
    ]
    header = "\n".join(part for part in header_parts if part)
    return f"{header}\n\n{body.strip()}".strip()


def _section_records(lines: List[str]) -> List[Dict]:
    records: List[Dict] = []
    current_section = "Cabecalho"
    parent_section = ""
    current_page = None
    buffer: List[str] = []

    def flush() -> None:
        nonlocal buffer
        text = "\n".join(buffer).strip()
        if text:
            records.append(
                {
                    "section": current_section,
                    "page": current_page,
                    "text": text,
                }
            )
        buffer = []

    for raw_line in lines:
        line = raw_line.rstrip()
        clean = _clean_line(line)
        if not clean:
            if buffer and buffer[-1] != "":
                buffer.append("")
            continue
        page_match = PAGE_RE.match(clean)
        if page_match:
            flush()
            current_page = int(page_match.group(1))
            continue
        if SECTION_SEPARATOR_RE.match(clean):
            continue
        if _is_heading(clean) and not clean.upper().startswith(("DOCUMENTO:", "ENTIDADE EMISSORA:", "REVISAO", "REVISÃO")):
            flush()
            if clean.endswith(":") and parent_section:
                current_section = f"{parent_section} / {clean}"
            else:
                current_section = clean
                parent_section = clean
            continue
        buffer.append(clean)
    flush()
    return records


def _forced_entities_from_section(section: str) -> List[Dict]:
    section_norm = normalize_entity_text(section)
    section_tokens = set(section_norm.split())
    forced_names: List[str] = []
    if "sapec" in section_tokens or "tps" in section_tokens or "tgl" in section_tokens:
        if "tps" in section_tokens or "solidos" in section_tokens:
            forced_names.append("SAPEC solidos")
        if "tgl" in section_tokens or "liquidos" in section_tokens:
            forced_names.append("SAPEC liquidos")
        if forced_names:
            forced_names.append("SAPEC")
    if "secil" in section_tokens or "cais de oeste" in section_norm or "cais de este" in section_norm:
        if "oeste" in section_tokens or "west" in section_tokens:
            forced_names.append("Secil W")
        if "este" in section_tokens or "east" in section_tokens:
            forced_names.append("Secil E")
        if forced_names:
            forced_names.append("Secil")
    return [ENTITY_BY_NAME[name] for name in dict.fromkeys(forced_names) if name in ENTITY_BY_NAME]


def _resolve_entities(section: str, body: str, document_entities: List[Dict]) -> List[Dict]:
    forced = _forced_entities_from_section(section)
    if forced:
        return forced

    section_matches = detect_port_entities(f"{section}\n{body}")
    section_specific = specific_entities(section_matches)
    if section_specific:
        return section_matches

    document_specific = specific_entities(document_entities)
    if document_specific:
        return document_entities
    return section_matches or document_entities


def structured_chunk_document(
    text: str,
    document_name: str,
    *,
    max_chars: int = 950,
) -> List[Dict]:
    lines = str(text or "").splitlines()
    title = _document_title(lines, document_name)
    version = _document_version(lines)
    records = _section_records(lines)
    chunks: List[Dict] = []
    document_entities = detect_port_entities(f"{document_name}\n{title}")

    for record in records:
        section = record["section"]
        for body in _split_section_body(record["text"], max_chars=max_chars):
            entity_matches = _resolve_entities(section, body, document_entities)
            entity_names = entity_names_from_matches(entity_matches)
            main_entity = primary_entity(f"{section}\n{body}")
            if not main_entity and entity_matches:
                main_entity = entity_matches[0]
            metadata = {
                "document_name": document_name,
                "document_title": title,
                "document_version": version,
                "page": record.get("page"),
                "section": section,
                "entity_names": entity_names,
                "primary_entity": main_entity.get("name") if main_entity else "",
                "channel": main_entity.get("channel") if main_entity else "",
                "location_type": main_entity.get("type") if main_entity else "",
                "content_type": infer_content_type(section, body),
                "content_scope": infer_content_scope(entity_matches),
                "source": f"{document_name} | {section}",
            }
            chunks.append(
                {
                    **metadata,
                    "text": _contextual_text(metadata, body),
                    "raw_text": body,
                }
            )
    return chunks


def chunk_text_by_structure(text: str, *, chunk_size: int = 900, overlap: int = 160) -> List[str]:
    del overlap
    chunks = structured_chunk_document(text, "documento", max_chars=chunk_size)
    return [item["raw_text"] for item in chunks if item.get("raw_text")]
