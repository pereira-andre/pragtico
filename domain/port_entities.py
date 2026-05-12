from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List


def normalize_entity_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


PORT_ENTITIES: List[Dict] = [
    {
        "name": "Secil W",
        "type": "terminal_berth",
        "channel": "Barra / Outao",
        "entry_order": 10,
        "aliases": ["Secil W", "Secil Oeste", "Secil West", "Terminal Secil W", "Cais de Oeste", "Cais Oeste", "Cais A Secil"],
        "must_not_mix_with": ["Secil E"],
    },
    {
        "name": "Secil E",
        "type": "terminal_berth",
        "channel": "Barra / Outao",
        "entry_order": 11,
        "aliases": ["Secil E", "Secil Este", "Secil East", "Terminal Secil E", "Cais de Este", "Cais Este", "Cais B Secil"],
        "must_not_mix_with": ["Secil W"],
    },
    {
        "name": "Secil",
        "type": "terminal_group",
        "channel": "Barra / Outao",
        "entry_order": 10,
        "aliases": ["Secil", "Terminal Secil"],
        "must_not_mix_with": [],
        "generic": True,
        "disambiguation_options": ["Secil W", "Secil E"],
        "ambiguity_note": "Secil pode referir-se ao cais W/Oeste ou ao cais E/Este.",
    },
    {
        "name": "TMS-1",
        "type": "terminal",
        "channel": "Canal Norte",
        "entry_order": 100,
        "aliases": ["TMS-1", "TMS1", "Terminal Multiusos Zona 1", "Terminal Multiusos 1", "TERSADO", "Cais das Fontainhas"],
        "must_not_mix_with": ["TMS-2", "Autoeuropa", "SAPEC solidos", "SAPEC liquidos"],
    },
    {
        "name": "TMS-2",
        "type": "terminal",
        "channel": "Canal Norte",
        "entry_order": 110,
        "aliases": ["TMS-2", "TMS2", "Terminal Multiusos Zona 2", "Terminal Multiusos 2", "SADOPORT", "Sadoport"],
        "must_not_mix_with": ["TMS-1", "Autoeuropa", "SAPEC solidos", "SAPEC liquidos"],
    },
    {
        "name": "Autoeuropa",
        "type": "terminal",
        "channel": "Canal Norte",
        "entry_order": 120,
        "aliases": ["Autoeuropa", "Auto-Europa", "Cais 10", "Cais 11", "Terminal Ro-Ro", "Ro-Ro", "RORO", "Coelho da Mota"],
        "must_not_mix_with": ["TMS-1", "TMS-2", "SAPEC solidos", "SAPEC liquidos"],
    },
    {
        "name": "Praias do Sado",
        "type": "terminal",
        "channel": "Canal Norte",
        "entry_order": 130,
        "aliases": ["Praias do Sado", "Terminal Praias do Sado", "Terminal de Praias do Sado", "Pirites Alentejanas", "ex-Pirites Alentejanas"],
        "must_not_mix_with": ["SAPEC solidos", "SAPEC liquidos", "Autoeuropa"],
    },
    {
        "name": "SAPEC solidos",
        "type": "terminal",
        "channel": "Canal Norte",
        "entry_order": 140,
        "aliases": ["SAPEC solidos", "Sapec solidos", "SAPEC granéis sólidos", "SAPEC graneis solidos", "Terminal Portuario SAPEC", "Terminal SAPEC solidos", "TPS", "Terminal de Graneis Solidos"],
        "must_not_mix_with": ["SAPEC liquidos", "Secil W", "Secil E", "Eco-Oil", "Tanquisado"],
    },
    {
        "name": "SAPEC liquidos",
        "type": "terminal",
        "channel": "Canal Norte",
        "entry_order": 150,
        "aliases": ["SAPEC liquidos", "Sapec liquidos", "SAPEC granéis líquidos", "SAPEC graneis liquidos", "Terminal SAPEC liquidos", "Terminal SAPEC graneis liquidos", "TGL", "Terminal de Graneis Liquidos"],
        "must_not_mix_with": ["SAPEC solidos", "Secil W", "Secil E", "Eco-Oil", "Tanquisado"],
    },
    {
        "name": "SAPEC",
        "type": "terminal_group",
        "channel": "Canal Norte",
        "entry_order": 140,
        "aliases": ["SAPEC", "Cais da SAPEC"],
        "must_not_mix_with": [],
        "generic": True,
        "disambiguation_options": ["SAPEC solidos", "SAPEC liquidos"],
        "ambiguity_note": "SAPEC pode referir-se ao terminal de sólidos ou ao terminal de líquidos.",
    },
    {
        "name": "Alstom",
        "type": "terminal",
        "channel": "Canal Norte",
        "entry_order": 160,
        "aliases": [
            "Alstom",
            "Terminal Alstom",
            "Cais Alstom",
            "Cais da Alstom",
            "Alstom Portugal",
            "ABB Alstom",
            "ABB-ALSTOM",
            "Cais ABB Alstom",
        ],
        "must_not_mix_with": ["SAPEC solidos", "SAPEC liquidos"],
    },
    {
        "name": "Uralada",
        "type": "terminal",
        "channel": "Porto de Setubal",
        "entry_order": 170,
        "aliases": ["Uralada", "Terminal Uralada", "Cais Uralada", "Cais da Uralada", "URALADA"],
        "must_not_mix_with": ["SAPEC solidos", "SAPEC liquidos", "Alstom"],
    },
    {
        "name": "Lisnave",
        "type": "shipyard",
        "channel": "Canal Sul",
        "entry_order": 200,
        "aliases": [
            "Lisnave",
            "LISNAVE",
            "Estaleiro Lisnave",
            "Estaleiro Naval Lisnave",
            "Estaleiros Mitrena",
            "Mitrena",
            "Terminal Lisnave",
            "Cais da Lisnave",
            "Docas da Lisnave",
            "Docas Lisnave",
            "Docas 20 21 22",
            "Doca 20",
            "Doca 21",
            "Doca 22",
            "Hidrolift",
        ],
        "must_not_mix_with": ["Tanquisado", "Eco-Oil", "SAPEC solidos", "SAPEC liquidos"],
        "scope_hints": ["estaleiro", "pontes-cais", "docas secas", "hidrolift"],
    },
    {
        "name": "Tanquisado",
        "type": "terminal",
        "channel": "Canal Sul",
        "entry_order": 210,
        "aliases": ["Tanquisado", "TANQUISADO", "Terminal Tanquisado"],
        "must_not_mix_with": ["Eco-Oil", "SAPEC solidos", "SAPEC liquidos", "Lisnave"],
    },
    {
        "name": "Eco-Oil",
        "type": "terminal",
        "channel": "Canal Sul",
        "entry_order": 220,
        "aliases": ["Eco-Oil", "Eco Oil", "EcoOil", "ECO-OIL", "ECOIL", "Terminal ECO-OIL"],
        "must_not_mix_with": ["Tanquisado", "SAPEC solidos", "SAPEC liquidos", "Secil W", "Secil E"],
    },
    {
        "name": "Termitrena",
        "type": "terminal",
        "channel": "Canal Sul",
        "entry_order": 230,
        "aliases": ["Termitrena", "Eurominas", "Terminal da Mitrena", "Terminal de Graneis Solidos da Mitrena"],
        "must_not_mix_with": ["Lisnave", "Tanquisado", "Eco-Oil"],
    },
    {
        "name": "Teporset",
        "type": "terminal",
        "channel": "Canal Sul",
        "entry_order": 240,
        "aliases": ["Teporset", "Terminal Teporset", "Terminal Portuario de Setubal"],
        "must_not_mix_with": ["Lisnave", "Tanquisado", "Eco-Oil"],
    },
    {
        "name": "Fundeadouro Norte",
        "type": "anchorage",
        "channel": "Zona de fundeadouro",
        "entry_order": 300,
        "aliases": ["Fundeadouro Norte", "Fundeadouro norte"],
        "must_not_mix_with": ["Fundeadouro Sul", "Troia A", "Troia B"],
    },
    {
        "name": "Fundeadouro Sul",
        "type": "anchorage",
        "channel": "Canal Sul / Troia",
        "entry_order": 310,
        "aliases": ["Fundeadouro Sul", "Fundeadouro sul", "Fundeadouro de Troia", "Fundeadouro Tróia", "Fundeadouro Troia"],
        "must_not_mix_with": ["Fundeadouro Norte"],
    },
    {
        "name": "Troia A",
        "type": "anchorage",
        "channel": "Canal Sul / Troia",
        "entry_order": 320,
        "aliases": ["Troia A", "Tróia A", "Fundeadouro Troia A", "Fundeadouro Tróia A"],
        "must_not_mix_with": ["Troia B", "Fundeadouro Norte"],
    },
    {
        "name": "Troia B",
        "type": "anchorage",
        "channel": "Canal Sul / Troia",
        "entry_order": 330,
        "aliases": ["Troia B", "Tróia B", "Fundeadouro Troia B", "Fundeadouro Tróia B"],
        "must_not_mix_with": ["Troia A", "Fundeadouro Norte"],
    },
]


