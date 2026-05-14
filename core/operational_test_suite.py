"""Admin-only operational flow tests for port calls and maneuvers."""

from __future__ import annotations

import json
import os
import time
import csv
from copy import deepcopy
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from textwrap import wrap
from typing import Any, Callable

from blueprints.port_calls import (
    VESSEL_CATALOG_STATE_KEY,
    _build_vessel_catalog_options,
    _coerce_port_call_payload_with_catalog,
    _filter_vessel_catalog_options,
    _remove_vessel_catalog_record,
    _sync_vessel_catalog_record_to_active_port_calls,
    _upsert_vessel_catalog_record,
    _validate_vessel_catalog_record,
    _vessel_catalog_txt,
)
from core import services
from core.chat_planner import build_chat_execution_plan
from core.chat_reasoning import build_conversation_reasoning_state
from core.form_helpers import (
    ensure_maneuver_hour_capacity_for_approval,
    ensure_portal_berth_is_available,
    ensure_portal_berth_is_physically_available,
)
from core.maneuver_context import answer_slash_validation, build_scale_context
from core.operational_diagnostics import build_operational_diagnostic, format_operational_diagnostic
from core.operational_actions import answer_slash_query, finalize_operational_proposal
from core.operational_sources import answer_direct_operational_query, build_operational_chat_sources
from core.portal_notifications import latest_maneuver_by_type
from core.rule_catalog import _active_knowledge_dir
from domain.berth_profiles import find_best_berth_profile
from domain.berth_layout import build_slot_occupancy, find_occupied_berth_conflict
from domain.chat_action_config import SLASH_COMMAND_ALIASES
from domain.chat_action_templates import build_slash_help
from domain.chat_actions import parse_slash_command
from integrations.tide_service import TideService
from storage.port_call_helpers import _decorate_port_call


TEST_VESSEL_PREFIX = "TESTE QA"
RAILWAY_BOT_TEST_FIXTURE = Path(__file__).resolve().parents[1] / "resources" / "qa" / "railway_bot_tests_150.json"
QUERY_SLASH_COMMANDS = {
    "local_warnings",
    "wave",
    "tides",
    "weather",
    "planning",
    "planning_approved",
    "planning_pending",
    "colreg_list",
    "colreg_rule",
    "consult_scale",
    "consult_maneuver",
    "consult_scale_cost",
    "consult_maneuver_cost",
    "consult_vessel",
    "rule",
}
BOT_CRITICAL_TEST_MATRIX: list[dict] = [
    {
        "id": "roro-north-strong",
        "group": "Rebocadores e vento",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Ro-Ro acima de 220 m com Norte forte",
        "question": "Quantos reboques para RORO de 230m a entrar com vento norte forte?",
        "expected_summary": "Recomenda 4 rebocadores grandes e preserva a excecao pratica acima de 220 m.",
        "source": "knowledge/tug_operational_guidance.json",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": ("Recomendo 4 rebocadores grandes", "mais de 220 m", "vento Norte forte"),
    },
    {
        "id": "autoeuropa-roro-live-weather-tugs",
        "group": "Rebocadores e vento",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Autoeuropa Ro-Ro com meteorologia atual",
        "question": "Tenho um navio para sair da Autoeuropa. É um roro com 200 m e tem bowthruster. Quantos reboques devo pedir face às condições meteorológicas atuais?",
        "expected_summary": "Cruza a regra de rebocadores com vento/rajadas atuais e não responde só com posicionamento.",
        "source": "knowledge/tug_operational_guidance.json + meteorologia live",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": (
            "Recomendo 2 rebocadores",
            "Ro-Ro com vento",
            "a sair: 2 rebocadores",
            "Meteorologia considerada",
            "Local: Autoeuropa",
        ),
    },
    {
        "id": "bulk-tms2-north-strong",
        "group": "Rebocadores e vento",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Graneleiro/contentores grande a sair com Norte forte",
        "question": "Um Graneleiro de 190m a sair do TMS2 com vento norte forte, quantos rebocadores leva?",
        "expected_summary": "Recomenda 4 grandes, mantendo a excecao Tanquisado/Eco-Oil/Lisnave caso a caso.",
        "source": "knowledge/tug_operational_guidance.json",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": ("Recomendo 4 rebocadores grandes", "vento Norte forte", "Tanquisado", "Eco-Oil"),
    },
    {
        "id": "lisnave-100-150",
        "group": "Rebocadores e vento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Lacuna Lisnave 100-150 m",
        "question": "Um navio de 130m para a Lisnave precisa de quantos rebocadores?",
        "expected_summary": "Fecha a lacuna documental com regra pratica de 3 rebocadores.",
        "source": "knowledge/tug_operational_guidance.json",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": ("Recomendo 3 rebocadores", "acima de 100 m ate 150 m", "Quatro rebocadores sao excessivos"),
    },
    {
        "id": "diagnostic-lisnave-300-six-tugs",
        "group": "Diagnostico operacional",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "operational_diagnostic",
        "label": "Diagnostico Lisnave 300 m",
        "question": "Um navio na LISNAVE de 300 m manobra com quantos rebocadores normalmente?",
        "expected_summary": "A ficha deve aplicar o patamar especifico antes de qualquer minimo generico.",
        "source": "core/operational_diagnostics.py + knowledge/tug_operational_guidance.json",
        "expected_tokens": ("Minimo critico identificado: 6 rebocador", "Lisnave acima de 250 m: 6 rebocadores"),
    },
    {
        "id": "diagnostic-hidrolift-beam-limit",
        "group": "Diagnostico operacional",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "operational_diagnostic",
        "label": "Diagnostico Hidrolift boca 45 m",
        "question": "Tenho um navio para entrar no hidrolift no preia-mar das 20:03. O navio tem 45 m de boca, pode manobrar?",
        "expected_summary": "A ficha bloqueia pela boca antes de discutir a hora.",
        "source": "core/operational_diagnostics.py + IT-014",
        "expected_tokens": ("Bloqueio dimensional", "boca maxima 32 m", "Boca: 45 m"),
    },
    {
        "id": "diagnostic-ecooil-two-tugs",
        "group": "Diagnostico operacional",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "operational_diagnostic",
        "label": "Diagnostico Eco-Oil com 2 rebocadores",
        "question": "Entrada para Eco-Oil com 2 rebocadores pode avancar?",
        "expected_summary": "A ficha deve chamar o minimo pratico local de 3 rebocadores.",
        "source": "core/operational_diagnostics.py + knowledge/tug_operational_guidance.json",
        "expected_tokens": ("Eco-Oil: usar sempre no minimo 3 rebocadores", "Rebocadores insuficientes"),
    },
    {
        "id": "diagnostic-tanquisado-two-tugs",
        "group": "Diagnostico operacional",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "operational_diagnostic",
        "label": "Diagnostico Tanquisado com 2 rebocadores",
        "question": "Entrada para Tanquisado com 2 rebocadores pode avancar?",
        "expected_summary": "A ficha deve chamar o minimo pratico local de 3 rebocadores.",
        "source": "core/operational_diagnostics.py + knowledge/tug_operational_guidance.json",
        "expected_tokens": ("Tanquisado: usar sempre no minimo 3 rebocadores", "Rebocadores insuficientes"),
    },
    {
        "id": "diagnostic-route-reponto-lead-time",
        "group": "Diagnostico operacional",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "operational_diagnostic",
        "label": "Diagnostico percurso e reponto",
        "question": "Navio do Fundeadouro Norte para a Lisnave deve sair quando para chegar ao reponto das 20:03?",
        "expected_summary": "A ficha deve validar duracao origem-destino e hora de chegada ao ponto critico.",
        "source": "core/operational_diagnostics.py + knowledge/Notas_Pilotagem.txt",
        "expected_tokens": ("Percurso/duracao", "cerca de 1 hora e 30 minutos", "fase critica no cais/doca"),
    },
    {
        "id": "diagnostic-secil-reponto",
        "group": "Diagnostico operacional",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "operational_diagnostic",
        "label": "Diagnostico SECIL com reponto",
        "question": "Entrada para a SECIL W marcada para as 13:30, tenho de ir ao reponto?",
        "expected_summary": "A ficha deve tratar a SECIL como local critico de reponto e distinguir Oeste/Este.",
        "source": "core/operational_diagnostics.py + IT-009_Secil.txt",
        "expected_tokens": ("Local: SECIL", "SECIL W/Oeste", "todos os navios atracam proximo do reponto"),
    },
    {
        "id": "diagnostic-alstom-mandatory-rules",
        "group": "Diagnostico operacional",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "operational_diagnostic",
        "label": "Diagnostico ALSTOM regras obrigatorias",
        "question": "Entrada para a Alstom desde a Barra com vento 15 kts pode avançar?",
        "expected_summary": "A ficha deve aplicar estibordo, preia-mar, limite de vento, dia e uma manobra por reponto.",
        "source": "core/operational_diagnostics.py + IT-038_Alstom.txt",
        "expected_tokens": (
            "Local: ALSTOM",
            "atracam apenas por estibordo",
            "reponto de preia-mar",
            "1h30",
            "inferior a 15 kt",
            "atinge/excede o limite pratico",
        ),
    },
    {
        "id": "diagnostic-secil-isolates-old-lisnave-context",
        "group": "Diagnostico operacional",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "operational_diagnostic",
        "label": "Diagnostico SECIL sem herdar Lisnave",
        "question": "Marquei manobra de entrada para a Secil E as 1925. Está correto?",
        "history": [
            {
                "role": "user",
                "content": "Um navio na LISNAVE de 300 m manobra com quantos rebocadores normalmente?",
            },
            {"role": "assistant", "content": "Recomendo 6 rebocadores grandes."},
            {"role": "user", "content": "Com nevoeiro em porto posso avancar?"},
        ],
        "expected_summary": "A ficha deve tratar a SECIL E como novo caso e nao herdar Lisnave, nevoeiro ou rebocadores antigos.",
        "source": "core/chat_context_scope.py + core/operational_diagnostics.py",
        "expected_tokens": ("Local: SECIL", "Doca/cais: SECIL E/Este", "Hora referida: 19:25"),
        "forbidden_tokens": ("Local: LISNAVE", "6 rebocador", "nevoeiro"),
    },
    {
        "id": "conversation-context-sapec-non-imo-follow-up",
        "group": "Contexto conversacional",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "conversation_reasoning",
        "label": "Follow-up SAPEC carga não IMO",
        "question": "E carga não IMO",
        "history": [
            {
                "role": "user",
                "content": "Um navio com 9,2m de calado pode atracar na SAPEC Líquidos?",
            },
            {
                "role": "assistant",
                "content": "Depende se a carga é IMO ou não IMO e da altura de água.",
            },
            {"role": "user", "content": "E carga não IMO"},
        ],
        "expected_summary": "A ficha deve assumir continuidade SAPEC quando o follow-up curto não conflita.",
        "source": "core/chat_context_scope.py + core/chat_reasoning.py",
        "expected_tokens": (
            "Ficha de contexto provável",
            "Premissa de continuidade",
            "SAPEC / TPS-TGL",
            "Calado: 9,2 m.",
            "Carga: não IMO.",
        ),
    },
    {
        "id": "conversation-context-new-case-asks-confirmation",
        "group": "Contexto conversacional",
        "risk": "Alto",
        "mode": "Manual guiado",
        "runner": "manual",
        "label": "Mudança de caso sem herança indevida",
        "question": "Após falar da Lisnave, perguntar: Marquei entrada para Secil E às 19:25. Está correto?",
        "expected_summary": "Deve tratar Secil E como novo caso, sem herdar rebocadores, nevoeiro ou LOA da Lisnave.",
        "source": "core/chat_context_scope.py + /new",
        "expected_tokens": (),
    },
    {
        "id": "secil-e-entry-1925-reponto",
        "group": "SECIL e reponto",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Entrada Secil E 19:25 validada contra reponto",
        "question": "Marquei manobra de entrada para a Secil E as 1925. Está correta a hora?",
        "expected_summary": "A resposta deve validar a hora pelo reponto/antecedencia e nao apenas por inexistir proibicao noturna.",
        "source": "IT-009_Secil.txt + marés live quando disponíveis",
        "expected_origin": "secil_entry_timing",
        "expected_tokens": ("Secil E", "30-45 min", "não é apenas ser dia/noite"),
        "forbidden_tokens": ("não há proibição", "horário das 19:25 é, portanto, permitido"),
    },
    {
        "id": "alstom-barra-preia-mar-lead-time",
        "group": "Percursos e repontos",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "ALSTOM desde a Barra para preia-mar",
        "question": "Quanto tempo da Barra para o Cais Alstom para apanhar o reponto de preia-mar?",
        "expected_summary": "Usa a antecedencia operacional obrigatoria da ALSTOM, nao uma duracao generica do Canal Norte.",
        "source": "IT-038_Alstom.txt",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("Cais ALSTOM", "1 hora e 30 minutos antes da preia-mar", "reponto de preia-mar"),
    },
    {
        "id": "distance-tms1-alstom",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Distancia TMS 1 - ALSTOM",
        "question": "Qual a distância do TMS 1 até à Alstom?",
        "expected_summary": "Usa a referencia direta de segmento TMS 1 -> ALSTOM.",
        "source": "knowledge/Notas_Pilotagem.txt",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("TMS 1", "Cais ALSTOM", "3,5 milhas náuticas", "pode ser somada"),
    },
    {
        "id": "distance-tms1-sapec",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Distancia TMS 1 - SAPEC",
        "question": "Qual a distância do TMS 1 até à SAPEC?",
        "expected_summary": "Usa a referencia direta de segmento TMS 1 -> SAPEC.",
        "source": "knowledge/Notas_Pilotagem.txt",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("TMS 1", "SAPEC", "2,2 milhas náuticas", "pode ser somada"),
    },
    {
        "id": "distance-tms1-praias",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Distancia TMS 1 - Praias do Sado",
        "question": "Qual a distância do TMS 1 até às Praias do Sado?",
        "expected_summary": "Usa a referencia direta de segmento TMS 1 -> Praias do Sado.",
        "source": "knowledge/Notas_Pilotagem.txt",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("TMS 1", "Praias do Sado", "1,6 milhas náuticas", "pode ser somada"),
    },
    {
        "id": "distance-tms1-autoeuropa",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Distancia TMS 1 - Autoeuropa",
        "question": "Qual a distância do TMS 1 até à Autoeuropa em NM?",
        "expected_summary": "Aceita NM como unidade de distancia e usa a referencia direta.",
        "source": "knowledge/Notas_Pilotagem.txt",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("TMS 1", "Autoeuropa", "1,0 milha náutica", "pode ser somada"),
    },
    {
        "id": "distance-tms1-joao-farto",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Distancia TMS 1 - Boia Joao Farto",
        "question": "Qual a distância do TMS 1 até à bóia João Farto?",
        "expected_summary": "Usa a referencia direta de segmento TMS 1 -> Boia Joao Farto.",
        "source": "knowledge/Notas_Pilotagem.txt",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("TMS 1", "Bóia João Farto", "1,6 milhas náuticas", "pode ser somada"),
    },
    {
        "id": "distance-tms1-outao",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Distancia TMS 1 - Outao",
        "question": "Qual a distância do TMS 1 até ao Outão?",
        "expected_summary": "Usa a referencia direta de segmento TMS 1 -> Outao.",
        "source": "knowledge/Notas_Pilotagem.txt",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("TMS 1", "Outão", "3,0 milhas náuticas", "pode ser somada"),
    },
    {
        "id": "distance-tms1-fora-barra",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Distancia TMS 1 - fora da Barra",
        "question": "Qual a distância do TMS 1 até fora da Barra?",
        "expected_summary": "Usa a referencia direta de segmento TMS 1 -> fora da Barra/Pilar 2.",
        "source": "knowledge/Notas_Pilotagem.txt",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("TMS 1", "fora da Barra", "6,0 milhas náuticas", "pode ser somada"),
    },
    {
        "id": "route-plan-joao-farto-alstom",
        "group": "Percursos e distancias",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Planeamento Canal Norte Joao Farto - ALSTOM",
        "question": "Estou na Bóia João Farto para a Alstom, quanto falta?",
        "expected_summary": "Calcula a distancia restante por pernadas e mostra rumos de entrada/saida.",
        "source": "knowledge/setubal_route_planning.json",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("Canal Norte", "5,0 milhas náuticas", "Bóia João Farto -> Bóia 1CC", "rumo 040°"),
        "forbidden_tokens": ("rumo inverso",),
    },
    {
        "id": "route-plan-full-north-channel",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Canal Norte completo desde embarque",
        "question": "Da posição de embarque até ao fim do canal norte, qual a distância?",
        "expected_summary": "Soma todas as pernadas desde a pilot station ate ao fim do Canal Norte.",
        "source": "knowledge/setubal_route_planning.json",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("Pilot station", "Canal Norte", "10,3 milhas náuticas", "SAPEC -> Cais ALSTOM"),
    },
    {
        "id": "route-plan-tms2-north-channel-position",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Referencia TMS2 no Canal Norte",
        "question": "Da entrada da barra ao TMS2 pelo canal norte, quanto falta?",
        "expected_summary": "Situa TMS1/TMS2 no Canal Norte antes da Autoeuropa, como referencia operacional.",
        "source": "knowledge/setubal_route_planning.json",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("Canal Norte", "6,5 milhas náuticas", "TMS 1 -> Bóia 5CC", "Bóia 5CC -> TMS 2"),
    },
    {
        "id": "route-plan-full-south-channel",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Canal Sul ate Boia 14CS",
        "question": "Da entrada da barra até ao fim do canal sul, quais as milhas?",
        "expected_summary": "Soma o Canal Sul desde o Pilar 2 ate a Boia 14CS.",
        "source": "knowledge/setubal_route_planning.json",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("Pilar 2", "Bóia 14CS", "10,0 milhas náuticas", "Bóia 12CS -> Bóia 14CS"),
    },
    {
        "id": "route-plan-south-lisnave-eta",
        "group": "Percursos e distancias",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "ETA Boia 12CS - Lisnave",
        "question": "Da Bóia 12 CS para a Lisnave a 6 nós, saída às 10:00, qual ETA?",
        "expected_summary": "Calcula distancia, pernadas restantes e ETA com velocidade indicada.",
        "source": "knowledge/setubal_route_planning.json",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("Canal Sul para LISNAVE", "1,0 milha náutica", "A 6,0 kt", "ETA ao destino: 10:10"),
    },
    {
        "id": "route-plan-south-reverse-headings",
        "group": "Percursos e distancias",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Rumos inversos Lisnave - Pilar 2",
        "question": "Da Lisnave para o Pilar 2, quais os rumos de saída?",
        "expected_summary": "Aplica +180 graus aos rumos de entrada para sair no sentido inverso.",
        "source": "knowledge/setubal_route_planning.json",
        "expected_origin": "operational_route_transit",
        "expected_tokens": ("10,5 milhas náuticas", "rumo 210°", "rumo 245°", "rumo 220°"),
        "forbidden_tokens": ("rumo inverso",),
    },
    {
        "id": "route-plan-cross-channel-lisnave-tms1",
        "group": "Percursos e distancias",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Ligacao Lisnave - TMS1 via Joao Farto",
        "question": "Vou mudar um navio da LISNAVE para o TMS1. Se for a 5 kts quanto tempo levo de um cais ao outro?",
        "expected_summary": "Liga Canal Sul e Canal Norte pela Boia Joao Farto e calcula tempo a velocidade indicada.",
        "source": "knowledge/setubal_route_planning.json",
        "expected_origin": "operational_route_transit",
        "expected_tokens": (
            "Canal Sul / Canal Norte via Bóia João Farto",
            "7,9 milhas náuticas",
            "Bóia 4CS -> Bóia João Farto",
            "Bóia João Farto -> Bóia 1CC",
            "A 5,0 kt, duração estimada: 1 h 35 min.",
        ),
        "forbidden_tokens": ("rumo inverso",),
    },
    {
        "id": "tanquisado-east-side-push",
        "group": "Rebocadores e vento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Tanquisado a sair com E forte",
        "question": "A sair de Tanquisado com vento E forte, onde meto o reboque?",
        "expected_summary": "Mantem proa+popa estabelecidos e aponta terceiro rebocador a empurrar ao costado na largada dos cabos.",
        "source": "knowledge/tug_operational_guidance.json",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": (
            "Tanquisado com 3 rebocadores",
            "1 a proa e 1 a popa",
            "terceiro",
            "Tanquisado a sair com vento E forte",
            "1 rebocador a empurrar ao costado",
            "largada dos cabos",
        ),
    },
    {
        "id": "ecooil-west-side-push",
        "group": "Rebocadores e vento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Eco-Oil a sair com W forte",
        "question": "A sair da Eco-Oil com vento W forte, onde meto o reboque?",
        "expected_summary": "Mantem proa+popa estabelecidos e aponta terceiro rebocador a empurrar ao costado, como efeito oposto da Tanquisado.",
        "source": "knowledge/tug_operational_guidance.json",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": (
            "Eco-Oil com 3 rebocadores",
            "1 a proa e 1 a popa",
            "terceiro",
            "Eco-Oil a sair com vento W forte",
            "1 rebocador a empurrar ao costado",
            "oposto da Tanquisado",
        ),
    },
    {
        "id": "fourth-tug-side-push-or-standby",
        "group": "Rebocadores e vento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "4.º rebocador: costado ou standby",
        "question": "Tenho 4 rebocadores na Tanquisado, onde meto o quarto reboque?",
        "expected_summary": "O quarto rebocador so empurra ao costado se houver espaco sem criar ma vizinhanca; senao fica standby.",
        "source": "knowledge/tug_operational_guidance.json",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": ("4.º rebocador", "empurrar ao costado", "ma vizinhanca", "standby"),
    },
    {
        "id": "five-tugs-lisnave-bow-south",
        "group": "Rebocadores e vento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Lisnave 5 rebocadores proa a sul",
        "question": "Na Lisnave com 5 rebocadores e navio com proa a sul, onde meto os rebocadores?",
        "expected_summary": "Com proa a sul, ficam 2 a proa e 1 a popa estabelecidos.",
        "source": "knowledge/tug_operational_guidance.json",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": ("2 rebocadores a proa", "1 rebocador a popa", "standby"),
    },
    {
        "id": "five-tugs-lisnave-stern-south",
        "group": "Rebocadores e vento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Lisnave 5 rebocadores popa a sul",
        "question": "Na Lisnave com 5 rebocadores e navio com popa a sul, onde meto os rebocadores?",
        "expected_summary": "Com popa a sul/proa a norte, ficam 2 a popa e 1 a proa estabelecidos.",
        "source": "knowledge/tug_operational_guidance.json",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": ("2 rebocadores a popa", "1 rebocador a proa", "standby"),
    },
    {
        "id": "six-tugs-positioning",
        "group": "Rebocadores e vento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "6 rebocadores: 2+2 e dois ao costado",
        "question": "Com 6 rebocadores, onde meto os rebocadores?",
        "expected_summary": "Com 6, ficam 2 estabelecidos a proa e 2 a popa, mais 2 a empurrar ao costado.",
        "source": "knowledge/tug_operational_guidance.json",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": ("2 rebocadores a proa", "2 rebocadores a popa", "os outros 2 empurram ao costado"),
    },
    {
        "id": "small-deep-no-bowthruster",
        "group": "Rebocadores e vento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Navio pequeno, fundo e sem bowthruster",
        "question": "Navio sem bowthruster de 110m e calado 8m precisa de quantos rebocadores?",
        "expected_summary": "Usa 1 rebocador grande de 35 t, nao rebocador pequeno.",
        "source": "knowledge/tug_operational_guidance.json",
        "expected_origin": "operational_tug_guidance",
        "expected_tokens": ("Recomendo 1 rebocador grande", "35 t", "nao rebocador pequeno"),
    },
    {
        "id": "fog-suspends-maneuver",
        "group": "Limites de seguranca",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Nevoeiro suspende a manobra",
        "question": "Um roro vai sair agora da Autoeuropa. Tem 200 m e ja pus 2 reboques. Pode sair? Ah, mas ficou nevoeiro!",
        "expected_summary": "Bloqueia a decisao: rebocadores nao compensam nevoeiro.",
        "source": "knowledge/operational_safety_limits.json",
        "expected_origin": "operational_safety_limit",
        "expected_tokens": ("Não.", "nevoeiro em porto", "manobras ficam suspensas", "rebocadores não elimina"),
    },
    {
        "id": "fog-underway-colreg-procedure",
        "group": "Limites de seguranca",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Nevoeiro súbito em navegação: COLREG",
        "question": "Se um navio for apanhado no meio do nevoeiro a navegar, que procedimentos deve adoptar segundo a COLREG?",
        "expected_summary": "Nao bloqueia apenas com suspensao: aplica RIEAM/COLREG, sinais de nevoeiro e decisao local cais/fundeadouro/abortar.",
        "source": "knowledge/operational_safety_limits.json + knowledge/RIEAM_COLREG_Regras_Estrada.txt",
        "expected_origin": "fog_underway_procedure",
        "expected_tokens": (
            "Regra 19",
            "Regra 35",
            "1 som prolongado",
            "avaliar posição",
            "cais de destino",
            "fundeadouro",
            "abortar antes de entrar",
            "VHF 73",
        ),
    },
    {
        "id": "wind-31-suspends-maneuver",
        "group": "Limites de seguranca",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "31 kt suspende mesmo com mais rebocadores",
        "question": "Se estiver 31 kts de vento, posso sair com 4 rebocadores?",
        "expected_summary": "Suspende acima de 25 kt e so admite retoma abaixo de 25 kt.",
        "source": "knowledge/operational_safety_limits.json",
        "expected_origin": "operational_safety_limit",
        "expected_tokens": ("31 kt", "manobras ficam suspensas", "mais rebocadores não anula", "menos de 25 kt"),
    },
    {
        "id": "colreg-narrow-channel-overtaking",
        "group": "RIEAM/COLREG",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "COLREG ultrapassagem canal estreito",
        "question": "Pelo COLREG, quero ultrapassar num canal estreito. Que sinais uso?",
        "expected_summary": "Interpreta Regra 9 e Regra 34 com sinais de ultrapassagem e acordo.",
        "source": "knowledge/RIEAM_COLREG_Regras_Estrada.txt",
        "expected_origin": "colreg_interpretation",
        "expected_tokens": ("Regra 9", "Regra 34", "2 sons prolongados + 1 curto", "5 sons curtos"),
    },
    {
        "id": "colreg-dredging-safe-side",
        "group": "RIEAM/COLREG",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "COLREG dragagem bordo livre",
        "question": "No RIEAM, uma draga com vermelho vermelho e verde verde indica o quê?",
        "expected_summary": "Interpreta Regra 27: vermelho marca obstrucao e verde marca bordo livre.",
        "source": "knowledge/RIEAM_COLREG_Regras_Estrada.txt",
        "expected_origin": "colreg_interpretation",
        "expected_tokens": ("Regra 27", "bordo obstruído", "bordo por onde se pode passar"),
    },
    {
        "id": "visibility-one-km-threshold",
        "group": "Limites de seguranca",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "knowledge_json",
        "label": "Limiar live de visibilidade",
        "question": "Se o live feed indicar 1,0 km de visibilidade, o bot trata como visibilidade reduzida?",
        "expected_summary": "O limiar tecnico fica fixado em 1,0 km para interpretar dados live.",
        "source": "knowledge/operational_safety_limits.json",
        "source_path": "operational_safety_limits.json",
        "expected_tokens": ("fog_visibility_km_reference", "1.0", "visibilidade operacional"),
    },
    {
        "id": "loss-of-engine-emergency",
        "group": "Limites de seguranca",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Perda de maquina: ferro e VTS",
        "question": "O navio ficou sem maquina e ainda nao tem rebocadores perto. O que aconselhas de imediato?",
        "expected_summary": "Prioriza largar ferro, VTS no canal 73 e nao cai na resposta de posicionamento de rebocadores.",
        "source": "knowledge/operational_safety_limits.json",
        "expected_origin": "operational_emergency_response",
        "expected_tokens": ("largar ferro imediatamente", "VHF 73", "canal 14", "canal 71"),
    },
    {
        "id": "loss-of-bow-emergency",
        "group": "Limites de seguranca",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Emergencia: perda de bow",
        "question": "Se o navio perder bow na manobra, o que faz?",
        "expected_summary": "A resposta manda largar ferro com 1 manilha na agua e reportar nos canais certos.",
        "source": "knowledge/operational_safety_limits.json",
        "expected_origin": "operational_emergency_response",
        "expected_tokens": ("1 manilha na agua", "VHF 73", "canal 14"),
    },
    {
        "id": "parted-lines-emergency",
        "group": "Limites de seguranca",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Emergencia: cabos partidos",
        "question": "Se partirem cabos na manobra, qual e a resposta imediata?",
        "expected_summary": "A resposta prepara ferro, novos cabos, lancha de cabos e rebocadores de atencao.",
        "source": "knowledge/operational_safety_limits.json",
        "expected_origin": "operational_emergency_response",
        "expected_tokens": ("preparar novos cabos", "lancha de cabos", "rebocadores de atencao"),
    },
    {
        "id": "parted-tug-line-emergency",
        "group": "Limites de seguranca",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Emergencia: cabo do reboque",
        "question": "Se o cabo do reboque partir, o que fazemos?",
        "expected_summary": "A resposta manda preparar cabo de amarracao do navio, solto e sem guincho, para o reboque.",
        "source": "knowledge/operational_safety_limits.json",
        "expected_origin": "operational_emergency_response",
        "expected_tokens": ("cabo de amarracao do navio", "sem estar no guincho", "fazer firme ao reboque"),
    },
    {
        "id": "grounding-emergency",
        "group": "Limites de seguranca",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Emergencia: encalhe",
        "question": "Se encalhar, qual e a resposta imediata?",
        "expected_summary": "A resposta manda ver profundidades, avaliar a mare, chamar rebocadores e considerar ferro.",
        "source": "knowledge/operational_safety_limits.json",
        "expected_origin": "operational_emergency_response",
        "expected_tokens": ("verificar profundidades", "avaliar a situacao com a mare", "chamar rebocadores"),
    },
    {
        "id": "collision-emergency",
        "group": "Limites de seguranca",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Emergencia: colisao",
        "question": "Se colidir na manobra, qual e a resposta imediata?",
        "expected_summary": "A resposta manda largar ferro imediatamente, chamar rebocadores e esquivar/afastar.",
        "source": "knowledge/operational_safety_limits.json",
        "expected_origin": "operational_emergency_response",
        "expected_tokens": ("largar ferro imediatamente", "chamar rebocadores", "esquivar-se"),
    },
    {
        "id": "shiphandling-practical-source",
        "group": "Fontes de conhecimento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "knowledge_text",
        "label": "Notes on Shiphandling incorporado",
        "question": "A fonte de shiphandling cobre pivot point, reboques, bow thruster e ferro?",
        "expected_summary": "Garante que a sintese operacional extraida do PDF esta presente no conhecimento indexavel.",
        "source": "knowledge/Notes_on_Shiphandling_Practical.txt",
        "source_path": "Notes_on_Shiphandling_Practical.txt",
        "expected_tokens": ("pivot point", "1 a proa + 1 a popa", "Bow thruster", "largar ferro", "VHF 73"),
    },
    {
        "id": "navigation-lights-source",
        "group": "Fontes de conhecimento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "knowledge_text",
        "label": "Lista de Luzes Setubal incorporada",
        "question": "A fonte de balizagem/luzes de Setubal esta indexavel no conhecimento?",
        "expected_summary": "Garante que a lista de luzes contem IALA A, 1CN, 2CS e Doca Pesca.",
        "source": "knowledge/Lista_Luzes_Setubal.txt",
        "source_path": "Lista_Luzes_Setubal.txt",
        "expected_tokens": ("SISTEMA IALA", "IALA A", "Boia N.º 1CN", "Fl G 3s", "Boia N.º 2CS", "Doca Pesca"),
    },
    {
        "id": "navigation-light-1cn-direct",
        "group": "Fontes de conhecimento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Caracteristica da Boia 1CN",
        "question": "Qual e a caracteristica da Boia 1CN?",
        "expected_summary": "Responde pela tabela estruturada de luzes e inclui IALA A.",
        "source": "knowledge/navigation_lights_setubal.json",
        "expected_origin": "navigation_lights",
        "expected_tokens": ("Boia N.º 1CN", "Fl G 3s", "38º30,33'N", "8º51,46'W", "IALA A"),
    },
    {
        "id": "navigation-lights-iala-direct",
        "group": "Fontes de conhecimento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Setubal usa IALA A",
        "question": "Em Setubal usamos que sistema IALA?",
        "expected_summary": "Confirma explicitamente IALA A e a leitura bombordo/estibordo.",
        "source": "knowledge/navigation_lights_setubal.json",
        "expected_origin": "navigation_lights",
        "expected_tokens": ("IALA A", "bombordo", "vermelhas", "estibordo", "verdes"),
    },
    {
        "id": "setubal-history-culture-source",
        "group": "Fontes de conhecimento",
        "risk": "Medio",
        "mode": "Automatico",
        "runner": "knowledge_text",
        "label": "Historia e cultura de Setubal",
        "question": "Existe contexto historico/cultural de Setubal sem misturar com regras tecnicas?",
        "expected_summary": "Garante contexto sobre Cetobriga, Forte de Sao Filipe, Outao e choco frito, com nota de nao uso operacional.",
        "source": "knowledge/Setubal_Historia_Cultura.txt",
        "source_path": "Setubal_Historia_Cultura.txt",
        "expected_tokens": (
            "Cetobriga",
            "Forte de Sao Filipe",
            "Outao",
            "choco frito",
            "Nao deve ser usado para validar manobras",
        ),
    },
    {
        "id": "rieam-colreg-road-rules-source",
        "group": "Fontes de conhecimento",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "knowledge_text",
        "label": "RIEAM/COLREG regras da estrada",
        "question": "A fonte RIEAM/COLREG cobre as regras centrais de anti colisao?",
        "expected_summary": "Garante vigia, velocidade segura, risco, roda a roda, rumos cruzados, canais e visibilidade reduzida.",
        "source": "knowledge/RIEAM_COLREG_Regras_Estrada.txt",
        "source_path": "RIEAM_COLREG_Regras_Estrada.txt",
        "expected_tokens": (
            "Regra 5 - Vigia",
            "Regra 7 - Risco de abalroamento",
            "Regra 14 - Roda a roda",
            "guinar para estibordo",
            "Regra 15 - Rumos cruzados",
            "ve o outro por estibordo",
            "Regra 19",
            "visibilidade reduzida",
            "Regra 34 - Ultrapassagem em canal estreito",
            "Regra 27 - Dragagem/trabalhos submarinos",
            "Anexo IV - Perigo",
        ),
    },
    {
        "id": "navigation-basics-source",
        "group": "Noções básicas de navegação",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "knowledge_text",
        "label": "Unidades náuticas e Beaufort incorporadas",
        "question": "A fonte de unidades e Beaufort cobre as conversões essenciais?",
        "expected_summary": "Garante milha náutica, nó, jarda, manilha e escala Beaufort.",
        "source": "knowledge/Nocoes_Basicas_Navegacao_Unidades.txt",
        "source_path": "Nocoes_Basicas_Navegacao_Unidades.txt",
        "expected_tokens": (
            "1 milha nautica = 1852 m",
            "1 no = 1 milha nautica por hora",
            "1 jarda = 0,9144 m",
            "1 manilha = 27,5 m",
            "Beaufort 6: 22-27 kt",
        ),
    },
    {
        "id": "navigation-units-km-to-nautical-miles",
        "group": "Noções básicas de navegação",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Conversão km para milhas náuticas",
        "question": "20 km são quantas milhas náuticas?",
        "expected_summary": "Converte km para milhas náuticas sem depender de interpretação textual.",
        "source": "knowledge/Nocoes_Basicas_Navegacao_Unidades.txt",
        "expected_origin": "navigation_basics",
        "expected_tokens": ("20 km", "10,80 milhas náuticas", "1 milha náutica = 1852 m"),
    },
    {
        "id": "navigation-units-shackle-to-meters",
        "group": "Noções básicas de navegação",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Manilha para metros",
        "question": "1 manilha são quantos metros?",
        "expected_summary": "Fixa a equivalência operacional da manilha.",
        "source": "knowledge/Nocoes_Basicas_Navegacao_Unidades.txt",
        "expected_origin": "navigation_basics",
        "expected_tokens": ("1 manilha", "27,5 metros", "1 manilha = 27,5 m"),
    },
    {
        "id": "navigation-units-yards-to-meters",
        "group": "Noções básicas de navegação",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Jardas para metros",
        "question": "100 jardas são quantos metros?",
        "expected_summary": "Converte jardas para metros.",
        "source": "knowledge/Nocoes_Basicas_Navegacao_Unidades.txt",
        "expected_origin": "navigation_basics",
        "expected_tokens": ("100 jardas", "91,44 metros", "1 jarda = 0,9144 m"),
    },
    {
        "id": "navigation-beaufort-force-six",
        "group": "Noções básicas de navegação",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "direct_operational",
        "label": "Beaufort força 6",
        "question": "Força 6 Beaufort equivale a quantos nós?",
        "expected_summary": "Reconhece Beaufort e devolve kt/kmh.",
        "source": "knowledge/Nocoes_Basicas_Navegacao_Unidades.txt",
        "expected_origin": "navigation_basics",
        "expected_tokens": ("Beaufort 6", "22-27 kt", "39-49 km/h", "vento fresco"),
    },
    {
        "id": "ecooil-profile",
        "group": "Cais criticos",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "berth_profile",
        "label": "Perfil Eco-Oil",
        "question": "Que regras tenho para a Eco-Oil?",
        "expected_summary": "Calado, reponto, noite e defensas aparecem a partir do IT-008.",
        "source": "knowledge/berth_profiles.json",
        "profile_query": "Eco-Oil",
        "expected_tokens": ("IT-008_EcoOil.txt", "Calado maximo", "repontos de mare", "Atracacao noturna proibida"),
    },
    {
        "id": "tanquisado-profile",
        "group": "Cais criticos",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "berth_profile",
        "label": "Perfil Tanquisado",
        "question": "Quais os limites da Tanquisado?",
        "expected_summary": "Calado, reponto, noite e regra de saida fora de reponto aparecem a partir do IT-010.",
        "source": "knowledge/berth_profiles.json",
        "profile_query": "Tanquisado",
        "expected_tokens": ("IT-010_Tanquisado.txt", "Calado diurno praticavel", "repontos de mare", "Saida fora de reponto"),
    },
    {
        "id": "lisnave-profile",
        "group": "Cais criticos",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "berth_profile",
        "label": "Perfil Lisnave",
        "question": "Que regras ha na Lisnave para docas e cais?",
        "expected_summary": "Reponto, LOA noturna, calado variavel e orientacao operacional saem do IT-014.",
        "source": "knowledge/berth_profiles.json",
        "profile_query": "Lisnave",
        "expected_tokens": ("IT-014_Lisnave.txt", "repontos de mare", "280 m", "Calado"),
    },
    {
        "id": "lisnave-dock-checklist",
        "group": "Checklist de manobras",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "maneuver_checklist",
        "label": "Doca Lisnave com 3 rebocadores",
        "question": "A checklist da manobra avisa se uma doca Lisnave estiver com 3 rebocadores?",
        "expected_summary": "A checklist marca cautela, minimo de 4 rebocadores e proa a norte.",
        "source": "Pagina da manobra + domain/lisnave_rules.py",
        "fixture": "lisnave_dock_three_tugs",
        "expected_tokens": ("Lisnave - doca", "pelo menos 4 rebocadores", "proa a norte"),
    },
    {
        "id": "lisnave-d31-bow-south-checklist",
        "group": "Checklist de manobras",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "maneuver_checklist",
        "label": "D31/D32/D33 Lisnave com proa a sul",
        "question": "A checklist distingue D31/D32/D33 das docas 20/21/22 na orientação?",
        "expected_summary": "D31/D32/D33 entram via Hidrolift e ficam com proa a sul, apesar de serem docas Lisnave.",
        "source": "Pagina da manobra + domain/lisnave_rules.py",
        "fixture": "lisnave_dock31_four_tugs",
        "expected_tokens": ("D31, D32 e D33", "Hidrolift", "proa a sul"),
    },
    {
        "id": "lisnave-quay-checklist",
        "group": "Checklist de manobras",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "maneuver_checklist",
        "label": "Cais Lisnave com orientacao certa",
        "question": "A checklist distingue cais Lisnave de doca seca?",
        "expected_summary": "A checklist mostra proa a sul e nao aplica minimo de doca seca ao cais.",
        "source": "Pagina da manobra + domain/lisnave_rules.py",
        "fixture": "lisnave_quay_three_tugs",
        "expected_tokens": ("Orientação Lisnave", "proa a sul", "não se aplica às docas"),
    },
    {
        "id": "ecooil-checklist",
        "group": "Checklist de manobras",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "maneuver_checklist",
        "label": "Eco-Oil na checklist",
        "question": "A checklist puxa regras Eco-Oil para uma entrada?",
        "expected_summary": "A checklist inclui IT-008, calados, reponto e proibicao noturna de atracacao.",
        "source": "Pagina da manobra + knowledge/berth_profiles.json",
        "fixture": "ecooil_entry",
        "expected_tokens": ("IT-008_EcoOil.txt", "Calado maximo", "repontos de mare", "Atracacao noturna proibida"),
    },
    {
        "id": "tanquisado-checklist",
        "group": "Checklist de manobras",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "maneuver_checklist",
        "label": "Tanquisado na checklist",
        "question": "A checklist puxa regras Tanquisado para uma saida?",
        "expected_summary": "A checklist inclui IT-010, calado maximo e regra de saida fora de reponto.",
        "source": "Pagina da manobra + knowledge/berth_profiles.json",
        "fixture": "tanquisado_departure",
        "expected_tokens": ("IT-010_Tanquisado.txt", "calado maximo absoluto", "Saida fora de reponto", "preia-mar precedente"),
    },
    {
        "id": "validar-manobra-tanquisado-entry",
        "group": "Comandos slash",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "slash_validation",
        "label": "/validar-manobra Tanquisado com 2 rebocadores",
        "question": "/validar-manobra para entrada GALBOT em Tanquisado com só 2 rebocadores",
        "expected_summary": "A resposta deve priorizar IT-010/checklist, apanhar o novo mínimo prático de 3 rebocadores e não depender do histórico.",
        "source": "Comando /validar-manobra + knowledge/berth_profiles.json",
        "fixture": "tanquisado_entry_validation",
        "expected_tokens": (
            "Base documental acionada: IT-010_Tanquisado.txt",
            "Parecer operacional",
            "Decisão: NÃO AVANÇAR",
            "A marcação acerta a janela de reponto",
            "mínimo prático para Tanquisado",
            "Recomendação operacional",
            "histórico não usado como regra principal",
            "Janela planeada",
        ),
    },
    {
        "id": "validar-manobra-tanquisado-fora-reponto",
        "group": "Comandos slash",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "slash_validation",
        "label": "/validar-manobra Tanquisado fora do reponto",
        "question": "/validar-manobra para entrada Tanquisado marcada cedo demais para a maré",
        "expected_summary": "A resposta deve bloquear a validação quando a chegada estimada falha o reponto.",
        "source": "Comando /validar-manobra + tabela de marés + IT-010",
        "fixture": "tanquisado_entry_bad_tide",
        "expected_tokens": (
            "Decisão: NÃO AVANÇAR",
            "A marcação não cai suficientemente em cima do reponto",
            "Janela vencida com pontos pendentes",
            "IT-010_Tanquisado.txt",
        ),
    },
    {
        "id": "validar-manobra-ecooil-entry",
        "group": "Comandos slash",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "slash_validation",
        "label": "/validar-manobra Eco-Oil",
        "question": "/validar-manobra para entrada Eco-Oil marcada para chegar ao reponto",
        "expected_summary": "A resposta deve aplicar IT-008, reponto e leitura de calado/LOA sem depender do histórico.",
        "source": "Comando /validar-manobra + knowledge/berth_profiles.json",
        "fixture": "ecooil_entry_validation",
        "expected_tokens": (
            "Base documental acionada: IT-008_EcoOil.txt",
            "Decisão: CHECKLIST ORIGINAL OK (JANELA PASSADA)",
            "A marcação acerta a janela de reponto",
            "Calado (atracação)",
        ),
    },
    {
        "id": "validar-manobra-lisnave-doca-rebocadores",
        "group": "Comandos slash",
        "risk": "Critico",
        "mode": "Automatico",
        "runner": "slash_validation",
        "label": "/validar-manobra doca Lisnave",
        "question": "/validar-manobra para entrada de doca Lisnave com só 3 rebocadores",
        "expected_summary": "A resposta deve apanhar a regra prática de doca Lisnave e impedir parecer favorável.",
        "source": "Comando /validar-manobra + domain/lisnave_rules.py + IT-014",
        "fixture": "lisnave_dock_three_tugs_validation",
        "expected_tokens": (
            "Decisão: NÃO AVANÇAR",
            "Lisnave - doca",
            "pelo menos 4 rebocadores",
            "IT-014_Lisnave.txt",
        ),
    },
    {
        "id": "validar-manobra-mudanca-duas-fases",
        "group": "Comandos slash",
        "risk": "Alto",
        "mode": "Automatico",
        "runner": "slash_validation",
        "label": "/validar-manobra mudança",
        "question": "/validar-manobra para mudança Lisnave -> TMS 1 - Cais 4",
        "expected_summary": "A resposta deve tratar mudança como largada da origem e atracação no destino.",
        "source": "Comando /validar-manobra + perfis de cais origem/destino",
        "fixture": "shift_lisnave_to_tms1_validation",
        "expected_tokens": (
            "Mudança: validar o mesmo plano como largada da origem e atracação no destino",
            "LOA (largada da origem)",
            "LOA (atracação no destino)",
            "Regras do cais de origem",
            "Regras do cais de destino",
        ),
    },
    {
        "id": "route-order-north-south",
        "group": "Conversas a testar manualmente",
        "risk": "Alto",
        "mode": "Manual guiado",
        "runner": "manual",
        "label": "Ordem dos cais por canal",
        "question": "Qual a ordem dos cais de entrada pelo Canal Norte e pelo Canal Sul?",
        "expected_summary": "Tem de responder pela fonte de ordem dos cais, separando entrada/saida e Norte/Sul.",
        "source": "knowledge/Ordem_Cais_*.txt",
        "expected_tokens": (),
    },
    {
        "id": "reponto-matrix",
        "group": "Conversas a testar manualmente",
        "risk": "Alto",
        "mode": "Manual guiado",
        "runner": "manual",
        "label": "Marcacoes a reponto",
        "question": "Quando marco uma saida da Doca 22 da Lisnave e uma entrada para Tanquisado?",
        "expected_summary": "Deve combinar a matriz de marcacoes com as regras de cais, sem inventar mares.",
        "source": "knowledge/Marcar_manobra_repontos_mare.txt",
        "expected_tokens": (),
    },
]