ENTITY_BY_NAME = {entity["name"]: entity for entity in PORT_ENTITIES}
_NORMALIZED_ALIASES = [
    (normalize_entity_text(alias), alias, entity)
    for entity in PORT_ENTITIES
    for alias in entity.get("aliases", [])
]
_NORMALIZED_ALIASES.sort(key=lambda item: len(item[0]), reverse=True)


def _contains_alias(text_norm: str, alias_norm: str) -> bool:
    if not alias_norm:
        return False
    return f" {alias_norm} " in f" {text_norm} "


def detect_port_entities(text: str, *, include_generic: bool = True) -> List[Dict]:
    text_norm = normalize_entity_text(text)
    if not text_norm:
        return []

    matches: Dict[str, Dict] = {}
    for alias_norm, alias, entity in _NORMALIZED_ALIASES:
        if not include_generic and entity.get("generic"):
            continue
        if not _contains_alias(text_norm, alias_norm):
            continue
        current = matches.setdefault(
            entity["name"],
            {
                "name": entity["name"],
                "type": entity.get("type", ""),
                "channel": entity.get("channel", ""),
                "entry_order": entity.get("entry_order"),
                "aliases": entity.get("aliases", []),
                "matched_aliases": [],
                "must_not_mix_with": entity.get("must_not_mix_with", []),
                "generic": bool(entity.get("generic")),
                "disambiguation_options": entity.get("disambiguation_options", []),
                "ambiguity_note": entity.get("ambiguity_note", ""),
                "scope_hints": entity.get("scope_hints", []),
            },
        )
        current["matched_aliases"].append(alias)

    return sorted(
        matches.values(),
        key=lambda item: (
            bool(item.get("generic")),
            item.get("entry_order") if item.get("entry_order") is not None else 9999,
            item["name"],
        ),
    )