def critical_bot_test_matrix() -> list[dict]:
    """Return the curated critical conversation matrix shown in the admin test page."""
    return [dict(item) for item in BOT_CRITICAL_TEST_MATRIX]


def _critical_bot_test_matrix_groups(matrix: list[dict]) -> list[dict]:
    groups: list[dict] = []
    by_group: dict[str, dict] = {}
    for item in matrix:
        group_name = item.get("group") or "Geral"
        group = by_group.setdefault(group_name, {"name": group_name, "items": [], "automatic_count": 0, "manual_count": 0})
        group["items"].append(item)
        if item.get("runner") == "manual":
            group["manual_count"] += 1
        else:
            group["automatic_count"] += 1
    return list(by_group.values())


def _label_now() -> str:
    return datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S")


def _state_from_steps(steps: list[dict]) -> str:
    if any(step.get("state") == "failed" for step in steps):
        return "failed"
    if any(step.get("state") == "warning" for step in steps):
        return "warning"
    return "passed"


def _status_badge(state: str) -> str:
    return {
        "passed": "online",
        "failed": "offline",
        "warning": "degraded",
    }.get(state, "degraded")


def cleanup_operational_test_records() -> dict:
    """Delete previous records created by this controlled admin test suite."""
    started = time.perf_counter()
    deleted: list[dict] = []
    errors: list[str] = []
    try:
        records = services.store.list_port_calls()
    except Exception as exc:
        return {
            "kind": "cleanup",
            "state": "failed",
            "state_label": "Falhou",
            "state_badge": "offline",
            "started_at_label": _label_now(),
            "finished_at_label": _label_now(),
            "duration_ms": 0,
            "deleted": [],
            "deleted_count": 0,
            "errors": [f"Não foi possível listar escalas: {exc}"],
        }

    targets = [
        record
        for record in records
        if str(record.get("vessel_name") or "").strip().upper().startswith(TEST_VESSEL_PREFIX)
    ]
    for record in targets:
        try:
            removed = services.store.delete_port_call(record["id"])
            deleted.append(
                {
                    "id": removed.get("id"),
                    "reference_code": removed.get("reference_code"),
                    "vessel_name": removed.get("vessel_name"),
                }
            )
        except Exception as exc:
            errors.append(f"{record.get('reference_code') or record.get('id')}: {exc}")

    state = "failed" if errors else "passed"
    return {
        "kind": "cleanup",
        "state": state,
        "state_label": "Falhou" if errors else "Limpeza concluída",
        "state_badge": _status_badge(state),
        "started_at_label": _label_now(),
        "finished_at_label": _label_now(),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "deleted": deleted,
        "deleted_count": len(deleted),
        "errors": errors,
    }


def _railway_bot_test_records() -> list[dict]:
    if not RAILWAY_BOT_TEST_FIXTURE.exists():
        return []
    try:
        payload = json.loads(RAILWAY_BOT_TEST_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    records = payload.get("records") if isinstance(payload, dict) else []
    if not isinstance(records, list):
        return []
    normalized: list[dict] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        expected = [str(item) for item in record.get("expected_substrings") or [] if str(item or "").strip()]
        forbidden = [str(item) for item in record.get("forbidden_substrings") or [] if str(item or "").strip()]
        missing = [str(item) for item in record.get("missing_expected") or [] if str(item or "").strip()]
        forbidden_present = [str(item) for item in record.get("forbidden_present") or [] if str(item or "").strip()]
        warnings = [str(item) for item in record.get("warnings") or [] if str(item or "").strip()]
        verdict = str(record.get("verdict") or "review").strip().lower()
        normalized.append(
            {
                **record,
                "number": record.get("number") or index,
                "suite": record.get("suite") or "Railway",
                "group": record.get("group") or "Sem grupo",
                "risk": record.get("risk") or "",
                "question": record.get("question") or "",
                "expected_substrings": expected,
                "forbidden_substrings": forbidden,
                "expected_summary": " · ".join(expected) if expected else "--",
                "forbidden_summary": " · ".join(forbidden) if forbidden else "--",
                "missing_expected": missing,
                "forbidden_present": forbidden_present,
                "warnings": warnings,
                "verdict": verdict,
                "state_badge": {
                    "pass": "online",
                    "passed": "online",
                    "fail": "offline",
                    "failed": "offline",
                    "review": "degraded",
                    "warning": "degraded",
                }.get(verdict, "degraded"),
                "verification_summary": _railway_verification_summary(verdict, missing, forbidden_present, warnings),
                "answer_origin": record.get("answer_origin") or "--",
                "answer_excerpt": record.get("answer_excerpt") or " ".join(str(record.get("answer") or "").split())[:420],
            }
        )
    return normalized


def _railway_verification_summary(
    verdict: str,
    missing: list[str],
    forbidden_present: list[str],
    warnings: list[str],
) -> str:
    if missing:
        return "Faltam: " + "; ".join(missing[:3])
    if forbidden_present:
        return "Proibidos presentes: " + "; ".join(forbidden_present[:3])
    if warnings:
        return "Avisos: " + "; ".join(warnings[:3])
    if verdict in {"pass", "passed"}:
        return "Critérios verificados"
    return "Requer revisão"


def _railway_bot_test_groups(records: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(record["suite"], []).append(record)
    return [
        {
            "name": suite,
            "count": len(items),
            "passed_count": sum(1 for item in items if item["verdict"] in {"pass", "passed"}),
            "failed_count": sum(1 for item in items if item["verdict"] in {"fail", "failed"}),
            "review_count": sum(1 for item in items if item["verdict"] not in {"pass", "passed", "fail", "failed"}),
            "items": items,
        }
        for suite, items in grouped.items()
    ]


def railway_bot_test_log_inventory() -> dict:
    """Return the 150 Railway bot test records used for page inspection and export."""
    records = _railway_bot_test_records()
    passed_count = sum(1 for item in records if item["verdict"] in {"pass", "passed"})
    failed_count = sum(1 for item in records if item["verdict"] in {"fail", "failed"})
    review_count = len(records) - passed_count - failed_count
    return {
        "count": len(records),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "review_count": review_count,
        "fixture_path": str(RAILWAY_BOT_TEST_FIXTURE),
        "groups": _railway_bot_test_groups(records),
        "records": records,
        "error": "" if records else "Fixture dos 150 testes Railway não encontrada ou inválida.",
    }


def _railway_bot_test_export_payload() -> dict:
    inventory = railway_bot_test_log_inventory()
    return {
        "description": "150 perguntas executadas contra Railway para validar o bot PRAGtico.",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "total": inventory["count"],
            "passed": inventory["passed_count"],
            "review": inventory["review_count"],
            "failed": inventory["failed_count"],
        },
        "records": inventory["records"],
    }


def _railway_bot_test_export_text(payload: dict) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "PRAGtico - 150 testes Railway",
        f"Total: {summary.get('total', 0)}",
        f"Corretos: {summary.get('passed', 0)}",
        f"Duvidas: {summary.get('review', 0)}",
        f"Falhas/bugs: {summary.get('failed', 0)}",
        "",
    ]
    for item in payload.get("records") or []:
        lines.extend(
            [
                f"#{item.get('number')} [{item.get('verdict')}] {item.get('suite')} / {item.get('group')}",
                f"Pergunta: {item.get('question')}",
                f"Origem resposta: {item.get('answer_origin')}",
                f"Esperado: {item.get('expected_summary')}",
                f"Verificacao: {item.get('verification_summary')}",
                "Resposta:",
                str(item.get("answer") or "").strip(),
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _pdf_safe_text(value: str) -> str:
    encoded = str(value or "").encode("cp1252", "replace").decode("cp1252")
    return encoded.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _render_text_pdf(title: str, body: str) -> bytes:
    page_width = 595
    page_height = 842
    margin = 48
    font_size = 9
    line_height = 13
    usable_width = page_width - (margin * 2)
    max_chars = max(42, int(usable_width / (font_size * 0.62)))
    raw_lines = [title, "", *str(body or "").splitlines()]
    wrapped_lines: list[str] = []
    for raw_line in raw_lines:
        clean = str(raw_line or "").replace("\t", "  ")
        if not clean:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            wrap(
                clean,
                width=max_chars,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    max_lines_per_page = max(1, int((page_height - (margin * 2)) / line_height))
    pages = [
        wrapped_lines[index:index + max_lines_per_page]
        for index in range(0, len(wrapped_lines), max_lines_per_page)
    ] or [["Sem conteúdo."]]

    objects: dict[int, bytes] = {}
    font_id = 3
    next_id = 4
    page_ids: list[int] = []
    for page_lines in pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)
        stream_lines = ["BT", f"/F1 {font_size} Tf"]
        y = page_height - margin
        for line in page_lines:
            stream_lines.append(f"1 0 0 1 {margin} {y} Tm ({_pdf_safe_text(line)}) Tj")
            y -= line_height
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("cp1252", "replace")
        objects[content_id] = (
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")

    objects[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for object_id in range(1, next_id):
        offsets[object_id] = len(pdf)
        pdf.extend(f"{object_id} 0 obj\n".encode("ascii"))
        pdf.extend(objects[object_id])
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {next_id}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for object_id in range(1, next_id):
        pdf.extend(f"{offsets[object_id]:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {next_id} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("ascii")
    )
    return bytes(pdf)


def railway_bot_test_export_bytes(export_format: str) -> tuple[bytes, str, str]:
    """Export the 150 Railway bot tests in a debug-friendly format."""
    export_format = str(export_format or "").strip().lower()
    if export_format not in {"json", "csv", "pdf"}:
        raise ValueError("Formato de exportação inválido.")
    payload = _railway_bot_test_export_payload()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    if export_format == "json":
        return (
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
            f"pragtico_railway_150_testes_{timestamp}.json",
        )
    if export_format == "csv":
        output = StringIO()
        fieldnames = [
            "number",
            "suite",
            "group",
            "risk",
            "verdict",
            "question",
            "answer_origin",
            "expected_summary",
            "verification_summary",
            "latency_ms",
            "conversation_id",
            "message_id",
            "answer",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for item in payload.get("records") or []:
            writer.writerow(item)
        return (
            output.getvalue().encode("utf-8-sig"),
            "text/csv; charset=utf-8",
            f"pragtico_railway_150_testes_{timestamp}.csv",
        )
    pdf = _render_text_pdf(
        "PRAGtico - 150 testes Railway",
        _railway_bot_test_export_text(payload),
    )
    return pdf, "application/pdf", f"pragtico_railway_150_testes_{timestamp}.pdf"


def berth_capacity_test_matrix() -> list[dict]:
    """Return berth-capacity rules that must remain visible in admin QA."""
    source = "knowledge/berth_profiles.json + knowledge/companions + domain/berth_layout.py + tests/test_berth_layout.py"

    def vessel(identifier: str, name: str, berth: str, loa_m: str | None = None) -> dict:
        payload = {"id": identifier, "vessel_name": name, "berth_label": berth}
        if loa_m is not None:
            payload["vessel_loa_m"] = loa_m
        return payload

    def case(
        identifier: str,
        group: str,
        label: str,
        case_text: str,
        expected: str,
        *,
        occupants: list[dict] | None = None,
        target_berth: str = "",
        target_loa_m: str = "",
        expected_conflict: bool | None = None,
        expected_conflict_id: str = "",
        expected_occupied_slots: int | None = None,
    ) -> dict:
        initial_state = ", ".join(
            f"{item.get('vessel_name')} em {item.get('berth_label')} ({item.get('vessel_loa_m', 'LOA desconhecido')} m)"
            for item in occupants or []
        ) or "Sem navios em porto no terminal testado."
        target = f"{target_berth} · LOA {target_loa_m} m" if target_berth else "Contagem de ocupação"
        return {
            "id": identifier,
            "group": group,
            "risk": "Critico",
            "label": label,
            "case": case_text,
            "initial_state": initial_state,
            "target": target,
            "expected": expected,
            "source": source,
            "occupants": occupants or [],
            "target_berth": target_berth,
            "target_loa_m": target_loa_m,
            "expected_conflict": expected_conflict,
            "expected_conflict_id": expected_conflict_id,
            "expected_occupied_slots": expected_occupied_slots,
        }

    return [
        case(
            "autoeuropa-two-small",
            "Autoeuropa",
            "Autoeuropa permite 2 navios abaixo de 230 m",
            "Cais 10 ocupado por 180 m; Cais 11 recebe outro 180 m.",
            "Sem conflito: Cais 10 e Cais 11 podem estar ambos ocupados quando nenhum navio chega a 230 m.",
            occupants=[vessel("auto-10", "Ro-Ro 180 C10", "Cais 10 / Autoeuropa", "180")],
            target_berth="Cais 11 / Autoeuropa",
            target_loa_m="180",
            expected_conflict=False,
        ),
        case(
            "autoeuropa-target-230-exclusive",
            "Autoeuropa",
            "Autoeuropa bloqueia navio novo a partir de 230 m",
            "Cais 10 ocupado por 180 m; tentar aprovar 230 m no Cais 11.",
            "Conflito: o novo navio com LOA >= 230 m precisa da Autoeuropa livre.",
            occupants=[vessel("auto-10", "Ro-Ro 180 C10", "Cais 10 / Autoeuropa", "180")],
            target_berth="Cais 11 / Autoeuropa",
            target_loa_m="230",
            expected_conflict=True,
            expected_conflict_id="auto-10",
        ),
        case(
            "autoeuropa-existing-230-exclusive",
            "Autoeuropa",
            "Autoeuropa bloqueia quando o navio em porto tem 230 m",
            "Cais 10 ocupado por 230 m; tentar aprovar 180 m no Cais 11.",
            "Conflito: um Ro-Ro de 230 m ou mais fica sozinho no terminal.",
            occupants=[vessel("auto-large", "Ro-Ro 230 C10", "Cais 10 / Autoeuropa", "230")],
            target_berth="Cais 11 / Autoeuropa",
            target_loa_m="180",
            expected_conflict=True,
            expected_conflict_id="auto-large",
        ),
        case(
            "autoeuropa-unknown-loa-conservative",
            "Autoeuropa",
            "Autoeuropa fica conservadora sem LOA do ocupante",
            "Cais 10 ocupado sem LOA registado; tentar aprovar 180 m no Cais 11.",
            "Conflito: sem LOA do navio em porto, não se assume que há espaço para segundo navio.",
            occupants=[vessel("auto-unknown", "Ro-Ro sem LOA", "Cais 10 / Autoeuropa")],
            target_berth="Cais 11 / Autoeuropa",
            target_loa_m="180",
            expected_conflict=True,
            expected_conflict_id="auto-unknown",
        ),
        case(
            "autoeuropa-two-229-allowed",
            "Autoeuropa",
            "Autoeuropa não aplica folga extra abaixo de 230 m",
            "Cais 10 ocupado por 229 m; tentar aprovar 229 m no Cais 11.",
            "Sem conflito: a regra operacional da Autoeuropa permite dois navios abaixo de 230 m, sem folga adicional de 30 m.",
            occupants=[vessel("auto-10", "Ro-Ro 229 C10", "Cais 10 / Autoeuropa", "229")],
            target_berth="Cais 11 / Autoeuropa",
            target_loa_m="229",
            expected_conflict=False,
        ),
        case(
            "tms1-230-c4-spans-c5",
            "TMS 1",
            "TMS 1 ocupa mais do que um cais por LOA",
            "Navio de 230 m no Cais 4; tentar usar o Cais 5.",
            "Conflito: o navio longo ocupa Cais 4 + Cais 5.",
            occupants=[vessel("tms1-c4", "TMS1 230 C4", "TMS 1 - Cais 4", "230")],
            target_berth="TMS 1 - Cais 5",
            target_loa_m="100",
            expected_conflict=True,
            expected_conflict_id="tms1-c4",
        ),
        case(
            "tms1-230-c4-allows-c6",
            "TMS 1",
            "TMS 1 liberta cais sem sobreposição física",
            "Navio de 230 m no Cais 4; tentar usar Cais 6 com 120 m.",
            "Sem conflito: Cais 6 fica fora do span Cais 4 + Cais 5.",
            occupants=[vessel("tms1-c4", "TMS1 230 C4", "TMS 1 - Cais 4", "230")],
            target_berth="TMS 1 - Cais 6",
            target_loa_m="120",
            expected_conflict=False,
        ),
        case(
            "tms1-target-230-c5-spans-c6",
            "TMS 1",
            "TMS 1 usa a esquerda se a direita está ocupada",
            "Cais 6 ocupado por 120 m; tentar aprovar 230 m no Cais 5.",
            "Sem conflito: como a direita está ocupada, o navio usa Cais 4 + Cais 5.",
            occupants=[vessel("tms1-c6", "TMS1 120 C6", "TMS 1 - Cais 6", "120")],
            target_berth="TMS 1 - Cais 5",
            target_loa_m="230",
            expected_conflict=False,
        ),
        case(
            "tms1-target-120-c5-does-not-span-c6",
            "TMS 1",
            "TMS 1 aceita navio curto no cais atribuído",
            "Cais 6 ocupado por 120 m; tentar aprovar 120 m no Cais 5.",
            "Sem conflito: 120 m fica dentro do Cais 5.",
            occupants=[vessel("tms1-c6", "TMS1 120 C6", "TMS 1 - Cais 6", "120")],
            target_berth="TMS 1 - Cais 5",
            target_loa_m="120",
            expected_conflict=False,
        ),
        case(
            "tms1-c7-100-spans-c6-not-c8",
            "TMS 1",
            "TMS 1 Cais 7 usa Cais 6 e não Cais 8",
            "Navio de 100 m no Cais 7; tentar usar Cais 8.",
            "Sem conflito: o Cais 8 é isolado e não serve de continuação ao Cais 7.",
            occupants=[vessel("tms1-c7", "TMS1 100 C7", "TMS 1 - Cais 7", "100")],
            target_berth="TMS 1 - Cais 8",
            target_loa_m="80",
            expected_conflict=False,
        ),
        case(
            "tms1-c7-left-side-occupied",
            "TMS 1",
            "TMS 1 bloqueia Cais 7 grande se o lado esquerdo está ocupado",
            "Cais 6 ocupado por 80 m; tentar aprovar 100 m no Cais 7.",
            "Conflito: Cais 7 precisa do Cais 6 para este LOA, porque o Cais 8 é isolado.",
            occupants=[vessel("tms1-c6", "TMS1 80 C6", "TMS 1 - Cais 6", "80")],
            target_berth="TMS 1 - Cais 7",
            target_loa_m="100",
            expected_conflict=True,
            expected_conflict_id="tms1-c6",
        ),
        case(
            "tms1-c7-610-capacity",
            "TMS 1",
            "TMS 1 Cais 7 não excede o lado esquerdo disponível",
            "Tentar aprovar 610 m no Cais 7.",
            "Conflito por capacidade: Cais 4 a Cais 7 somam 605 m e o Cais 8 não entra como continuação.",
            target_berth="TMS 1 - Cais 7",
            target_loa_m="610",
            expected_conflict=True,
            expected_conflict_id="__berth_capacity__",
        ),
        case(
            "tms1-left-boundary-blocked",
            "TMS 1",
            "TMS 1 bloqueia Cais 4 se a direita está ocupada",
            "Cais 5 ocupado; tentar aprovar 230 m no Cais 4.",
            "Conflito: o TMS1 já não tem Cais 3, por isso o Cais 4 não tem lado esquerdo disponível.",
            occupants=[vessel("tms1-c5", "TMS1 100 C5", "TMS 1 - Cais 5", "100")],
            target_berth="TMS 1 - Cais 4",
            target_loa_m="230",
            expected_conflict=True,
            expected_conflict_id="tms1-c5",
        ),
        case(
            "tms1-right-and-left-occupied-blocked",
            "TMS 1",
            "TMS 1 bloqueia se direita e esquerda estão ocupadas",
            "Cais 4 e Cais 6 ocupados; tentar aprovar 230 m no Cais 5.",
            "Conflito: não há sequência contígua livre para acomodar o navio.",
            occupants=[
                vessel("tms1-c4", "TMS1 100 C4", "TMS 1 - Cais 4", "100"),
                vessel("tms1-c6", "TMS1 100 C6", "TMS 1 - Cais 6", "100"),
            ],
            target_berth="TMS 1 - Cais 5",
            target_loa_m="230",
            expected_conflict=True,
            expected_conflict_id="tms1-c6",
        ),
        case(
            "tms1-30m-clearance-blocked",
            "TMS 1",
            "TMS 1 aplica folga mínima de 30 m",
            "Cais 6 ocupado por 170 m; tentar aprovar 330 m no Cais 4.",
            "Conflito: Cais 4 + 5 e Cais 6 somam 525 m, mas 330 + 30 + 170 exige 530 m.",
            occupants=[vessel("tms1-c6", "TMS1 170 C6", "TMS 1 - Cais 6", "170")],
            target_berth="TMS 1 - Cais 4",
            target_loa_m="330",
            expected_conflict=True,
            expected_conflict_id="tms1-c6",
        ),
        case(
            "tms1-cais8-limit",
            "TMS 1",
            "Cais 8 aceita só um navio até 215 m",
            "Tentar aprovar navio de 216 m no TMS 1 - Cais 8.",
            "Conflito por capacidade física do Cais 8.",
            target_berth="TMS 1 - Cais 8",
            target_loa_m="216",
            expected_conflict=True,
            expected_conflict_id="__berth_capacity__",
        ),
        case(
            "tms1-three-large-with-cais8",
            "TMS 1",
            "TMS 1 permite 3 navios grandes incluindo Cais 8",
            "230 m no Cais 4 e 230 m no Cais 6; tentar 215 m no Cais 8.",
            "Sem conflito: o máximo é 3 navios grandes quando o terceiro fica no Cais 8, que pertence ao TMS 1 mas é isolado.",
            occupants=[
                vessel("tms1-c4", "TMS1 230 C4", "TMS 1 - Cais 4", "230"),
                vessel("tms1-c6", "TMS1 230 C6", "TMS 1 - Cais 6", "230"),
            ],
            target_berth="TMS 1 - Cais 8",
            target_loa_m="215",
            expected_conflict=False,
        ),
        case(
            "tms1-two-large-allow-small-cais8",
            "TMS 1",
            "TMS 1 permite Cais 8 curto se livre",
            "230 m no Cais 4 e 230 m no Cais 6; tentar 180 m no Cais 8.",
            "Sem conflito: o limite de 3 grandes não se aplica a navio abaixo de 200 m.",
            occupants=[
                vessel("tms1-c4", "TMS1 230 C4", "TMS 1 - Cais 4", "230"),
                vessel("tms1-c6", "TMS1 230 C6", "TMS 1 - Cais 6", "230"),
            ],
            target_berth="TMS 1 - Cais 8",
            target_loa_m="180",
            expected_conflict=False,
        ),
        case(
            "tms2-230-a-spans-b",
            "TMS 2",
            "TMS 2 230 m ocupa posição adjacente",
            "Posição A ocupada por 230 m; tentar 230 m na Posição B.",
            "Conflito: com quatro posições de referência, cada posição tem 180,75 m e o navio de 230 m ocupa A + B.",
            occupants=[vessel("tms2-a", "TMS2 230 A", "TMS 2 - Posição A", "230")],
            target_berth="TMS 2 - Posição B",
            target_loa_m="230",
            expected_conflict=True,
            expected_conflict_id="tms2-a",
        ),
        case(
            "tms2-300-a-spans-b",
            "TMS 2",
            "TMS 2 ocupa posições adjacentes por LOA",
            "Navio de 300 m na Posição A; tentar usar a Posição B.",
            "Conflito: o navio longo ocupa A + B.",
            occupants=[vessel("tms2-a", "TMS2 300 A", "TMS 2 - Posição A", "300")],
            target_berth="TMS 2 - Posição B",
            target_loa_m="100",
            expected_conflict=True,
            expected_conflict_id="tms2-a",
        ),
        case(
            "tms2-300-a-allows-c",
            "TMS 2",
            "TMS 2 deixa livre posição sem sobreposição",
            "Navio de 300 m na Posição A; tentar usar a Posição C.",
            "Sem conflito: A + B ficam ocupadas e C continua avaliável.",
            occupants=[vessel("tms2-a", "TMS2 300 A", "TMS 2 - Posição A", "300")],
            target_berth="TMS 2 - Posição C",
            target_loa_m="100",
            expected_conflict=False,
        ),
        case(
            "tms2-right-occupied-use-left",
            "TMS 2",
            "TMS 2 prefere direita mas usa esquerda se necessário",
            "Posição C ocupada; tentar aprovar 300 m na Posição B.",
            "Sem conflito: como a direita está ocupada, o navio usa Posição A + Posição B.",
            occupants=[vessel("tms2-c", "TMS2 100 C", "TMS 2 - Posição C", "100")],
            target_berth="TMS 2 - Posição B",
            target_loa_m="300",
            expected_conflict=False,
        ),
        case(
            "tms2-right-and-left-occupied-blocked",
            "TMS 2",
            "TMS 2 bloqueia se direita e esquerda estão ocupadas",
            "Posições A e C ocupadas; tentar aprovar 300 m na Posição B.",
            "Conflito: não há sequência contígua livre para acomodar o navio.",
            occupants=[
                vessel("tms2-a", "TMS2 100 A", "TMS 2 - Posição A", "100"),
                vessel("tms2-c", "TMS2 100 C", "TMS 2 - Posição C", "100"),
            ],
            target_berth="TMS 2 - Posição B",
            target_loa_m="300",
            expected_conflict=True,
            expected_conflict_id="tms2-c",
        ),
        case(
            "tms2-500-b-spans-all",
            "TMS 2",
            "TMS 2 central com 500 m ocupa três posições",
            "Navio de 500 m na Posição B; tentar usar a Posição C.",
            "Conflito: para incluir B e acomodar 500 m, o span cobre B + C + D.",
            occupants=[vessel("tms2-b", "TMS2 500 B", "TMS 2 - Posição B", "500")],
            target_berth="TMS 2 - Posição C",
            target_loa_m="100",
            expected_conflict=True,
            expected_conflict_id="tms2-b",
        ),
        case(
            "tms2-750-capacity",
            "TMS 2",
            "TMS 2 bloqueia LOA acima do comprimento total",
            "Tentar aprovar 750 m no TMS 2.",
            "Conflito por capacidade: o terminal tem 723 m úteis de referência.",
            target_berth="TMS 2 - Posição B",
            target_loa_m="750",
            expected_conflict=True,
            expected_conflict_id="__berth_capacity__",
        ),
        case(
            "tms2-four-150-allowed",
            "TMS 2",
            "TMS 2 permite quatro navios de 150 m",
            "Posições A, B e C com 150 m; tentar 150 m na Posição D.",
            "Sem conflito: quatro navios de 150 m cabem no comprimento total com folgas de 30 m.",
            occupants=[
                vessel("tms2-a", "TMS2 150 A", "TMS 2 - Posição A", "150"),
                vessel("tms2-b", "TMS2 150 B", "TMS 2 - Posição B", "150"),
                vessel("tms2-c", "TMS2 150 C", "TMS 2 - Posição C", "150"),
            ],
            target_berth="TMS 2 - Posição D",
            target_loa_m="150",
            expected_conflict=False,
        ),
        case(
            "tms2-four-170-blocked",
            "TMS 2",
            "TMS 2 bloqueia quatro navios de 170 m",
            "Posições A, B e C com 170 m; tentar 170 m na Posição D.",
            "Conflito por comprimento total e folgas: quatro navios de 170 m não passam nos 723 m.",
            occupants=[
                vessel("tms2-a", "TMS2 170 A", "TMS 2 - Posição A", "170"),
                vessel("tms2-b", "TMS2 170 B", "TMS 2 - Posição B", "170"),
                vessel("tms2-c", "TMS2 170 C", "TMS 2 - Posição C", "170"),
            ],
            target_berth="TMS 2 - Posição D",
            target_loa_m="170",
            expected_conflict=True,
            expected_conflict_id="tms2-a",
        ),
        case(
            "occupancy-tms2-middle-500",
            "Contagem de slots",
            "Ocupação conta TMS 2 central de 500 m como 3 slots",
            "Snapshot com navio de 500 m na Posição B.",
            "A ocupação deve contar A + B + C como ocupados.",
            occupants=[vessel("tms2-b", "TMS2 500 B", "TMS 2 - Posição B", "500")],
            expected_occupied_slots=3,
        ),
        case(
            "occupancy-tms1-c7-overflow",
            "Contagem de slots",
            "Ocupação conta Cais 7 de 100 m como Cais 6 + Cais 7",
            "Snapshot com navio de 100 m no Cais 7.",
            "A ocupação deve contar dois slots físicos no TMS 1, sem ocupar o Cais 8.",
            occupants=[vessel("tms1-c7", "TMS1 100 C7", "TMS 1 - Cais 7", "100")],
            expected_occupied_slots=2,
        ),
        case(
            "occupancy-autoeuropa-large",
            "Contagem de slots",
            "Ocupação conta Autoeuropa >=230 m como 2 slots",
            "Snapshot com Ro-Ro de 230 m no Cais 10.",
            "A ocupação deve contar Cais 10 + Cais 11.",
            occupants=[vessel("auto-large", "Ro-Ro 230 C10", "Cais 10 / Autoeuropa", "230")],
            expected_occupied_slots=2,
        ),
    ]


def _evaluate_berth_capacity_case(item: dict) -> dict:
    occupants = deepcopy(item.get("occupants") or [])
    errors: list[str] = []
    observed: list[str] = []

    if item.get("target_berth"):
        conflict = find_occupied_berth_conflict(
            item.get("target_berth"),
            occupants,
            target_vessel_loa_m=item.get("target_loa_m"),
            berth_options=services.BERTH_OPTIONS,
        )
        expected_conflict = item.get("expected_conflict")
        actual_conflict = conflict is not None
        conflict_ref = ""
        if conflict:
            conflict_ref = conflict.get("id") or conflict.get("reference_code") or conflict.get("vessel_name") or "conflito"
            observed.append(f"Conflito: {conflict_ref}")
        else:
            observed.append("Sem conflito")
        if expected_conflict is not None and actual_conflict != expected_conflict:
            errors.append(f"esperado conflito={expected_conflict}, obtido conflito={actual_conflict}")
        expected_ref = item.get("expected_conflict_id")
        if expected_ref and conflict:
            actual_refs = {
                str(conflict.get("id") or ""),
                str(conflict.get("reference_code") or ""),
                str(conflict.get("vessel_name") or ""),
            }
            if expected_ref not in actual_refs:
                errors.append(f"conflito esperado {expected_ref}, obtido {conflict_ref}")
        elif expected_ref and not conflict:
            errors.append(f"conflito esperado {expected_ref}, mas não houve conflito")

    expected_slots = item.get("expected_occupied_slots")
    if expected_slots is not None:
        occupancy = build_slot_occupancy(occupants, berth_options=services.BERTH_OPTIONS)
        actual_slots = occupancy.get("occupied_slot_count")
        observed.append(f"Slots ocupados: {actual_slots}")
        if actual_slots != expected_slots:
            errors.append(f"slots esperados {expected_slots}, obtidos {actual_slots}")

    return {
        "state": "failed" if errors else "passed",
        "observed": "; ".join(observed + errors) or "Caso sem verificação.",
    }


def operational_test_inventory() -> dict:
    """Return current retained test records so the admin page can show cleanup state."""
    matrix = critical_bot_test_matrix()
    automatic_count = sum(1 for item in matrix if item.get("runner") != "manual")
    manual_count = len(matrix) - automatic_count
    matrix_payload = {
        "bot_matrix": matrix,
        "bot_matrix_groups": _critical_bot_test_matrix_groups(matrix),
        "bot_matrix_count": len(matrix),
        "bot_matrix_automatic_count": automatic_count,
        "bot_matrix_manual_count": manual_count,
        "railway_log": railway_bot_test_log_inventory(),
        "berth_capacity_tests": berth_capacity_test_matrix(),
        "berth_capacity_test_count": len(berth_capacity_test_matrix()),
        "expected_module_count": 9,
    }
    try:
        records = services.store.list_port_calls()
    except Exception as exc:
        return {"count": 0, "items": [], "error": str(exc), **matrix_payload}
    items = [
        {
            "id": record.get("id"),
            "reference_code": record.get("reference_code"),
            "vessel_name": record.get("vessel_name"),
            "status_label": record.get("status_label") or record.get("status"),
        }
        for record in records
        if str(record.get("vessel_name") or "").strip().upper().startswith(TEST_VESSEL_PREFIX)
    ]
    return {"count": len(items), "items": items, "error": "", **matrix_payload}


def _missing_expected_tokens(text: str, expected_tokens: tuple[str, ...] | list[str]) -> list[str]:
    folded = str(text or "").casefold()
    return [str(token) for token in expected_tokens if str(token or "").strip() and str(token).casefold() not in folded]


def _present_forbidden_tokens(text: str, forbidden_tokens: tuple[str, ...] | list[str]) -> list[str]:
    folded = str(text or "").casefold()
    return [str(token) for token in forbidden_tokens if str(token or "").strip() and str(token).casefold() in folded]


def _critical_knowledge_dir() -> str:
    return _active_knowledge_dir() or os.path.join(os.getcwd(), "knowledge")


def _critical_json_source_text(source_path: str) -> str:
    path = os.path.join(_critical_knowledge_dir(), source_path)
    with open(path, "r", encoding="utf-8") as handle:
        return json.dumps(json.load(handle), ensure_ascii=False, sort_keys=True)


def _critical_text_source_text(source_path: str) -> str:
    path = os.path.join(_critical_knowledge_dir(), source_path)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _critical_berth_profile_text(profile_query: str) -> str:
    match = find_best_berth_profile(profile_query, _critical_knowledge_dir())
    profile = (match or {}).get("profile") or {}
    values = [
        profile.get("name", ""),
        profile.get("document", ""),
        profile.get("overview", ""),
    ]
    for key in ("dimensions", "vessel_limits", "draft_rules", "maneuver_rules", "night_rules", "restrictions"):
        values.extend(profile.get(key, []) or [])
    return "\n".join(str(value or "") for value in values)


def _critical_checklist_fixture_data(fixture: str) -> dict:
    fixtures = {
        "lisnave_dock_three_tugs": {
            "vessel_name": "TESTE QA MATRIX LISNAVE DOCA",
            "vessel_type": "Carga geral",
            "loa": "132",
            "beam": "20",
            "gt": "8100",
            "draft": "5.4",
            "bow_thruster": "yes",
            "status": "scheduled",
            "maneuver": {
                "id": "matrix-lisnave-dock-entry",
                "type": "entry",
                "state": "pending",
                "origin": "Sines",
                "destination": "Lisnave - Doca 20",
                "tug_count": "3",
            },
        },
        "lisnave_quay_three_tugs": {
            "vessel_name": "TESTE QA MATRIX LISNAVE CAIS",
            "vessel_type": "Carga geral",
            "loa": "132",
            "beam": "20",
            "gt": "8100",
            "draft": "6.0",
            "bow_thruster": "yes",
            "status": "scheduled",
            "maneuver": {
                "id": "matrix-lisnave-quay-entry",
                "type": "entry",
                "state": "pending",
                "origin": "Sines",
                "destination": "Lisnave - Cais 1 A",
                "tug_count": "3",
            },
        },
        "lisnave_dock31_four_tugs": {
            "vessel_name": "TESTE QA MATRIX LISNAVE D31",
            "vessel_type": "Carga geral",
            "loa": "132",
            "beam": "20",
            "gt": "8100",
            "draft": "5.4",
            "bow_thruster": "yes",
            "status": "scheduled",
            "maneuver": {
                "id": "matrix-lisnave-d31-entry",
                "type": "entry",
                "state": "pending",
                "origin": "Sines",
                "destination": "Lisnave - Doca 31",
                "tug_count": "4",
            },
        },
        "ecooil_entry": {
            "vessel_name": "TESTE QA MATRIX ECO OIL",
            "vessel_type": "Graneis liquidos",
            "loa": "145",
            "beam": "24",
            "gt": "9500",
            "draft": "7.2",
            "bow_thruster": "yes",
            "status": "scheduled",
            "maneuver": {
                "id": "matrix-ecooil-entry",
                "type": "entry",
                "state": "pending",
                "origin": "Sines",
                "destination": "Eco-Oil",
                "tug_count": "2",
            },
        },
        "tanquisado_departure": {
            "vessel_name": "TESTE QA MATRIX TANQUISADO",
            "vessel_type": "Graneis liquidos",
            "loa": "150",
            "beam": "25",
            "gt": "10200",
            "draft": "8.1",
            "bow_thruster": "yes",
            "status": "in_port",
            "maneuver": {
                "id": "matrix-tanquisado-departure",
                "type": "departure",
                "state": "pending",
                "origin": "Tanquisado",
                "destination": "Sines",
                "tug_count": "2",
            },
        },
    }
    if fixture not in fixtures:
        raise ValueError(f"Fixture de checklist desconhecida: {fixture}")
    return fixtures[fixture]


class _CriticalSlashValidationStore:
    knowledge_dir = "knowledge"
    backend_name = "critical_matrix"

    def __init__(self, port_call: dict) -> None:
        self.port_call = port_call

    def get_user_profile(self, username: str) -> dict:
        return {}

    def get_port_call(self, port_call_id: str) -> dict:
        if port_call_id != self.port_call.get("id"):
            raise KeyError(port_call_id)
        return self.port_call

    def get_port_activity_snapshot(self, window_days: int = 5) -> dict:
        maneuver = (self.port_call.get("maneuver_history") or [{}])[0]
        planned_row = {
            "id": f"planned-{maneuver.get('id', '')}",
            "port_call_id": self.port_call.get("id"),
            "reference_code": self.port_call.get("reference_code"),
            "vessel_name": self.port_call.get("vessel_name"),
            "maneuver_id": maneuver.get("id"),
            "maneuver_label": maneuver.get("action_label") or maneuver.get("type_label") or "Manobra",
            "situation_class": maneuver.get("state") or "pending",
            "situation_label": maneuver.get("state_label") or maneuver.get("state") or "Pendente",
            "date_value": maneuver.get("planned_at"),
            "planned_value": maneuver.get("planned_at"),
        }
        return {
            "arrivals": [self.port_call],
            "in_port": [],
            "departed": [],
            "aborted": [],
            "departure_candidates": [],
            "planned_maneuvers": [planned_row],
            "archived_maneuvers": [],
        }


def critical_slash_validation_text(fixture: str) -> str:
    fixtures = {
        "tanquisado_entry_validation": {
            "vessel_name": "GALBOT",
            "vessel_type": "Graneis liquidos",
            "loa": "101.4",
            "beam": "18",
            "gt": "7000",
            "dwt": "9000",
            "draft": "7.5",
            "bow_thruster": "yes",
            "berth": "Tanquisado (lado jusante)",
            "last_port": "Sines",
            "next_port": "Tanquisado",
            "maneuver": {
                "id": "matrix-tanquisado-entry-validation",
                "type": "entry",
                "state": "pending",
                "planned_at": "2026-04-30T19:12:00+00:00",
                "planned_input_value": "2026-04-30T19:12",
                "origin": "Sines",
                "destination": "Tanquisado (lado jusante)",
                "tug_count": "2",
            },
        },
        "tanquisado_entry_bad_tide": {
            "vessel_name": "GALBOT",
            "vessel_type": "Graneis liquidos",
            "loa": "101.4",
            "beam": "18",
            "gt": "7000",
            "dwt": "9000",
            "draft": "7.5",
            "bow_thruster": "yes",
            "berth": "Tanquisado (lado jusante)",
            "last_port": "Sines",
            "next_port": "Tanquisado",
            "maneuver": {
                "id": "matrix-tanquisado-bad-tide",
                "type": "entry",
                "state": "pending",
                "planned_at": "2026-04-30T18:00:00+00:00",
                "planned_input_value": "2026-04-30T18:00",
                "origin": "Sines",
                "destination": "Tanquisado (lado jusante)",
                "tug_count": "2",
            },
        },
        "ecooil_entry_validation": {
            "vessel_name": "TESTE QA ECO VALIDAR",
            "vessel_type": "Graneis liquidos",
            "loa": "145",
            "beam": "24",
            "gt": "9500",
            "dwt": "12000",
            "draft": "7.2",
            "bow_thruster": "yes",
            "berth": "Eco-Oil",
            "last_port": "Sines",
            "next_port": "Eco-Oil",
            "maneuver": {
                "id": "matrix-ecooil-entry-validation",
                "type": "entry",
                "state": "pending",
                "planned_at": "2026-04-30T19:12:00+00:00",
                "planned_input_value": "2026-04-30T19:12",
                "origin": "Sines",
                "destination": "Eco-Oil",
                "tug_count": "3",
            },
        },
        "lisnave_dock_three_tugs_validation": {
            "vessel_name": "TESTE QA LISNAVE VALIDAR",
            "vessel_type": "Carga geral",
            "loa": "132",
            "beam": "20",
            "gt": "8100",
            "dwt": "10000",
            "draft": "5.4",
            "bow_thruster": "yes",
            "berth": "Lisnave - Doca 20",
            "last_port": "Sines",
            "next_port": "Lisnave",
            "maneuver": {
                "id": "matrix-lisnave-dock-validation",
                "type": "entry",
                "state": "pending",
                "planned_at": "2026-04-30T19:12:00+00:00",
                "planned_input_value": "2026-04-30T19:12",
                "origin": "Sines",
                "destination": "Lisnave - Doca 20",
                "tug_count": "3",
            },
        },
        "shift_lisnave_to_tms1_validation": {
            "vessel_name": "TESTE QA MUDANCA VALIDAR",
            "vessel_type": "Abastecedor",
            "loa": "80",
            "beam": "16",
            "gt": "3200",
            "dwt": "4500",
            "draft": "5.5",
            "bow_thruster": "yes",
            "status": "in_port",
            "berth": "Lisnave - Cais 1 A",
            "last_port": "Lisnave - Cais 1 A",
            "next_port": "TMS 1 - Cais 4",
            "maneuver": {
                "id": "matrix-shift-lisnave-tms1-validation",
                "type": "shift",
                "state": "pending",
                "planned_at": "2026-04-30T21:12:00+00:00",
                "planned_input_value": "2026-04-30T21:12",
                "origin": "Lisnave - Cais 1 A",
                "destination": "TMS 1 - Cais 4",
                "tug_count": "2",
            },
        },
    }
    if fixture not in fixtures:
        raise ValueError(f"Fixture de /validar-manobra desconhecida: {fixture}")
    data = fixtures[fixture]
    maneuver = {
        **data["maneuver"],
        "created_by": "admin",
        "created_at": "2026-04-30T10:00:00+01:00",
        "updated_at": "2026-04-30T10:00:00+01:00",
        "constraints": [],
        "plan_note": "Cenario de validacao do comando.",
    }
    port_call = _decorate_port_call(
        {
            "id": f"matrix-validar-manobra-{fixture}",
            "vessel_name": data["vessel_name"],
            "vessel_type": data["vessel_type"],
            "vessel_loa_m": data["loa"],
            "vessel_beam_m": data["beam"],
            "vessel_gt_t": data["gt"],
            "vessel_dwt_t": data["dwt"],
            "vessel_max_draft_m": data["draft"],
            "vessel_bow_thruster": data["bow_thruster"],
            "vessel_stern_thruster": "no",
            "status": data.get("status", "scheduled"),
            "approval_status": "pending",
            "berth": data["berth"],
            "last_port": data["last_port"],
            "next_port": data["next_port"],
            "created_by": "admin",
            "created_at": "2026-04-30T09:00:00+01:00",
            "updated_at": "2026-04-30T09:00:00+01:00",
            "maneuver_history": [maneuver],
        }
    )
    previous_store = services.store
    previous_tide_service = getattr(services, "tide_service", None)
    previous_knowledge_dir = getattr(services, "KNOWLEDGE_DIR", "")
    should_restore_knowledge_dir = not _active_knowledge_dir()
    if should_restore_knowledge_dir:
        services.KNOWLEDGE_DIR = _critical_knowledge_dir()
    if not getattr(services, "tide_service", None):
        services.tide_service = TideService(
            os.path.join(os.getcwd(), "resources", "tides", "mares.2026.201.9_setubal_troia.csv")
        )
    services.store = _CriticalSlashValidationStore(port_call)
    try:
        payload = answer_slash_validation(
            {
                "maneuver_id": maneuver["id"][:8],
                "maneuver_type": "entry",
                "reference_code": port_call.get("reference_code"),
            },
            "admin",
        )
    finally:
        services.store = previous_store
        services.tide_service = previous_tide_service
        if should_restore_knowledge_dir:
            services.KNOWLEDGE_DIR = previous_knowledge_dir
    return str((payload or {}).get("answer") or "")


def critical_maneuver_checklist_text(fixture: str) -> str:
    data = _critical_checklist_fixture_data(fixture)
    maneuver = {
        **data["maneuver"],
        "planned_at": "2026-05-02T10:00:00+01:00",
        "created_by": "admin",
        "created_at": "2026-05-01T10:00:00+01:00",
        "updated_at": "2026-05-01T10:00:00+01:00",
        "constraints": [],
        "plan_note": "Cenario de validacao da checklist.",
    }
    port_call = _decorate_port_call(
        {
            "id": f"matrix-{fixture}",
            "vessel_name": data["vessel_name"],
            "vessel_type": data["vessel_type"],
            "vessel_loa_m": data["loa"],
            "vessel_beam_m": data["beam"],
            "vessel_gt_t": data["gt"],
            "vessel_dwt_t": "12000",
            "vessel_max_draft_m": data["draft"],
            "vessel_bow_thruster": data["bow_thruster"],
            "vessel_stern_thruster": "unknown",
            "status": data["status"],
            "approval_status": "approved" if data["status"] == "in_port" else "pending",
            "berth": maneuver["origin"] if maneuver["type"] == "departure" else maneuver["destination"],
            "last_port": maneuver["origin"],
            "next_port": maneuver["destination"],
            "created_by": "admin",
            "created_at": "2026-05-01T10:00:00+01:00",
            "updated_at": "2026-05-01T10:00:00+01:00",
            "maneuver_history": [maneuver],
        }
    )
    previous_knowledge_dir = getattr(services, "KNOWLEDGE_DIR", "")
    should_restore_knowledge_dir = not _active_knowledge_dir()
    if should_restore_knowledge_dir:
        services.KNOWLEDGE_DIR = _critical_knowledge_dir()
    try:
        context = build_scale_context(port_call)
    finally:
        if should_restore_knowledge_dir:
            services.KNOWLEDGE_DIR = previous_knowledge_dir
    target = next(
        (
            item
            for item in context.get("maneuvers", [])
            if item.get("id") == maneuver["id"] or item.get("type") == maneuver["type"]
        ),
        None,
    )
    if not target:
        return ""
    lines = []
    for item in target.get("analysis_checklist") or []:
        lines.append(f"{item.get('status', '')} | {item.get('title', '')}: {item.get('detail', '')}")
    return "\n".join(lines)


class OperationalFlowSuite:
    def __init__(self, *, actor_username: str, cleanup_after: bool = True):
        self.actor_username = actor_username or "admin"
        self.cleanup_after = cleanup_after
        self.started_at = datetime.now().astimezone()
        self.started_perf = time.perf_counter()
        self.run_token = self.started_at.strftime("%H%M%S")
        self.created_ids: list[str] = []
        self.retained_records: list[dict] = []
        self.scenarios: list[dict] = []
        self.seed_base = 9100000 + int(self.started_at.timestamp()) % 800000

    def _future(self, *, days: int, hour: int, minute: int = 0) -> str:
        candidate = (self.started_at + timedelta(days=days)).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if candidate <= self.started_at:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    def _event_time(self, *, days: int, hour: int, minute: int = 0) -> str:
        return self._future(days=days, hour=hour, minute=minute)

    def _imo(self, index: int) -> str:
        return str(self.seed_base + index)[-7:].zfill(7)

    def _profile(self, index: int, *, vessel_type: str = "Carga geral") -> dict:
        return {
            "vessel_short_name": "",
            "vessel_imo": self._imo(index),
            "vessel_call_sign": f"TQA{self.run_token[-4:]}{index:02d}",
            "vessel_flag": "Portugal",
            "vessel_type": vessel_type,
            "vessel_loa_m": "148.0",
            "vessel_beam_m": "23.0",
            "vessel_gt_t": "12600",
            "vessel_dwt_t": "18200",
            "vessel_max_draft_m": "8.6",
            "vessel_bow_thruster": "yes",
            "vessel_stern_thruster": "unknown",
        }

    def _new_scenario(self, module: str, name: str, purpose: str) -> dict:
        scenario = {
            "module": module,
            "name": name,
            "purpose": purpose,
            "state": "running",
            "state_badge": "degraded",
            "steps": [],
        }
        self.scenarios.append(scenario)
        return scenario

    def _record_step(
        self,
        scenario: dict,
        *,
        label: str,
        element: str,
        expected: str,
        observed: str,
        state: str,
    ) -> None:
        scenario["steps"].append(
            {
                "label": label,
                "element": element,
                "expected": expected,
                "observed": observed,
                "state": state,
                "state_badge": _status_badge(state),
            }
        )

    def _step(
        self,
        scenario: dict,
        label: str,
        element: str,
        expected: str,
        action: Callable[[], Any],
        *,
        expected_error: str = "",
    ) -> Any:
        try:
            value = action()
        except Exception as exc:
            observed = str(exc)
            if expected_error and expected_error.casefold() in observed.casefold():
                self._record_step(
                    scenario,
                    label=label,
                    element=element,
                    expected=expected,
                    observed=observed,
                    state="passed",
                )
                return exc
            self._record_step(
                scenario,
                label=label,
                element=element,
                expected=expected,
                observed=observed,
                state="failed",
            )
            return None

        if expected_error:
            self._record_step(
                scenario,
                label=label,
                element=element,
                expected=expected,
                observed="A operação passou, mas devia ter sido bloqueada.",
                state="failed",
            )
            return value

        self._record_step(
            scenario,
            label=label,
            element=element,
            expected=expected,
            observed=self._summarize(value),
            state="passed",
        )
        return value

    def _check(
        self,
        scenario: dict,
        label: str,
        element: str,
        expected: str,
        condition: bool,
        observed: str,
    ) -> None:
        self._record_step(
            scenario,
            label=label,
            element=element,
            expected=expected,
            observed=observed,
            state="passed" if condition else "failed",
        )

    def _check_state(
        self,
        scenario: dict,
        label: str,
        element: str,
        expected: str,
        observed: str,
        *,
        state: str,
    ) -> None:
        self._record_step(
            scenario,
            label=label,
            element=element,
            expected=expected,
            observed=observed,
            state=state,
        )

    def _finish_scenario(self, scenario: dict) -> None:
        scenario["state"] = _state_from_steps(scenario["steps"])
        scenario["state_badge"] = _status_badge(scenario["state"])

    def _summarize(self, value: Any) -> str:
        if isinstance(value, dict):
            vessel = value.get("vessel_name")
            ref = value.get("reference_code") or value.get("id")
            status = value.get("status_label") or value.get("status")
            if vessel or ref:
                return " · ".join(str(part) for part in (ref, vessel, status) if part)
        return "OK"

    def _runtime_state_snapshot(self, key: str) -> dict | None:
        if not hasattr(services.store, "get_runtime_state"):
            return None
        value = services.store.get_runtime_state(key)
        return deepcopy(value) if isinstance(value, dict) else None

    def _restore_runtime_state(self, key: str, snapshot: dict | None) -> None:
        if not hasattr(services.store, "set_runtime_state"):
            return
        if snapshot is None and hasattr(services.store, "delete_runtime_state"):
            services.store.delete_runtime_state(key)
            return
        services.store.set_runtime_state(key, deepcopy(snapshot or {}))

    def _catalog_payload(
        self,
        index: int,
        *,
        name: str,
        vessel_type: str = "Carga geral",
        call_sign: str = "",
    ) -> dict:
        payload = {
            "vessel_name": f"{TEST_VESSEL_PREFIX} {name} {self.run_token}",
            **self._profile(index, vessel_type=vessel_type),
        }
        if call_sign:
            payload["vessel_call_sign"] = call_sign
        return payload

    def _created_records(self) -> list[dict]:
        records: list[dict] = []
        for port_call_id in self.created_ids:
            try:
                records.append(services.store.get_port_call(port_call_id))
            except Exception:
                continue
        return records

    def _create_port_call(
        self,
        scenario: dict,
        *,
        index: int,
        name: str,
        eta: str,
        berth: str,
        last_port: str,
        next_port: str,
        vessel_type: str = "Carga geral",
        constraints: list[str] | None = None,
        tug_count: str = "2",
    ) -> dict | None:
        full_name = f"{TEST_VESSEL_PREFIX} {name} {self.run_token}"
        profile = self._profile(index, vessel_type=vessel_type)
        record = self._step(
            scenario,
            "Criar escala",
            "Registo de escala e manobra de entrada inicial",
            "Escala criada em estado pendente, com entrada inicial pendente.",
            lambda: services.store.create_port_call(
                vessel_name=full_name,
                eta=eta,
                created_by=self.actor_username,
                constraints=constraints or [],
                berth=berth,
                last_port=last_port,
                next_port=next_port,
                notes=f"Cenário automático de teste: {name}.",
                **profile,
            ),
        )
        if isinstance(record, dict):
            self.created_ids.append(record["id"])
            self.retained_records.append(
                {
                    "id": record.get("id"),
                    "reference_code": record.get("reference_code"),
                    "vessel_name": record.get("vessel_name"),
                }
            )
        return record if isinstance(record, dict) else None

    def _free_hour_capacity_test_berths(self, planned_at: str, *, count: int = 5) -> list[str]:
        """Pick simple free berths so the hourly-capacity test is not masked by live occupancy."""
        snapshot = services.store.get_port_activity_snapshot(window_days=3650)
        in_port = snapshot.get("in_port", []) or []
        preferred = [
            "Secil W",
            "Secil E",
            "Cais Palmeiras",
            "Praias do Sado / Pirites Alentejanas",
            "SAPEC Sólidos",
            "SAPEC Líquidos",
            "ALSTOM",
            "PAN Tróia",
            "Tanquisado (lado jusante)",
            "Eco-Oil (lado montante)",
            "Lisnave - Cais 0 B",
            "Lisnave - Cais 0 A",
            "Lisnave - Doca 20",
            "Lisnave - Doca 21",
            "Lisnave - Doca 22",
            "Lisnave - Cais 1 B",
            "Lisnave - Cais 1 A",
            "Lisnave - Cais 2 B",
            "Lisnave - Cais 2 A",
            "Lisnave - Cais 3 B",
            "Lisnave - Cais 3 A",
            "Lisnave - Doca 31",
            "Lisnave - Doca 32",
            "Lisnave - Doca 33",
            "Teporset",
        ]
        selected: list[str] = []
        for berth in preferred:
            conflict = find_occupied_berth_conflict(
                berth,
                in_port,
                target_planned_at=planned_at,
                target_vessel_loa_m="148.0",
                berth_options=services.BERTH_OPTIONS,
            )
            if conflict:
                continue
            selected.append(berth)
            if len(selected) >= count:
                break
        if len(selected) < count:
            raise ValueError(
                f"Sem {count} cais livres simples para testar capacidade horária; encontrados {len(selected)}."
            )
        return selected

    def _latest(self, port_call: dict, maneuver_type: str) -> dict:
        current = services.store.get_port_call(port_call["id"])
        maneuver = latest_maneuver_by_type(current, maneuver_type)
        if not maneuver:
            raise ValueError(f"Sem manobra {maneuver_type} na escala.")
        return maneuver

    def _approve_entry(self, port_call: dict) -> dict:
        current = services.store.get_port_call(port_call["id"])
        maneuver = latest_maneuver_by_type(current, "entry")
        if not maneuver:
            raise ValueError("Sem entrada pendente.")
        ensure_maneuver_hour_capacity_for_approval(current, "entry")
        ensure_portal_berth_is_available(
            maneuver.get("destination", ""),
            current_port_call_id=current["id"],
            target_planned_at=maneuver.get("planned_at"),
            target_vessel_loa_m=current.get("vessel_loa_m"),
        )
        return services.store.approve_port_call(
            current["id"],
            decided_by=self.actor_username,
            approval_note="Teste: entrada validada.",
        )

    def _approve_departure(self, port_call: dict) -> dict:
        current = services.store.get_port_call(port_call["id"])
        ensure_maneuver_hour_capacity_for_approval(current, "departure")
        return services.store.approve_port_call(
            current["id"],
            decided_by=self.actor_username,
            approval_note="Teste: saída validada.",
        )

    def _approve_shift(self, port_call: dict) -> dict:
        current = services.store.get_port_call(port_call["id"])
        maneuver = latest_maneuver_by_type(current, "shift")
        if not maneuver:
            raise ValueError("Sem mudança pendente.")
        ensure_maneuver_hour_capacity_for_approval(current, "shift")
        ensure_portal_berth_is_available(
            maneuver.get("destination", ""),
            current_port_call_id=current["id"],
            target_planned_at=maneuver.get("planned_at"),
            target_vessel_loa_m=current.get("vessel_loa_m"),
            label="Cais destino",
        )
        return services.store.approve_shift_plan(
            current["id"],
            decided_by=self.actor_username,
            approval_note="Teste: mudança validada.",
        )

    def _complete_entry(self, port_call: dict, *, at: str) -> dict:
        current = services.store.get_port_call(port_call["id"])
        maneuver = latest_maneuver_by_type(current, "entry")
        if not maneuver:
            raise ValueError("Sem entrada para concluir.")
        berth = ensure_portal_berth_is_physically_available(
            maneuver.get("destination") or current.get("berth", ""),
            current_port_call_id=current["id"],
            target_vessel_loa_m=current.get("vessel_loa_m"),
        )
        return services.store.mark_port_call_arrived(
            current["id"],
            arrived_at=at,
            updated_by=self.actor_username,
            berth=berth,
        )

    def _complete_departure(self, port_call: dict, *, at: str) -> dict:
        current = services.store.get_port_call(port_call["id"])
        return services.store.mark_port_call_departed(
            current["id"],
            departed_at=at,
            updated_by=self.actor_username,
            next_port=current.get("next_port", ""),
        )

    def _complete_shift(self, port_call: dict, *, at: str) -> dict:
        current = services.store.get_port_call(port_call["id"])
        maneuver = latest_maneuver_by_type(current, "shift")
        if not maneuver:
            raise ValueError("Sem mudança para concluir.")
        ensure_portal_berth_is_physically_available(
            maneuver.get("destination") or current.get("shift_destination_berth", ""),
            current_port_call_id=current["id"],
            label="Cais destino",
            target_vessel_loa_m=current.get("vessel_loa_m"),
        )
        return services.store.mark_shift_completed(
            current["id"],
            shifted_at=at,
            updated_by=self.actor_username,
        )

    def _scenario_natural_flow(self) -> None:
        scenario = self._new_scenario(
            "Fluxo de escala",
            "Fluxo natural de escala",
            "Cria uma escala, planeia saída ainda antes da entrada, valida, conclui entrada, valida saída e fecha a escala.",
        )
        port_call = self._create_port_call(
            scenario,
            index=1,
            name="FLUXO PRINCIPAL",
            eta=self._future(days=2, hour=8),
            berth="Secil W",
            last_port="Sines",
            next_port="Vigo",
            vessel_type="Graneis sólidos",
            constraints=["daylight"],
        )
        if not port_call:
            self._finish_scenario(scenario)
            return

        entry = self._latest(port_call, "entry")
        self._check(
            scenario,
            "Entrada inicial",
            "Manobra criada automaticamente",
            "A entrada nasce pendente e com destino no cais previsto.",
            entry.get("state") == "pending" and entry.get("destination") == "Secil W",
            f"{entry.get('type')} · {entry.get('state')} · {entry.get('destination')}",
        )
        port_call = self._step(
            scenario,
            "Planear saída antes da entrada",
            "Planeamento de manobras posteriores",
            "A saída pode ser marcada sem esperar pela entrada concluída.",
            lambda: services.store.schedule_departure_plan(
                port_call["id"],
                planned_departure_at=self._future(days=3, hour=18),
                updated_by=self.actor_username,
                next_port="Vigo",
                constraints=["daylight"],
                departure_plan_note="Teste: saída já planeada antes da entrada.",
                draft_m="7.9",
                tug_count="2",
            ),
        ) or port_call
        if not isinstance(port_call, dict):
            self._finish_scenario(scenario)
            return

        port_call = self._step(
            scenario,
            "Aprovar entrada",
            "Validação da pilotagem",
            "A entrada fica aprovada e guarda quem aprovou.",
            lambda: self._approve_entry(port_call),
        ) or port_call
        approved_entry = self._latest(port_call, "entry")
        self._check(
            scenario,
            "Registo do validador",
            "Quem aprovou a manobra",
            "A entrada tem decided_by preenchido.",
            bool(approved_entry.get("decided_by")),
            approved_entry.get("decided_by") or "--",
        )

        port_call = self._step(
            scenario,
            "Concluir entrada",
            "Ocupação física do cais",
            "A escala passa a navio em porto e o cais fica ocupado.",
            lambda: self._complete_entry(port_call, at=self._event_time(days=2, hour=9)),
        ) or port_call
        self._check(
            scenario,
            "Estado em porto",
            "Estado da escala após ATA",
            "Estado in_port.",
            port_call.get("status") == "in_port",
            port_call.get("status", "--"),
        )

        entry_id = self._latest(port_call, "entry").get("id")
        port_call = self._step(
            scenario,
            "Registar pilotagem da entrada",
            "Registo executante e ficha de manobra",
            "A entrada recebe report_note e reported_by.",
            lambda: services.store.attach_entry_report(
                port_call["id"],
                updated_by=self.actor_username,
                maneuver_started_at=self._event_time(days=2, hour=8, minute=10),
                maneuver_finished_at=self._event_time(days=2, hour=9),
                draft_m="7.8",
                notes="Teste: entrada concluída sem ocorrências.",
                maneuver_id=entry_id,
            ),
        ) or port_call

        port_call = self._step(
            scenario,
            "Aprovar saída",
            "Validação de saída",
            "A saída fica aprovada e pronta para conclusão.",
            lambda: self._approve_departure(port_call),
        ) or port_call
        port_call = self._step(
            scenario,
            "Concluir saída",
            "Fecho operacional da escala",
            "A escala passa para departed.",
            lambda: self._complete_departure(port_call, at=self._event_time(days=3, hour=18, minute=45)),
        ) or port_call
        departure_id = self._latest(port_call, "departure").get("id")
        port_call = self._step(
            scenario,
            "Registar pilotagem da saída",
            "Arquivo da manobra",
            "A saída recebe report_note e executante.",
            lambda: services.store.attach_departure_report(
                port_call["id"],
                updated_by=self.actor_username,
                maneuver_started_at=self._event_time(days=3, hour=18),
                maneuver_finished_at=self._event_time(days=3, hour=18, minute=45),
                draft_m="7.6",
                notes="Teste: saída concluída e escala encerrada.",
                maneuver_id=departure_id,
            ),
        ) or port_call
        final = services.store.get_port_call(port_call["id"])
        self._check(
            scenario,
            "Estado final",
            "Escala concluída",
            "Estado departed com entrada e saída concluídas.",
            final.get("status") == "departed"
            and self._latest(final, "entry").get("state") == "completed"
            and self._latest(final, "departure").get("state") == "completed",
            f"{final.get('status')} · entrada {self._latest(final, 'entry').get('state')} · saída {self._latest(final, 'departure').get('state')}",
        )
        snapshot = services.store.get_port_activity_snapshot(window_days=3650)
        departed_ids = {
            row.get("id") or row.get("port_call_id")
            for row in snapshot.get("departed", []) or []
        }
        archived_departure_ids = {
            row.get("port_call_id")
            for row in snapshot.get("archived_maneuvers", []) or []
            if row.get("maneuver_type") == "departure"
        }
        self._check(
            scenario,
            "Live feed operacional",
            "Saídas recentes/arquivo",
            "A saída concluída aparece nas saídas recentes e no arquivo de manobras.",
            final["id"] in departed_ids and final["id"] in archived_departure_ids,
            (
                f"{len(snapshot.get('departed', []))} saída(s); "
                f"{len(snapshot.get('archived_maneuvers', []))} manobra(s) arquivada(s)."
            ),
        )
        self._finish_scenario(scenario)

    def _scenario_shift_flow(self) -> None:
        scenario = self._new_scenario(
            "Fluxo de escala",
            "Mudança de cais",
            "Valida que uma escala pode entrar, mudar de cais, registar a mudança e sair no fim.",
        )
        port_call = self._create_port_call(
            scenario,
            index=2,
            name="MUDANCA",
            eta=self._future(days=4, hour=7),
            berth="Cais 10 / Autoeuropa",
            last_port="Southampton",
            next_port="Leixões",
            vessel_type="Roll-on/Roll-off",
        )
        if not port_call:
            self._finish_scenario(scenario)
            return
        port_call = self._step(
            scenario,
            "Aprovar e concluir entrada",
            "Entrada antes da mudança",
            "Navio fica em porto no cais inicial.",
            lambda: self._complete_entry(
                self._approve_entry(port_call),
                at=self._event_time(days=4, hour=8),
            ),
        ) or port_call
        port_call = self._step(
            scenario,
            "Planear mudança",
            "Manobra shift",
            "A mudança fica pendente para outro cais.",
            lambda: services.store.schedule_shift_plan(
                port_call["id"],
                planned_shift_at=self._future(days=4, hour=13),
                updated_by=self.actor_username,
                destination_berth="Cais 11 / Autoeuropa",
                constraints=[],
                shift_plan_note="Teste: mudança para cais adjacente.",
                draft_m="8.1",
                tug_count="1",
            ),
        ) or port_call
        port_call = self._step(
            scenario,
            "Aprovar mudança",
            "Validação do destino",
            "A mudança fica aprovada se o destino estiver livre.",
            lambda: self._approve_shift(port_call),
        ) or port_call
        port_call = self._step(
            scenario,
            "Concluir mudança",
            "Atualização do cais atual",
            "O cais atual passa para Cais 11 / Autoeuropa.",
            lambda: self._complete_shift(port_call, at=self._event_time(days=4, hour=13, minute=35)),
        ) or port_call
        shift_id = self._latest(port_call, "shift").get("id")
        port_call = self._step(
            scenario,
            "Registar pilotagem da mudança",
            "Registo executante da mudança",
            "A mudança recebe relatório.",
            lambda: services.store.attach_shift_report(
                port_call["id"],
                updated_by=self.actor_username,
                maneuver_started_at=self._event_time(days=4, hour=13),
                maneuver_finished_at=self._event_time(days=4, hour=13, minute=35),
                draft_m="8.0",
                notes="Teste: mudança concluída.",
                maneuver_id=shift_id,
            ),
        ) or port_call
        self._check(
            scenario,
            "Cais atualizado",
            "Localização do navio",
            "A escala guarda o novo cais.",
            port_call.get("berth") == "Cais 11 / Autoeuropa",
            port_call.get("berth", "--"),
        )
        port_call = self._step(
            scenario,
            "Planear e concluir saída",
            "Fecho após mudança",
            "A escala também consegue fechar depois da mudança.",
            lambda: self._complete_departure(
                self._approve_departure(
                    services.store.schedule_departure_plan(
                        port_call["id"],
                        planned_departure_at=self._future(days=5, hour=10),
                        updated_by=self.actor_username,
                        next_port="Leixões",
                        constraints=[],
                        departure_plan_note="Teste: saída depois de mudança.",
                        draft_m="8.0",
                        tug_count="2",
                    )
                ),
                at=self._event_time(days=5, hour=10, minute=40),
            ),
        ) or port_call
        self._check(
            scenario,
            "Estado final",
            "Escala com mudança e saída",
            "Estado departed.",
            port_call.get("status") == "departed",
            port_call.get("status", "--"),
        )
        self._finish_scenario(scenario)

    def _scenario_berth_release(self) -> None:
        scenario = self._new_scenario(
            "Regras operacionais",
            "Bloqueio por cais ocupado",
            "Confirma que a entrada seguinte só aprova após saída validada e só conclui depois da saída concluída.",
        )
        occupant = self._create_port_call(
            scenario,
            index=3,
            name="OCUPANTE SAPEC",
            eta=self._future(days=6, hour=7),
            berth="SAPEC Sólidos",
            last_port="Aveiro",
            next_port="Casablanca",
            vessel_type="Graneis sólidos",
        )
        incoming = self._create_port_call(
            scenario,
            index=4,
            name="ENTRADA SAPEC",
            eta=self._future(days=6, hour=15),
            berth="SAPEC Sólidos",
            last_port="Lisboa",
            next_port="Faro",
            vessel_type="Carga geral",
        )
        if not occupant or not incoming:
            self._finish_scenario(scenario)
            return
        occupant = self._complete_entry(
            self._approve_entry(occupant),
            at=self._event_time(days=6, hour=8),
        )
        self._step(
            scenario,
            "Bloquear aprovação com cais ocupado",
            "Validação de cais na aprovação",
            "A entrada posterior não pode ser aprovada enquanto o ocupante não tem saída/mudança aprovada.",
            lambda: self._approve_entry(incoming),
            expected_error="ocupado",
        )
        occupant = services.store.schedule_departure_plan(
            occupant["id"],
            planned_departure_at=self._future(days=6, hour=12),
            updated_by=self.actor_username,
            next_port="Casablanca",
            constraints=[],
            departure_plan_note="Teste: saída liberta SAPEC.",
            draft_m="8.2",
            tug_count="2",
        )
        occupant = self._approve_departure(occupant)
        incoming = self._step(
            scenario,
            "Aprovar entrada após saída aprovada",
            "Libertação operacional planeada",
            "A entrada posterior pode ser aprovada porque a saída do ocupante já está aprovada e é anterior.",
            lambda: self._approve_entry(incoming),
        ) or incoming
        self._step(
            scenario,
            "Bloquear conclusão física",
            "Cais livre de facto",
            "Mesmo aprovada, a entrada não conclui até a saída anterior estar concluída.",
            lambda: self._complete_entry(incoming, at=self._event_time(days=6, hour=15, minute=40)),
            expected_error="ainda está ocupado",
        )
        occupant = self._complete_departure(occupant, at=self._event_time(days=6, hour=12, minute=40))
        incoming = self._step(
            scenario,
            "Concluir após saída física",
            "Ocupação real do cais",
            "Depois da saída concluída, a entrada posterior conclui.",
            lambda: self._complete_entry(incoming, at=self._event_time(days=6, hour=15, minute=40)),
        ) or incoming
        self._check(
            scenario,
            "Resultado do cais",
            "Navio posterior em SAPEC",
            "O segundo navio fica em porto no cais pretendido.",
            incoming.get("status") == "in_port" and incoming.get("berth") == "SAPEC Sólidos",
            f"{incoming.get('status')} · {incoming.get('berth')}",
        )
        self._finish_scenario(scenario)

    def _scenario_abort_keeps_block(self) -> None:
        scenario = self._new_scenario(
            "Regras operacionais",
            "Aborto mantém bloqueio",
            "Confirma que uma saída aprovada e depois abortada deixa de libertar o cais para o navio seguinte.",
        )
        occupant = self._create_port_call(
            scenario,
            index=5,
            name="ABORTO OCUPANTE",
            eta=self._future(days=7, hour=7),
            berth="Teporset",
            last_port="Huelva",
            next_port="Tarragona",
            vessel_type="Graneis líquidos",
            constraints=["gas"],
        )
        incoming = self._create_port_call(
            scenario,
            index=6,
            name="ABORTO SEGUINTE",
            eta=self._future(days=7, hour=16),
            berth="Teporset",
            last_port="Sines",
            next_port="Lisboa",
            vessel_type="Carga geral",
        )
        if not occupant or not incoming:
            self._finish_scenario(scenario)
            return
        occupant = self._complete_entry(
            self._approve_entry(occupant),
            at=self._event_time(days=7, hour=8),
        )
        occupant = services.store.schedule_departure_plan(
            occupant["id"],
            planned_departure_at=self._future(days=7, hour=12),
            updated_by=self.actor_username,
            next_port="Tarragona",
            constraints=["gas"],
            departure_plan_note="Teste: saída será abortada.",
            draft_m="7.3",
            tug_count="2",
        )
        occupant = self._approve_departure(occupant)
        occupant = services.store.abort_departure_plan(
            occupant["id"],
            updated_by=self.actor_username,
            aborted_reason="Teste: operação abortada.",
        )
        self._step(
            scenario,
            "Bloquear após aborto",
            "Cais continua ocupado",
            "A entrada seguinte continua bloqueada porque a saída foi abortada.",
            lambda: self._approve_entry(incoming),
            expected_error="ocupado",
        )
        self._finish_scenario(scenario)

    def _scenario_capacity_limit(self) -> None:
        scenario = self._new_scenario(
            "Regras operacionais",
            "Limite de 4 manobras por hora",
            "Aprova quatro manobras na mesma hora e valida que a quinta fica bloqueada.",
        )
        planned_at = self._future(days=8, hour=15)
        try:
            berths = self._free_hour_capacity_test_berths(planned_at)
        except Exception as exc:
            self._record_step(
                scenario,
                label="Escolher cais livres",
                element="Capacidade horária",
                expected="Existem cinco cais livres para isolar o teste de limite por hora.",
                observed=str(exc),
                state="failed",
            )
            self._finish_scenario(scenario)
            return
        port_calls: list[dict] = []
        for offset, berth in enumerate(berths, start=10):
            record = self._create_port_call(
                scenario,
                index=offset,
                name=f"CAPACIDADE {offset}",
                eta=planned_at,
                berth=berth,
                last_port="Lisboa",
                next_port="Setúbal",
                vessel_type="Carga geral",
            )
            if record:
                port_calls.append(record)
        for record in port_calls[:4]:
            self._step(
                scenario,
                f"Aprovar {record.get('vessel_name', '')}",
                "Capacidade horária",
                "As primeiras quatro aprovações na mesma hora passam.",
                lambda record=record: self._approve_entry(record),
            )
        if len(port_calls) >= 5:
            self._step(
                scenario,
                "Bloquear quinta aprovação",
                "Capacidade horária",
                "A quinta manobra aprovada na mesma hora é recusada.",
                lambda: self._approve_entry(port_calls[4]),
                expected_error="4 manobras aprovadas",
            )
        snapshot = services.store.get_port_activity_snapshot(window_days=3650)
        approved_count = sum(
            1
            for row in snapshot.get("planned_maneuvers", []) or []
            if row.get("situation_class") == "approved"
            and row.get("planned_label") == "15:00"
            and str(row.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
        )
        self._check(
            scenario,
            "Snapshot de planeamento",
            "Manobras aprovadas visíveis",
            "O planeamento mostra quatro aprovações de teste nessa hora.",
            approved_count == 4,
            f"{approved_count} aprovada(s) às 15:00.",
        )
        self._finish_scenario(scenario)

    def _scenario_berth_capacity_rules(self) -> None:
        scenario = self._new_scenario(
            "Regras operacionais",
            "Capacidade de cais por LOA",
            "Executa a matriz de TMS 1, TMS 2 e Autoeuropa para garantir que navios compridos ocupam todos os slots físicos necessários.",
        )
        for item in berth_capacity_test_matrix():
            result = _evaluate_berth_capacity_case(item)
            self._check_state(
                scenario,
                item.get("label", item.get("id", "Caso sem nome")),
                item.get("group", "Capacidade de cais"),
                item.get("expected", ""),
                result["observed"],
                state=result["state"],
            )
        self._finish_scenario(scenario)

    def _scenario_anchorages_and_duplicates(self) -> None:
        scenario = self._new_scenario(
            "Regras operacionais",
            "Fundeadouros e duplicados",
            "Confirma que fundeadouros não bloqueiam como cais e que IMO ativo duplicado é recusado.",
        )
        first = self._create_port_call(
            scenario,
            index=20,
            name="FUNDEADOURO A",
            eta=self._future(days=9, hour=9),
            berth="Fundeadouro Norte",
            last_port="Leixões",
            next_port="Sines",
            vessel_type="Carga geral",
        )
        second = self._create_port_call(
            scenario,
            index=21,
            name="FUNDEADOURO B",
            eta=self._future(days=9, hour=10),
            berth="Fundeadouro Norte",
            last_port="Vigo",
            next_port="Lisboa",
            vessel_type="Carga geral",
        )
        if not first or not second:
            self._finish_scenario(scenario)
            return
        first = self._complete_entry(self._approve_entry(first), at=self._event_time(days=9, hour=9, minute=30))
        self._step(
            scenario,
            "Aprovar segunda entrada em fundeadouro",
            "Fundeadouro não conta como cais ocupado",
            "Outra entrada para Fundeadouro Norte pode ser aprovada.",
            lambda: self._approve_entry(second),
        )
        duplicate_profile = self._profile(22, vessel_type="Carga geral")
        duplicate_profile["vessel_imo"] = first.get("vessel_imo", "")
        self._step(
            scenario,
            "Recusar IMO duplicado",
            "Validação de escala ativa",
            "Uma escala ativa com o mesmo IMO é recusada.",
            lambda: services.store.create_port_call(
                vessel_name=f"{TEST_VESSEL_PREFIX} IMO DUPLICADO {self.run_token}",
                eta=self._future(days=9, hour=11),
                created_by=self.actor_username,
                constraints=[],
                berth="ALSTOM",
                last_port="Lisboa",
                next_port="Faro",
                notes="Teste: duplicado deve falhar.",
                **duplicate_profile,
            ),
            expected_error="Já existe uma escala ativa com o IMO",
        )
        self._finish_scenario(scenario)

    def _scenario_vessel_catalog_management(self) -> None:
        scenario = self._new_scenario(
            "Catálogo de navios",
            "Gestão controlada de navios frequentes",
            "Valida criação/importação lógica, preenchimento por catálogo, sincronização com escalas ativas, filtros, exportação TXT e remoção sem deixar alterações definitivas.",
        )
        original_state = self._runtime_state_snapshot(VESSEL_CATALOG_STATE_KEY)
        catalog_only: dict = {}
        sync_record: dict = {}
        active_port_call: dict | None = None
        try:
            catalog_only_payload = self._catalog_payload(
                30,
                name="CATALOGO PURO",
                vessel_type="roro",
                call_sign=f"TQACAT{self.run_token[-3:]}",
            )
            catalog_only_payload.update(
                {
                    "service_rate_profile": "Linha regular",
                    "regular_line_calls_365d": "12",
                    "tup_reduction_profile": "regular_line",
                    "service_notes": "Teste controlado de ficha sem escala ativa.",
                }
            )
            catalog_only = self._step(
                scenario,
                "Criar ficha de catálogo",
                "Importação/upsert de navio",
                "A ficha é validada, normaliza aliases de tipo e fica guardada no catálogo.",
                lambda: _upsert_vessel_catalog_record(catalog_only_payload, updated_by=self.actor_username),
            ) or {}
            self._check(
                scenario,
                "Tipo canónico",
                "Alias Ro-Ro",
                "O alias roro passa para Roll-on/Roll-off.",
                isinstance(catalog_only, dict) and catalog_only.get("vessel_type") == "Roll-on/Roll-off",
                catalog_only.get("vessel_type", "--") if isinstance(catalog_only, dict) else "--",
            )

            filled_payload = self._step(
                scenario,
                "Preencher escala por catálogo",
                "Importação de escala com IMO existente",
                "Uma escala importada pode indicar só o IMO e herdar a ficha técnica do catálogo.",
                lambda: _coerce_port_call_payload_with_catalog(
                    {
                        "vessel_imo": catalog_only_payload["vessel_imo"],
                        "eta": self._future(days=13, hour=9),
                        "berth": "Cais 10 / Autoeuropa",
                        "last_port": "Southampton",
                        "next_port": "Vigo",
                        "draft_m": "7.8",
                        "tug_count": 2,
                        "constraints": [],
                        "notes": "Teste: escala preenchida a partir do catálogo.",
                    }
                ),
            ) or {}
            self._check(
                scenario,
                "Ficha herdada",
                "Dados do navio na escala importada",
                "Nome, tipo e dimensões vêm da ficha guardada.",
                isinstance(filled_payload, dict)
                and filled_payload.get("vessel_name") == catalog_only_payload["vessel_name"]
                and filled_payload.get("vessel_type") == "Roll-on/Roll-off"
                and filled_payload.get("vessel_loa_m") == catalog_only_payload["vessel_loa_m"],
                (
                    f"{filled_payload.get('vessel_name', '--')} · {filled_payload.get('vessel_type', '--')} · "
                    f"LOA {filled_payload.get('vessel_loa_m', '--')}"
                    if isinstance(filled_payload, dict)
                    else "--"
                ),
            )

            catalog_answer = self._step(
                scenario,
                "Consultar ficha por bot",
                "Ficha de catálogo sem escala ativa",
                "A consulta por nome/call sign encontra o navio guardado.",
                lambda: answer_direct_operational_query(
                    f"Dados navio {catalog_only_payload['vessel_name']} call sign {catalog_only_payload['vessel_call_sign']}"
                ),
            )
            catalog_text = (catalog_answer or {}).get("answer", "") if isinstance(catalog_answer, dict) else ""
            self._check(
                scenario,
                "Resposta ficha catálogo",
                "Dados técnicos do navio",
                "A resposta mostra ficha, IMO, indicativo e dados técnicos.",
                catalog_only_payload["vessel_name"] in catalog_text
                and catalog_only_payload["vessel_call_sign"] in catalog_text
                and "Ficha de catálogo" in catalog_text
                and "GT" in catalog_text,
                catalog_text[:260] or "--",
            )

            active_port_call = self._create_port_call(
                scenario,
                index=31,
                name="CATALOGO SINCRONIZAR",
                eta=self._future(days=13, hour=11),
                berth="Cais 11 / Autoeuropa",
                last_port="Leixões",
                next_port="Sines",
                vessel_type="Carga geral",
            )
            if active_port_call:
                sync_payload = {
                    **_validate_vessel_catalog_record(
                        {
                            "vessel_name": active_port_call.get("vessel_name", ""),
                            "vessel_imo": active_port_call.get("vessel_imo", ""),
                            "vessel_call_sign": active_port_call.get("vessel_call_sign", ""),
                            "vessel_flag": active_port_call.get("vessel_flag", ""),
                            "vessel_type": "roro",
                            "vessel_loa_m": "199.9",
                            "vessel_beam_m": "32.2",
                            "vessel_gt_t": "52000",
                            "vessel_dwt_t": "18000",
                            "vessel_max_draft_m": "9.8",
                            "vessel_bow_thruster": "yes",
                            "vessel_stern_thruster": "unknown",
                        }
                    ),
                    "regular_line_calls_365d": "37",
                    "service_notes": "Teste: sincronização de ficha ativa.",
                }
                sync_record = self._step(
                    scenario,
                    "Atualizar ficha com escala ativa",
                    "Edição do catálogo",
                    "A ficha editada fica guardada e pronta para sincronizar a escala ativa.",
                    lambda: _upsert_vessel_catalog_record(sync_payload, updated_by=self.actor_username, validate=False),
                ) or {}
                synced_count = self._step(
                    scenario,
                    "Sincronizar escala ativa",
                    "Catálogo -> escala",
                    "A alteração do catálogo atualiza a ficha do navio na escala ativa correspondente.",
                    lambda: _sync_vessel_catalog_record_to_active_port_calls(
                        sync_record,
                        updated_by=self.actor_username,
                    ),
                )
                refreshed = services.store.get_port_call(active_port_call["id"])
                self._check(
                    scenario,
                    "Escala sincronizada",
                    "Tipo e dimensões na escala ativa",
                    "A escala ativa passa a refletir o tipo e LOA editados no catálogo.",
                    bool(synced_count)
                    and refreshed.get("vessel_type") == "Roll-on/Roll-off"
                    and refreshed.get("vessel_loa_m") == "199.9",
                    f"{synced_count or 0} sincronizada(s) · {refreshed.get('vessel_type', '--')} · LOA {refreshed.get('vessel_loa_m', '--')}",
                )

            activity = services.store.get_port_activity_snapshot(window_days=3650)
            vessels = _build_vessel_catalog_options(activity)
            filtered = _filter_vessel_catalog_options(
                vessels,
                q=catalog_only_payload["vessel_call_sign"],
                vessel_type="Roll-on/Roll-off",
            )
            self._check(
                scenario,
                "Filtros de catálogo",
                "Busca e tipo de navio",
                "A exportação/consulta filtrada devolve apenas navios compatíveis com os filtros.",
                bool(filtered)
                and all(catalog_only_payload["vessel_call_sign"] in (item.get("vessel_call_sign") or "") for item in filtered),
                f"{len(filtered)} resultado(s) para {catalog_only_payload['vessel_call_sign']}.",
            )

            txt_body = self._step(
                scenario,
                "Exportar ficha TXT",
                "Ficha imprimível/exportável",
                "A ficha de navio gera texto com identificação e dados técnicos.",
                lambda: _vessel_catalog_txt(catalog_only),
            )
            self._check(
                scenario,
                "Conteúdo TXT",
                "Exportação individual",
                "O texto exportado inclui nome, IMO e tipo.",
                isinstance(txt_body, str)
                and catalog_only_payload["vessel_name"] in txt_body
                and catalog_only_payload["vessel_imo"] in txt_body
                and "Roll-on/Roll-off" in txt_body,
                (txt_body or "")[:220] if isinstance(txt_body, str) else "--",
            )

            removed = self._step(
                scenario,
                "Remover navio individual",
                "Apagar ficha frequente",
                "A remoção individual oculta a ficha sem apagar escalas.",
                lambda: _remove_vessel_catalog_record(catalog_only.get("key", ""), hide=True),
            ) or {}
            after_remove = _build_vessel_catalog_options(services.store.get_port_activity_snapshot(window_days=3650))
            self._check(
                scenario,
                "Ficha removida",
                "Catálogo visível",
                "O navio removido deixa de aparecer no catálogo.",
                removed.get("key") == catalog_only.get("key")
                and not any(item.get("key") == catalog_only.get("key") for item in after_remove),
                removed.get("vessel_name") or removed.get("key", "--"),
            )

            _upsert_vessel_catalog_record(catalog_only_payload, updated_by=self.actor_username)
            test_vessels_before_clear = [
                item
                for item in _build_vessel_catalog_options(services.store.get_port_activity_snapshot(window_days=3650))
                if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
            ]
            removed_keys = self._step(
                scenario,
                "Remover todos os navios TESTE QA",
                "Limpeza global controlada",
                "O teste exercita a remoção em lote apenas sobre fichas TESTE QA, sem ocultar navios reais do catálogo.",
                lambda: [
                    _remove_vessel_catalog_record(item.get("key", ""), hide=True).get("key")
                    for item in test_vessels_before_clear
                    if item.get("key")
                ],
            ) or []
            test_vessels_after_clear = [
                item
                for item in _build_vessel_catalog_options(services.store.get_port_activity_snapshot(window_days=3650))
                if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
            ]
            self._check(
                scenario,
                "Lote TESTE QA removido",
                "Remoção em lote sem impacto real",
                "As fichas de teste são ocultadas e navios reais não são tocados.",
                bool(removed_keys) and not test_vessels_after_clear,
                f"{len(removed_keys)} chave(s) TESTE QA removida(s).",
            )
        finally:
            try:
                self._restore_runtime_state(VESSEL_CATALOG_STATE_KEY, original_state)
                self._record_step(
                    scenario,
                    label="Repor catálogo",
                    element="Estado original do catálogo",
                    expected="O teste não deixa alterações definitivas no catálogo.",
                    observed="Snapshot inicial reposto.",
                    state="passed",
                )
            except Exception as exc:
                self._record_step(
                    scenario,
                    label="Repor catálogo",
                    element="Estado original do catálogo",
                    expected="O teste não deixa alterações definitivas no catálogo.",
                    observed=str(exc),
                    state="failed",
                )
        self._finish_scenario(scenario)

    def _slash_datetime_label(self, *, days: int, hour: int, minute: int = 0) -> str:
        return datetime.fromisoformat(self._future(days=days, hour=hour, minute=minute)).strftime("%d/%m/%Y, %H:%M")

    def _slash_context(self) -> dict:
        records = self._created_records()
        if not records:
            return {
                "ref": "PTSET26TEST0001",
                "vessel_name": f"{TEST_VESSEL_PREFIX} SLASH",
                "maneuver_id": "00000000",
                "maneuver_id_short": "00000000",
                "maneuver_type": "entry",
                "maneuver_type_label": "entrada",
                "imo": "9990001",
            }

        target = next(
            (
                record
                for record in records
                if record.get("status") in {"scheduled", "in_port"}
                and record.get("maneuver_history")
            ),
            records[0],
        )
        maneuvers = list(target.get("maneuver_history") or [])
        maneuver = next((item for item in maneuvers if item.get("state") == "pending"), None)
        maneuver = maneuver or next((item for item in maneuvers if item.get("id")), {})
        maneuver_type = (maneuver.get("type") or "entry").strip().lower()
        type_labels = {"entry": "entrada", "departure": "saída", "shift": "mudança"}
        return {
            "ref": target.get("reference_code") or target.get("id") or "PTSET26TEST0001",
            "vessel_name": target.get("vessel_name") or f"{TEST_VESSEL_PREFIX} SLASH",
            "maneuver_id": maneuver.get("id") or "00000000",
            "maneuver_id_short": str(maneuver.get("id") or "00000000")[:8],
            "maneuver_type": maneuver_type,
            "maneuver_type_label": type_labels.get(maneuver_type, "entrada"),
            "imo": target.get("vessel_imo") or "9990001",
        }

    def _slash_expected_intents(self, command: str) -> set[str]:
        if command == "help":
            return {"help"}
        if command in QUERY_SLASH_COMMANDS:
            return {"query"}
        if command == "validate_maneuver":
            return {"validate", "template"}
        if command == "event_report":
            return {"event_report"}
        return {"action", "template"}

    def _slash_sample_for_alias(self, alias: str, command: str, ctx: dict, index: int) -> str:
        head = f"/{alias}"
        if command == "help":
            return head
        if command == "local_warnings":
            return head
        if command == "wave":
            return head
        if command == "tides":
            return f"{head} hoje"
        if command == "weather":
            return f"{head} hoje"
        if command in {"planning", "planning_approved", "planning_pending"}:
            return head
        if command == "colreg_list":
            return head
        if command == "colreg_rule":
            return f"{head} 35"
        if command == "rule":
            return head
        if command in {"consult_scale", "consult_scale_cost"}:
            return f"{head} {ctx['ref']}"
        if command in {"consult_maneuver", "consult_maneuver_cost"}:
            return f"{head} {ctx['maneuver_id_short']}"
        if command == "consult_vessel":
            return f"{head} {ctx['vessel_name']}"
        if command == "validate_maneuver":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
            )
        if command == "event_report":
            return f"{head} SEGURANCA. Cais 10. Teste operacional sem impacto."
        if command == "register_scale":
            return (
                f"{head}\n"
                f"Nome: {TEST_VESSEL_PREFIX} SLASH REGISTO {self.run_token} {index}\n"
                f"ETA de chegada: {self._slash_datetime_label(days=12, hour=10)}\n"
                "Cais previsto: ALSTOM\n"
                "Último porto: Lisboa\n"
                "Próximo destino: Faro\n"
                f"IMO: {self._imo(70 + index)}\n"
                f"Indicativo: TQASL{index:02d}\n"
                "Bandeira: Portugal\n"
                "Tipo de navio: Carga geral\n"
                "LOA: 118.0\n"
                "Boca: 19.2\n"
                "GT: 7200\n"
                "DWT: 9400\n"
                "Calado máximo: 7.2\n"
                "Bow thruster: yes\n"
                "Stern thruster: unknown\n"
                "Calado operacional: 6.4\n"
                "Rebocadores: 1\n"
                "Observações: teste de parser slash\n"
            )
        if command == "edit_scale":
            return (
                f"{head}\n"
                f"Ref: {ctx['ref']}\n"
                f"ETA: {self._slash_datetime_label(days=12, hour=11)}\n"
                "Motivo da alteração: Teste de comando slash\n"
            )
        if command == "delete_scale":
            return f"{head}\nRef: {ctx['ref']}\n"
        if command == "create_maneuver":
            return (
                f"{head}\n"
                f"Ref: {ctx['ref']}\n"
                "Tipo de manobra: saída\n"
                f"Hora prevista: {self._slash_datetime_label(days=12, hour=18)}\n"
                "Destino: Sines\n"
                "Calado: 6.5\n"
                "Rebocadores: 2\n"
                "Restrições: daylight\n"
                "Observações: teste de criação de manobra\n"
            )
        if command == "edit_maneuver":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
                f"Hora prevista: {self._slash_datetime_label(days=12, hour=19)}\n"
                "Motivo da alteração: Teste de edição slash\n"
            )
        if command == "delete_maneuver":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
            )
        if command == "approve_maneuver":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
            )
        if command == "create_report":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
                f"Início da manobra: {self._slash_datetime_label(days=12, hour=9)}\n"
                f"Fim da manobra: {self._slash_datetime_label(days=12, hour=10)}\n"
                "Calado: 6.4\n"
                "Observações: teste de registo slash\n"
            )
        if command == "edit_report":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
                f"Início da manobra: {self._slash_datetime_label(days=12, hour=9)}\n"
                f"Fim da manobra: {self._slash_datetime_label(days=12, hour=10)}\n"
                "Calado: 6.4\n"
                "Motivo da alteração: Teste de revisão de registo\n"
            )
        if command == "delete_report":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
            )
        if command == "abort_maneuver":
            return (
                f"{head}\n"
                f"ID da manobra: {ctx['maneuver_id_short']}\n"
                f"Tipo de manobra: {ctx['maneuver_type_label']}\n"
                "Motivo: Teste de aborto slash\n"
            )
        return head

    def _slash_query_argument(self, parsed: dict) -> str:
        return " ".join(str((parsed or {}).get("argument") or "").split())

    def _scenario_slash_commands(self) -> None:
        scenario = self._new_scenario(
            "Comandos /",
            "Cobertura completa dos comandos slash",
            "Valida todos os aliases definidos no sistema, executa consultas seguras e captura erros por comando.",
        )
        ctx = self._slash_context()
        tested_aliases: list[str] = []
        for index, (alias, command) in enumerate(sorted(SLASH_COMMAND_ALIASES.items()), start=1):
            sample = self._slash_sample_for_alias(alias, command, ctx, index)
            parsed = self._step(
                scenario,
                f"Parser /{alias}",
                f"Alias de {command}",
                "O comando é reconhecido e encaminhado para a intenção correta.",
                lambda sample=sample: parse_slash_command(sample, "admin"),
            )
            tested_aliases.append(alias)
            expected_intents = self._slash_expected_intents(command)
            observed_intent = (parsed or {}).get("intent") if isinstance(parsed, dict) else ""
            observed_target = (
                (parsed or {}).get("command")
                or (parsed or {}).get("proposal", {}).get("action")
                or observed_intent
                if isinstance(parsed, dict)
                else ""
            )
            self._check(
                scenario,
                f"Cobertura /{alias}",
                "Resultado do parser",
                f"Intenção esperada: {', '.join(sorted(expected_intents))}.",
                isinstance(parsed, dict) and observed_intent in expected_intents,
                f"intent={observed_intent or '--'} · destino={observed_target or '--'}",
            )

            proposal = (parsed or {}).get("proposal") if isinstance(parsed, dict) else None
            if isinstance(proposal, dict) and proposal.get("action"):
                finalized = self._step(
                    scenario,
                    f"Resolver alvo /{alias}",
                    "Ref/ID/campos do comando",
                    "O comando resolve escala/manobra por Ref ou ID sem obrigar a repetir todos os campos.",
                    lambda proposal=proposal: finalize_operational_proposal(deepcopy(proposal)),
                )
                self._check(
                    scenario,
                    f"Diagnóstico /{alias}",
                    "Campos reconhecidos e em falta",
                    "O diagnóstico fica explícito e sem erro inesperado.",
                    isinstance(finalized, dict)
                    and finalized.get("intent") in {"action", "template", "unsupported"}
                    and "#ERR-9000" not in str(finalized),
                    (
                        f"intent={(finalized or {}).get('intent', '--') if isinstance(finalized, dict) else '--'} · "
                        f"ação={(finalized or {}).get('action', '--') if isinstance(finalized, dict) else '--'} · "
                        f"em falta={', '.join((finalized or {}).get('missing_fields') or []) if isinstance(finalized, dict) else '--'}"
                    ),
                )

            if isinstance(parsed, dict) and parsed.get("intent") == "query":
                payload = self._step(
                    scenario,
                    f"Executar /{alias}",
                    "Consulta slash",
                    "A consulta devolve uma resposta controlada, mesmo que a fonte externa esteja indisponível.",
                    lambda parsed=parsed: answer_slash_query(
                        parsed.get("command", ""),
                        self._slash_query_argument(parsed),
                        "admin",
                    ),
                )
                answer = (payload or {}).get("answer", "") if isinstance(payload, dict) else ""
                self._check(
                    scenario,
                    f"Resposta /{alias}",
                    "Resultado visível",
                    "A resposta contém texto e answer_origin.",
                    isinstance(payload, dict) and bool(answer.strip()) and bool(payload.get("answer_origin")),
                    f"{(payload or {}).get('answer_origin', '--') if isinstance(payload, dict) else '--'} · {answer[:180] or '--'}",
                )

            if isinstance(parsed, dict) and parsed.get("intent") == "validate":
                payload = self._step(
                    scenario,
                    f"Executar /{alias}",
                    "Validação determinística de manobra",
                    "A validação devolve resposta própria e não falha a página.",
                    lambda parsed=parsed: answer_slash_validation(parsed.get("target") or {}, "admin"),
                )
                answer = (payload or {}).get("answer", "") if isinstance(payload, dict) else ""
                self._check(
                    scenario,
                    f"Resposta /{alias}",
                    "Checklist de manobra",
                    "A resposta contém texto e origem slash_validation.",
                    isinstance(payload, dict)
                    and bool(answer.strip())
                    and payload.get("answer_origin") == "slash_validation",
                    answer[:220] or "--",
                )

        self._check(
            scenario,
            "Cobertura total de aliases",
            "Inventário dos comandos /",
            f"Todos os {len(SLASH_COMMAND_ALIASES)} aliases definidos são testados.",
            len(set(tested_aliases)) == len(SLASH_COMMAND_ALIASES),
            f"{len(set(tested_aliases))}/{len(SLASH_COMMAND_ALIASES)} aliases testados.",
        )

        help_text = build_slash_help("admin")
        missing_help = [
            alias
            for alias in (
                "help",
                "avisos-locais",
                "ondulacao",
                "mares",
                "meteorologia",
                "colreg-lista",
                "colreg",
                "planeamento",
                "consultar-escala",
                "consultar-manobra",
                "consultar-navio",
                "validar-manobra",
                "registar-escala",
                "editar-escala",
                "apagar-escala",
                "criar-manobra",
                "editar-manobra",
                "apagar-manobra",
                "aprovar",
                "registar-manobra",
                "editar-registo-manobra",
                "apagar-registo-manobra",
                "abortar",
                "reportar-evento",
            )
            if f"/{alias}" not in help_text
        ]
        self._check(
            scenario,
            "Ajuda cobre comandos principais",
            "/help",
            "A ajuda admin lista os comandos slash principais.",
            not missing_help,
            "Em falta: " + ", ".join(missing_help) if missing_help else "Comandos principais presentes.",
        )

        unknown = self._step(
            scenario,
            "Comando desconhecido",
            "Tratamento de erro",
            "Um comando inválido devolve ajuda, sem exceção.",
            lambda: parse_slash_command("/comando-que-nao-existe", "admin"),
        )
        self._check(
            scenario,
            "Erro controlado",
            "Slash inválido",
            "Resposta contém comando não reconhecido.",
            isinstance(unknown, dict) and "Comando não reconhecido" in unknown.get("answer", ""),
            (unknown or {}).get("answer", "")[:180] if isinstance(unknown, dict) else "--",
        )
        self._finish_scenario(scenario)

    def _scenario_live_feed_snapshot(self) -> None:
        scenario = self._new_scenario(
            "Live feed/snapshot",
            "Dados públicos operacionais",
            "Valida se o snapshot alimenta chegadas previstas, navios em porto, planeamento e saídas recentes.",
        )
        snapshot = self._step(
            scenario,
            "Carregar snapshot",
            "Fonte operacional",
            "O snapshot devolve todas as coleções esperadas.",
            lambda: services.store.get_port_activity_snapshot(window_days=3650),
        )
        if not isinstance(snapshot, dict):
            self._finish_scenario(scenario)
            return
        for key in ("arrivals", "in_port", "departed", "planned_maneuvers", "stats"):
            self._check(
                scenario,
                f"Chave {key}",
                "Estrutura do live feed",
                f"A coleção {key} existe no snapshot.",
                key in snapshot,
                f"{key}: {type(snapshot.get(key)).__name__}",
            )

        test_arrivals = [
            item
            for item in snapshot.get("arrivals", []) or []
            if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
        ]
        self._check(
            scenario,
            "Chegadas previstas com ETA",
            "Previsões de chegada",
            "As chegadas previstas de teste têm ETA e manobra de entrada identificável.",
            bool(test_arrivals)
            and all((item.get("eta") or item.get("eta_label")) for item in test_arrivals)
            and any(
                latest_maneuver_by_type(item, "entry") and latest_maneuver_by_type(item, "entry").get("id")
                for item in test_arrivals
            ),
            f"{len(test_arrivals)} chegada(s) de teste.",
        )

        test_planned = [
            item
            for item in snapshot.get("planned_maneuvers", []) or []
            if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
        ]
        self._check(
            scenario,
            "Planeamento detalhado",
            "Manobras públicas",
            "Cada linha de planeamento tem tipo, estado, origem/destino, ID da manobra e agente.",
            bool(test_planned)
            and all(
                item.get("maneuver_label")
                and item.get("situation_label")
                and item.get("maneuver_id")
                and item.get("local_origin")
                and item.get("local_destination")
                and item.get("agent_label")
                for item in test_planned
            ),
            f"{len(test_planned)} manobra(s) de teste no planeamento.",
        )

        test_departed = [
            item
            for item in snapshot.get("departed", []) or []
            if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
        ]
        self._check(
            scenario,
            "Saídas recentes",
            "Arquivo público recente",
            "As escalas fechadas aparecem nas saídas recentes.",
            bool(test_departed),
            f"{len(test_departed)} saída(s) de teste.",
        )

        test_in_port = [
            item
            for item in snapshot.get("in_port", []) or []
            if str(item.get("vessel_name") or "").startswith(TEST_VESSEL_PREFIX)
        ]
        self._check(
            scenario,
            "Navios em porto",
            "Ocupação e localização",
            "O snapshot mostra navios de teste em porto com cais/localização.",
            bool(test_in_port) and all(item.get("berth_label") or item.get("berth") for item in test_in_port),
            f"{len(test_in_port)} navio(s) de teste em porto.",
        )

        planning_answer = self._step(
            scenario,
            "Pergunta natural sobre planeamento",
            "Resposta direta operacional",
            "A pergunta 'Que navios estão no planeamento?' usa o snapshot e lista manobras.",
            lambda: answer_direct_operational_query("Que navios estão no planeamento?"),
        )
        planning_text = (planning_answer or {}).get("answer", "") if isinstance(planning_answer, dict) else ""
        self._check(
            scenario,
            "Resposta de planeamento",
            "Live feed no chat",
            "A resposta contém manobras, navio e ID curto.",
            "Manobras planeadas" in planning_text
            and TEST_VESSEL_PREFIX in planning_text
            and "manobra" in planning_text,
            planning_text[:260] or "--",
        )

        arrivals_answer = self._step(
            scenario,
            "Pergunta natural sobre chegadas",
            "Previsões de chegada no chat",
            "A resposta mostra ETA real, porto de origem, cais destino e entrada.",
            lambda: answer_direct_operational_query("Algum navio nas previsões de chegada?"),
        )
        arrivals_text = (arrivals_answer or {}).get("answer", "") if isinstance(arrivals_answer, dict) else ""
        self._check(
            scenario,
            "Resposta de chegadas",
            "Live feed no chat",
            "A resposta não deve cair em ETA Sem hora para as escalas de teste.",
            TEST_VESSEL_PREFIX in arrivals_text
            and "ETA Sem hora" not in arrivals_text
            and "entrada" in arrivals_text,
            arrivals_text[:260] or "--",
        )

        ships_answer = self._step(
            scenario,
            "Pergunta natural sobre navios em cais",
            "Navios em porto no chat",
            "A resposta devolve contagem e detalhe operacional.",
            lambda: answer_direct_operational_query("Temos quantos navios em cais?"),
        )
        ships_text = (ships_answer or {}).get("answer", "") if isinstance(ships_answer, dict) else ""
        self._check(
            scenario,
            "Resposta de ocupação",
            "Live feed no chat",
            "A resposta menciona navios/cais ou ocupação.",
            any(token in ships_text.casefold() for token in ("navio", "cais", "ocupação", "ocupacao")),
            ships_text[:220] or "--",
        )
        self._finish_scenario(scenario)

    def _scenario_site_queries_and_archive(self) -> None:
        scenario = self._new_scenario(
            "Consultas site/live feed",
            "Ficha, escala, manobra e arquivo",
            "Valida respostas determinísticas e fontes do portal para ficha de navio, detalhes de escala/manobra, saídas recentes e arquivo.",
        )
        port_call = self._create_port_call(
            scenario,
            index=42,
            name="CONSULTAS SITE",
            eta=self._future(days=14, hour=8),
            berth="Tanquisado (lado jusante)",
            last_port="Sines",
            next_port="Lisboa",
            vessel_type="Graneis líquidos",
            constraints=["gas"],
        )
        if not port_call:
            self._finish_scenario(scenario)
            return

        port_call = self._step(
            scenario,
            "Preparar entrada concluída",
            "Dados base para consultas",
            "A escala fica com entrada aprovada, concluída e registada.",
            lambda: services.store.attach_entry_report(
                self._complete_entry(
                    self._approve_entry(port_call),
                    at=self._event_time(days=14, hour=9),
                )["id"],
                updated_by=self.actor_username,
                maneuver_started_at=self._event_time(days=14, hour=8, minute=10),
                maneuver_finished_at=self._event_time(days=14, hour=9),
                draft_m="7.1",
                notes="Teste: entrada para consultas do site.",
                maneuver_id=self._latest(port_call, "entry").get("id"),
            ),
        ) or port_call
        port_call = self._step(
            scenario,
            "Preparar saída arquivada",
            "Arquivo de manobras",
            "A escala fica com saída aprovada, concluída e registada para consulta histórica.",
            lambda: services.store.attach_departure_report(
                self._complete_departure(
                    self._approve_departure(
                        services.store.schedule_departure_plan(
                            port_call["id"],
                            planned_departure_at=self._future(days=15, hour=10),
                            updated_by=self.actor_username,
                            next_port="Lisboa",
                            constraints=["gas"],
                            departure_plan_note="Teste: saída para arquivo.",
                            draft_m="7.2",
                            tug_count="2",
                        )
                    ),
                    at=self._event_time(days=15, hour=10, minute=45),
                )["id"],
                updated_by=self.actor_username,
                maneuver_started_at=self._event_time(days=15, hour=10),
                maneuver_finished_at=self._event_time(days=15, hour=10, minute=45),
                draft_m="7.0",
                notes="Teste: saída arquivada para consultas.",
                maneuver_id=self._latest(port_call, "departure").get("id"),
            ),
        ) or port_call
        port_call = services.store.get_port_call(port_call["id"])
        departure = self._latest(port_call, "departure")
        departure_id = departure.get("id", "")
        departure_short = departure_id[:8].upper()
        reference = port_call.get("reference_code", "")
        vessel_name = port_call.get("vessel_name", "")

        vessel_answer = self._step(
            scenario,
            "Consultar ficha de navio",
            "Dados técnicos e operacionais",
            "A pergunta natural devolve ficha, escala, localização, manobras e agência.",
            lambda: answer_direct_operational_query(f"Dados navio {vessel_name} call sign {port_call.get('vessel_call_sign', '')}"),
        )
        vessel_text = (vessel_answer or {}).get("answer", "") if isinstance(vessel_answer, dict) else ""
        self._check(
            scenario,
            "Resposta ficha navio",
            "Ficha do navio no bot",
            "A resposta inclui identificação, ficha técnica, estado/localização e manobras.",
            vessel_name in vessel_text
            and reference in vessel_text
            and "GT" in vessel_text
            and "DWT" in vessel_text
            and "Manobras conhecidas" in vessel_text
            and departure_id in vessel_text,
            vessel_text[:300] or "--",
        )

        id_answer = self._step(
            scenario,
            "Consultar IDs",
            "Escala e manobra",
            "A pergunta por ID distingue referência da escala e ID real da manobra.",
            lambda: answer_direct_operational_query(
                f"Qual o id da escala do {vessel_name}? Depois diz também o id da manobra de saída."
            ),
        )
        id_text = (id_answer or {}).get("answer", "") if isinstance(id_answer, dict) else ""
        self._check(
            scenario,
            "Resposta IDs",
            "Tracking escala/manobra",
            "A resposta contém a referência da escala e o ID de manobra, não repete a Ref como se fosse manobra.",
            reference in id_text and departure_id in id_text and "ID da manobra" in id_text,
            id_text[:300] or "--",
        )

        approver_answer = self._step(
            scenario,
            "Consultar aprovação",
            "Quem aprovou e quem executou",
            "A pergunta sobre aprovação mostra validador, executante e ID da manobra.",
            lambda: answer_direct_operational_query(f"Quem aprovou a manobra do {vessel_name} para sair?"),
        )
        approver_text = (approver_answer or {}).get("answer", "") if isinstance(approver_answer, dict) else ""
        self._check(
            scenario,
            "Resposta aprovação",
            "Validador/piloto executante",
            "A resposta identifica aprovação, execução e manobra.",
            "aprovada por" in approver_text.casefold()
            and "execut" in approver_text.casefold()
            and departure_id in approver_text,
            approver_text[:300] or "--",
        )

        agent_answer = self._step(
            scenario,
            "Consultar agente/agência",
            "Agente de navegação",
            "Sempre que fala do agente deve incluir também a agência quando existe perfil.",
            lambda: answer_direct_operational_query(f"Qual era o agente do {vessel_name} na saída?"),
        )
        agent_text = (agent_answer or {}).get("answer", "") if isinstance(agent_answer, dict) else ""
        self._check(
            scenario,
            "Resposta agente",
            "Agente e agência",
            "A resposta menciona agente de navegação e agência/perfil.",
            "Agente de navegação" in agent_text
            and ("(" in agent_text or "agência" in agent_text.casefold() or "APSS" in agent_text),
            agent_text[:260] or "--",
        )

        recent_answer = self._step(
            scenario,
            "Consultar saídas recentes",
            "Partidas/arquivo público",
            "A pergunta natural sobre saídas recentes lista ATD, rota, manobra, agente, aprovação e execução.",
            lambda: answer_direct_operational_query("Alguma saída recente?"),
        )
        recent_text = (recent_answer or {}).get("answer", "") if isinstance(recent_answer, dict) else ""
        self._check(
            scenario,
            "Resposta saídas recentes",
            "Live feed de partidas",
            "A saída de teste aparece com piloto aprovador e executante.",
            vessel_name in recent_text
            and departure_short in recent_text.upper()
            and "aprovada por" in recent_text.casefold()
            and "executada por" in recent_text.casefold(),
            recent_text[:320] or "--",
        )

        scale_sources = self._step(
            scenario,
            "Fonte de escala",
            "Registo de escalas do portal",
            "As fontes do bot incluem a escala quando a pergunta aponta para a Ref.",
            lambda: build_operational_chat_sources(f"Dados da escala {reference} do navio {vessel_name}"),
        ) or []
        scale_text = "\n".join(str(source.get("snippet") or "") for source in scale_sources if isinstance(source, dict))
        self._check(
            scenario,
            "Escala nas fontes",
            "Contexto do site",
            "A fonte de escalas contém Ref, navio, estado, cais e agente.",
            reference in scale_text and vessel_name in scale_text and "Registo de escalas" in scale_text,
            scale_text[:320] or "--",
        )

        archive_sources = self._step(
            scenario,
            "Fonte de arquivo",
            "Arquivo de manobras",
            "As fontes do bot incluem manobras arquivadas com ID, rota e intervenientes.",
            lambda: build_operational_chat_sources(f"Arquivo de manobras do {vessel_name} saída {departure_short}"),
        ) or []
        archive_text = "\n".join(str(source.get("snippet") or "") for source in archive_sources if isinstance(source, dict))
        self._check(
            scenario,
            "Arquivo nas fontes",
            "Histórico operacional",
            "O arquivo contém a manobra concluída, rota, validador, executante e agente.",
            vessel_name in archive_text
            and departure_id in archive_text
            and "validado por" in archive_text.casefold()
            and "executado por" in archive_text.casefold(),
            archive_text[:360] or "--",
        )
        self._finish_scenario(scenario)

    def _scenario_live_environment_queries(self) -> None:
        scenario = self._new_scenario(
            "Live feed/snapshot",
            "Ambiente live e avisos",
            "Exercita perguntas live de luz do dia, lua, meteorologia e avisos locais com erro tratado quando uma fonte externa estiver indisponível.",
        )
        checks = [
            (
                "Luz do dia",
                "Qual o período luminoso para hoje?",
                ("Período luminoso", "meteorologia live", "não está configurada", "não consegui"),
            ),
            (
                "Fase da lua",
                "Qual a fase da lua hoje?",
                ("Fase da lua", "Lua", "meteorologia live", "não está configurada", "não consegui"),
            ),
            (
                "Meteorologia hoje",
                "Quais as previsões meteorológicas para hoje?",
                ("Previsão meteorológica", "Resumo das próximas horas", "meteorologia live", "não está configurada", "não consegui"),
            ),
            (
                "Meteorologia próximos dias",
                "Meteo próximos dias",
                ("Previsão geral", "vento médio", "meteorologia live", "não está configurada", "não consegui"),
            ),
            (
                "Avisos locais",
                "Que avisos locais estão em vigor?",
                ("Avisos locais", "aviso", "não estão configurados", "não consegui", "Sem avisos"),
            ),
            (
                "Aviso local individual",
                "Qual é o aviso local 91/26?",
                ("91/26", "Aviso", "Anav", "não estão configurados", "não consegui", "Sem avisos"),
            ),
        ]
        for label, question, expected_tokens in checks:
            payload = self._step(
                scenario,
                label,
                "Resposta live direta",
                "A consulta devolve resposta ou mensagem explícita de indisponibilidade, sem exceção.",
                lambda question=question: answer_direct_operational_query(question),
            )
            answer = (payload or {}).get("answer", "") if isinstance(payload, dict) else ""
            ok = isinstance(payload, dict) and bool(answer.strip()) and any(token in answer for token in expected_tokens)
            unavailable = any(
                marker in answer.casefold()
                for marker in (
                    "não está configurad",
                    "nao esta configurad",
                    "não estão configurad",
                    "nao estao configurad",
                    "não consegui",
                    "nao consegui",
                    "sem avisos",
                )
            )
            self._check_state(
                scenario,
                f"{label} tratada",
                "Erro trapping live",
                "A resposta é informativa; indisponibilidade externa conta como aviso, não como erro da página.",
                answer[:260] or "--",
                state="passed" if ok and not unavailable else "warning" if answer else "failed",
            )
        self._finish_scenario(scenario)

    def _scenario_bot_critical_matrix(self) -> None:
        scenario = self._new_scenario(
            "Matriz critica do bot",
            "Matriz critica de conversas e checklist",
            "Executa os casos automaticos da matriz visivel na pagina de testes: respostas diretas, fontes estruturadas e checklist de cais criticos.",
        )
        for item in critical_bot_test_matrix():
            runner = item.get("runner")
            if runner == "manual":
                continue
            expected_tokens = item.get("expected_tokens") or ()
            label = str(item.get("label") or item.get("id") or "Caso").strip()
            try:
                origin = ""
                if runner == "direct_operational":
                    payload = answer_direct_operational_query(item.get("question", "")) or {}
                    text = str(payload.get("answer") or "")
                    origin = str(payload.get("answer_origin") or "")
                    origin_ok = not item.get("expected_origin") or origin == item.get("expected_origin")
                elif runner == "operational_diagnostic":
                    diagnostic = build_operational_diagnostic(
                        item.get("question", ""),
                        history=item.get("history") or [],
                        knowledge_dir=_active_knowledge_dir() or "knowledge",
                    )
                    text = format_operational_diagnostic(diagnostic)
                    origin_ok = bool(diagnostic.get("present"))
                elif runner == "conversation_reasoning":
                    plan = build_chat_execution_plan(str(item.get("question") or ""))
                    state = build_conversation_reasoning_state(
                        str(item.get("question") or ""),
                        list(item.get("history") or []),
                        plan,
                    )
                    text = str((state or {}).get("summary") or "")
                    origin = "conversation_reasoning"
                    origin_ok = bool(state)
                elif runner == "knowledge_json":
                    text = _critical_json_source_text(str(item.get("source_path") or ""))
                    origin_ok = True
                elif runner == "knowledge_text":
                    text = _critical_text_source_text(str(item.get("source_path") or ""))
                    origin_ok = True
                elif runner == "berth_profile":
                    text = _critical_berth_profile_text(str(item.get("profile_query") or item.get("question") or ""))
                    origin_ok = True
                elif runner == "maneuver_checklist":
                    text = critical_maneuver_checklist_text(str(item.get("fixture") or ""))
                    origin_ok = True
                elif runner == "slash_validation":
                    text = critical_slash_validation_text(str(item.get("fixture") or ""))
                    origin_ok = True
                else:
                    text = ""
                    origin_ok = False
            except Exception as exc:
                self._record_step(
                    scenario,
                    label=label,
                    element=str(item.get("source") or runner or "--"),
                    expected=str(item.get("expected_summary") or "Caso automatico sem excecao."),
                    observed=str(exc),
                    state="failed",
                )
                continue

            missing = _missing_expected_tokens(text, expected_tokens)
            forbidden = _present_forbidden_tokens(text, item.get("forbidden_tokens") or ())
            expected_parts = [str(item.get("expected_summary") or "Termos criticos presentes.")]
            if item.get("expected_origin"):
                expected_parts.append(f"origem {item['expected_origin']}")
            self._check(
                scenario,
                label,
                str(item.get("source") or runner or "--"),
                " ".join(expected_parts),
                origin_ok and bool(text.strip()) and not missing and not forbidden,
                (
                    f"origem={origin or '--'} · falta={', '.join(missing) if missing else 'nada'} · "
                    f"proibido={', '.join(forbidden) if forbidden else 'nada'} · "
                    f"{text[:260] or '--'}"
                ),
            )
        self._finish_scenario(scenario)

    def _scenario_tug_guidance_queries(self) -> None:
        scenario = self._new_scenario(
            "Bot operacional",
            "Regras práticas de rebocadores",
            "Valida as respostas diretas do bot para a matriz prática de rebocadores, incluindo exceções de vento/cais e posicionamento.",
        )
        checks = [
            (
                "Ro-Ro grande com Norte forte",
                "Quantos reboques para RORO de 230m a entrar com vento norte forte?",
                ("Recomendo 4 rebocadores grandes", "mais de 220 m", "vento Norte forte"),
            ),
            (
                "Graneleiro TMS2 com Norte forte",
                "Um Graneleiro de 190m a sair do TMS2 com vento norte forte, quantos rebocadores leva?",
                ("Recomendo 4 rebocadores grandes", "vento Norte forte", "Tanquisado", "Eco-Oil"),
            ),
            (
                "Lisnave 100-150 m",
                "Um navio de 130m para a Lisnave precisa de quantos rebocadores?",
                ("Recomendo 3 rebocadores", "acima de 100 m ate 150 m", "Quatro rebocadores sao excessivos"),
            ),
            (
                "Tanquisado vento E forte",
                "A sair de Tanquisado com vento E forte, onde meto o reboque?",
                (
                    "Tanquisado com 3 rebocadores",
                    "1 a proa e 1 a popa",
                    "terceiro",
                    "Tanquisado a sair com vento E forte",
                    "1 rebocador a empurrar ao costado",
                    "largada dos cabos",
                ),
            ),
            (
                "Eco-Oil vento W forte",
                "A sair da Eco-Oil com vento W forte, onde meto o reboque?",
                (
                    "Eco-Oil com 3 rebocadores",
                    "1 a proa e 1 a popa",
                    "terceiro",
                    "Eco-Oil a sair com vento W forte",
                    "1 rebocador a empurrar ao costado",
                    "oposto da Tanquisado",
                ),
            ),
            (
                "Sem bowthruster pequeno mas fundo",
                "Navio sem bowthruster de 110m e calado 8m precisa de quantos rebocadores?",
                ("Recomendo 1 rebocador grande", "35 t", "nao rebocador pequeno"),
            ),
        ]
        for label, question, expected_tokens in checks:
            payload = self._step(
                scenario,
                label,
                "Resposta operacional direta",
                "A resposta usa tug_operational_guidance.json e mantém a recomendação prática esperada.",
                lambda question=question: answer_direct_operational_query(question),
            )
            answer = (payload or {}).get("answer", "") if isinstance(payload, dict) else ""
            origin = (payload or {}).get("answer_origin", "") if isinstance(payload, dict) else ""
            missing = [token for token in expected_tokens if token not in answer]
            self._check(
                scenario,
                f"{label} validado",
                "Conteúdo da resposta",
                "Origem operational_tug_guidance e termos críticos presentes.",
                origin == "operational_tug_guidance" and not missing,
                (
                    f"origem={origin or '--'} · falta={', '.join(missing) if missing else 'nada'} · "
                    f"{answer[:240] or '--'}"
                ),
            )
        self._finish_scenario(scenario)

    def _build_module_summaries(self) -> list[dict]:
        modules: dict[str, dict] = {}
        for scenario in self.scenarios:
            module_name = scenario.get("module") or "Geral"
            module = modules.setdefault(
                module_name,
                {
                    "name": module_name,
                    "scenarios": [],
                    "scenario_count": 0,
                    "total_steps": 0,
                    "passed_steps": 0,
                    "failed_steps": 0,
                    "warning_steps": 0,
                    "state": "passed",
                    "state_badge": "online",
                },
            )
            module["scenarios"].append(scenario)
            module["scenario_count"] += 1
            for step in scenario.get("steps", []):
                module["total_steps"] += 1
                if step.get("state") == "failed":
                    module["failed_steps"] += 1
                elif step.get("state") == "warning":
                    module["warning_steps"] += 1
                else:
                    module["passed_steps"] += 1

        ordered = list(modules.values())
        for module in ordered:
            module["state"] = (
                "failed"
                if module["failed_steps"]
                else "warning"
                if module["warning_steps"]
                else "passed"
            )
            module["state_badge"] = _status_badge(module["state"])
        return ordered

    def _append_cleanup_result(self, title: str, result: dict | None) -> None:
        if not result:
            return
        scenario = self._new_scenario(
            "Limpeza",
            title,
            "Regista a limpeza automática dos dados TESTE QA para manter a base controlada.",
        )
        errors = result.get("errors") or []
        self._record_step(
            scenario,
            label=title,
            element="Dados temporários TESTE QA",
            expected="Limpeza sem erros.",
            observed=(
                "; ".join(errors)
                if errors
                else f"{result.get('deleted_count', 0)} escala(s) removida(s)."
            ),
            state="failed" if errors or result.get("state") == "failed" else "passed",
        )
        self._finish_scenario(scenario)

    def run(self) -> dict:
        pre_cleanup = cleanup_operational_test_records()
        self._append_cleanup_result("Limpeza inicial", pre_cleanup)
        cleanup_result: dict | None = None
        try:
            self._scenario_natural_flow()
            self._scenario_shift_flow()
            self._scenario_berth_release()
            self._scenario_abort_keeps_block()
            self._scenario_capacity_limit()
            self._scenario_berth_capacity_rules()
            self._scenario_anchorages_and_duplicates()
            self._scenario_vessel_catalog_management()
            self._scenario_slash_commands()
            self._scenario_site_queries_and_archive()
            self._scenario_tug_guidance_queries()
            self._scenario_bot_critical_matrix()
            self._scenario_live_feed_snapshot()
            self._scenario_live_environment_queries()
        finally:
            if self.cleanup_after:
                cleanup_result = cleanup_operational_test_records()
                self._append_cleanup_result("Limpeza final", cleanup_result)

        modules = self._build_module_summaries()
        total_steps = sum(len(scenario["steps"]) for scenario in self.scenarios)
        failed_steps = sum(
            1
            for scenario in self.scenarios
            for step in scenario["steps"]
            if step.get("state") == "failed"
        )
        warning_steps = sum(
            1
            for scenario in self.scenarios
            for step in scenario["steps"]
            if step.get("state") == "warning"
        )
        passed_steps = total_steps - failed_steps - warning_steps
        state = "failed" if failed_steps else "warning" if warning_steps else "passed"
        finished_at = datetime.now().astimezone()
        return {
            "kind": "run",
            "state": state,
            "state_label": "Falhou" if state == "failed" else "Com avisos" if state == "warning" else "Tudo passou",
            "state_badge": _status_badge(state),
            "started_at_label": self.started_at.strftime("%d/%m/%Y %H:%M:%S"),
            "finished_at_label": finished_at.strftime("%d/%m/%Y %H:%M:%S"),
            "duration_ms": int((time.perf_counter() - self.started_perf) * 1000),
            "scenario_count": len(self.scenarios),
            "total_steps": total_steps,
            "passed_steps": passed_steps,
            "failed_steps": failed_steps,
            "warning_steps": warning_steps,
            "pre_cleanup": pre_cleanup,
            "cleanup": cleanup_result,
            "cleanup_after": self.cleanup_after,
            "retained_records": [] if self.cleanup_after else self.retained_records,
            "modules": modules,
            "scenarios": self.scenarios,
        }


def run_operational_flow_suite(*, actor_username: str, cleanup_after: bool = True) -> dict:
    return OperationalFlowSuite(actor_username=actor_username, cleanup_after=cleanup_after).run()