def specific_entities(entities: List[Dict]) -> List[Dict]:
    return [entity for entity in entities if not entity.get("generic")]


def entity_names_from_matches(entities: List[Dict]) -> List[str]:
    return [entity["name"] for entity in entities]


def resolve_port_entity(text: str, *, include_generic: bool = True) -> Dict[str, Any]:
    matches = detect_port_entities(text, include_generic=include_generic)
    specifics = specific_entities(matches)
    generic_matches = [entity for entity in matches if entity.get("generic")]
    primary = specifics[0] if specifics else (matches[0] if matches else None)

    disambiguation = None
    if not specifics:
        disambiguation_match = next(
            (
                entity
                for entity in generic_matches
                if entity.get("disambiguation_options")
            ),
            None,
        )
        if disambiguation_match:
            disambiguation = {
                "name": disambiguation_match["name"],
                "options": list(disambiguation_match.get("disambiguation_options") or []),
                "note": disambiguation_match.get("ambiguity_note", ""),
            }

    return {
        "matches": matches,
        "primary": primary,
        "is_ambiguous": bool(disambiguation),
        "disambiguation": disambiguation,
    }


def primary_entity(text: str) -> Dict | None:
    matches = detect_port_entities(text)
    specifics = specific_entities(matches)
    if specifics:
        return specifics[0]
    return matches[0] if matches else None
