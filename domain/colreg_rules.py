from __future__ import annotations

import re
from dataclasses import dataclass


COLREG_SOURCE_DOCUMENT = "RIEAM_COLREG_Regras_Estrada.txt"


@dataclass(frozen=True)
class ColregRule:
    number: int
    title: str
    part: str
    summary: str
    operational: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()


COLREG_RULES: tuple[ColregRule, ...] = (
    ColregRule(1, "Campo de aplicação", "Parte A - Generalidades", "Define onde o RIEAM/COLREG se aplica e admite regras locais complementares em portos, rios, radas e vias interiores."),
    ColregRule(2, "Responsabilidade", "Parte A - Generalidades", "Nenhuma regra desculpa negligência. O navio deve atender aos perigos, às circunstâncias especiais e ao risco imediato."),
    ColregRule(3, "Definições gerais", "Parte A - Generalidades", "Fixa conceitos como navio de propulsão mecânica, vela, pesca, desgovernado, capacidade de manobra reduzida, condicionado pelo calado, em marcha e visibilidade reduzida."),
    ColregRule(4, "Aplicação das regras de governo", "Parte B - Governo e navegação", "As regras da secção aplicam-se em qualquer condição de visibilidade."),
    ColregRule(5, "Vigia", "Parte B - Governo e navegação", "Manter vigia visual e auditiva permanente, usando todos os meios adequados às circunstâncias."),
    ColregRule(6, "Velocidade de segurança", "Parte B - Governo e navegação", "Navegar a velocidade que permita agir eficazmente para evitar abalroamento e parar em distância adequada."),
    ColregRule(7, "Risco de abalroamento", "Parte B - Governo e navegação", "Usar todos os meios disponíveis para avaliar risco. Na dúvida, assumir que o risco existe."),
    ColregRule(8, "Manobra para evitar abalroamento", "Parte B - Governo e navegação", "A manobra deve ser feita cedo, de forma clara, ampla e controlada até o outro navio estar passado e safo."),
    ColregRule(9, "Canais estreitos", "Parte B - Governo e navegação", "Navegar tão perto quanto possível do limite exterior de estibordo, sem dificultar navios que só possam navegar em segurança no canal."),
    ColregRule(10, "Esquemas de separação de tráfego", "Parte B - Governo e navegação", "Usar o corredor correto, na direção geral do tráfego, evitando linhas/zonas de separação e cruzando perpendicularmente quando necessário."),
    ColregRule(11, "Aplicação a navios à vista", "Parte B - Navios à vista", "As regras seguintes aplicam-se a navios que estão à vista uns dos outros."),
    ColregRule(12, "Navios à vela", "Parte B - Navios à vista", "Define prioridades entre veleiros conforme bordo de amuras e posição a barlavento/sotavento."),
    ColregRule(13, "Navio que alcança", "Parte B - Navios à vista", "Quem alcança deve sempre afastar-se do caminho do navio alcançado. Na dúvida, assume que está a alcançar."),
    ColregRule(14, "Roda a roda", "Parte B - Navios à vista", "Dois navios de propulsão mecânica em roda a roda, com risco, guinam ambos para estibordo para passar bombordo com bombordo."),
    ColregRule(15, "Rumos cruzados", "Parte B - Navios à vista", "O navio que vê o outro por estibordo deve afastar-se e, se possível, evitar cortar-lhe a proa."),
    ColregRule(16, "Navio sem prioridade", "Parte B - Navios à vista", "O navio que deve afastar-se deve manobrar cedo e francamente para se manter suficientemente afastado."),
    ColregRule(17, "Navio com prioridade", "Parte B - Navios à vista", "Mantém rumo e velocidade no início, mas pode/deve agir se o outro não manobrar ou se o risco já não puder ser evitado só pelo outro."),
    ColregRule(18, "Responsabilidades recíprocas", "Parte B - Navios à vista", "Ordena responsabilidades entre propulsão mecânica, vela, pesca, desgovernados, capacidade de manobra reduzida e condicionados pelo calado."),
    ColregRule(
        19,
        "Conduta em visibilidade reduzida",
        "Parte B - Visibilidade reduzida",
        "Aplica-se quando os navios não estão à vista e navegam perto ou dentro de visibilidade reduzida.",
        operational=(
            "Velocidade de segurança adaptada à visibilidade.",
            "Máquinas prontas a manobrar imediatamente.",
            "Radar e todos os meios disponíveis para avaliar aproximação excessiva/risco.",
            "Se ouvir sinal para vante ou não evitar aproximação excessiva, reduzir ao mínimo para governar, anular seguimento se necessário e navegar com extrema precaução.",
        ),
        signals=("📣 Ver Regra 35 para sinais sonoros de nevoeiro.",),
    ),
    ColregRule(20, "Aplicação de luzes e marcas", "Parte C - Luzes e marcas", "Define quando mostrar luzes e marcas. As luzes são obrigatórias do pôr ao nascer do sol e em visibilidade reduzida.", signals=("🌙 Luzes: usar de noite e em visibilidade reduzida.", "☀️ Marcas: usar de dia quando aplicável.")),
    ColregRule(21, "Definições de luzes", "Parte C - Luzes e marcas", "Define farol de mastro, luzes de borda, luz de popa, luz de reboque, luz visível em todo o horizonte e luz intermitente.", signals=("⚪ farol de mastro/popas/reboque", "🔴 luz de BB", "🟢 luz de EB", "✨ luz intermitente")),
    ColregRule(22, "Alcance das luzes", "Parte C - Luzes e marcas", "Define alcances mínimos das luzes conforme o comprimento do navio.", signals=("🔭 Alcance depende do comprimento do navio e do tipo de luz.",)),
    ColregRule(
        23,
        "Navios de propulsão mecânica a navegar",
        "Parte C - Luzes e marcas",
        "Navio de máquina em marcha mostra farol de mastro, luzes de borda e luz de popa; navios maiores podem exigir segundo farol de mastro.",
        signals=("⚪ farol de mastro a vante", "⚪ segundo farol de mastro se aplicável", "🔴 BB + 🟢 EB", "⚪ luz de popa"),
    ),
    ColregRule(
        24,
        "Reboque e empurrar",
        "Parte C - Luzes e marcas",
        "Define luzes/marcas para navios a rebocar, empurrar ou de braço dado.",
        signals=("⚪⚪ faróis de mastro verticais no rebocador", "⚪⚪⚪ se o comprimento do reboque exceder 200 m", "🟡 luz de reboque acima da luz de popa", "🔴/🟢 luzes de borda", "◆ marca biconica quando o reboque excede 200 m"),
    ),
    ColregRule(
        25,
        "Navios à vela e a remos",
        "Parte C - Luzes e marcas",
        "Define luzes para navios à vela em marcha e embarcações a remos.",
        signals=("🔴 BB + 🟢 EB + ⚪ popa", "🔴 sobre 🟢 opcional no tope para vela", "🔻 cone com vértice para baixo quando navega à vela mas também usa máquina"),
    ),
    ColregRule(
        26,
        "Navios em faina de pesca",
        "Parte C - Luzes e marcas",
        "Define luzes/marcas para pesca de arrasto e outras artes de pesca.",
        signals=("🟢 sobre ⚪ para arrasto", "🔴 sobre ⚪ para outras artes de pesca", "⚪ luz na direção da arte se a arte se estende mais de 150 m", "🔺🔻 marca de dois cones unidos pelos vértices"),
    ),
    ColregRule(
        27,
        "Desgovernado ou capacidade de manobra reduzida",
        "Parte C - Luzes e marcas",
        "Define sinais para navio desgovernado e navio com capacidade de manobra reduzida.",
        signals=("🔴🔴 navio desgovernado", "⚫⚫ duas bolas de dia para desgovernado", "🔴⚪🔴 capacidade de manobra reduzida", "⚫◆⚫ bola-losango-bola de dia"),
    ),
    ColregRule(
        28,
        "Navio condicionado pelo calado",
        "Parte C - Luzes e marcas",
        "Navio condicionado pelo calado pode mostrar sinais próprios, além das luzes normais de máquina.",
        signals=("🔴🔴🔴 três luzes vermelhas verticais", "⬛ cilindro de dia"),
    ),
    ColregRule(
        29,
        "Barcos de pilotos",
        "Parte C - Luzes e marcas",
        "Define sinais próprios de embarcações em serviço de pilotagem.",
        signals=("⚪ sobre 🔴 no tope", "🔴/🟢 e ⚪ popa quando em marcha", "⚓ luzes/marcas de fundeado quando fundeado"),
    ),
    ColregRule(
        30,
        "Fundeados e encalhados",
        "Parte C - Luzes e marcas",
        "Define luzes e marcas de navios fundeados ou encalhados.",
        signals=("⚓ fundeado: ⚪ luz todo o horizonte à proa; se >=50 m, outra ⚪ à popa mais baixa", "⚫ bola de dia para fundeado", "Encalhado: luzes de fundeado + 🔴🔴 e ⚫⚫⚫ de dia"),
    ),
    ColregRule(31, "Hidroaviões", "Parte C - Luzes e marcas", "Hidroaviões devem cumprir as luzes e marcas tanto quanto possível; se não for possível, mostram luzes/marcas tão semelhantes quanto praticável."),
    ColregRule(32, "Definições de sinais sonoros", "Parte D - Sinais sonoros e luminosos", "Define apito, som curto e som prolongado.", signals=("📣 curto: cerca de 1 s", "📣 prolongado: 4 a 6 s")),
    ColregRule(33, "Equipamento para sinais sonoros", "Parte D - Sinais sonoros e luminosos", "Define equipamento de sinais sonoros exigido conforme o comprimento do navio.", signals=("📣 apito", "🔔 sino", "🥁 tantã/gongo quando aplicável")),
    ColregRule(34, "Sinais de manobra e aviso", "Parte D - Sinais sonoros e luminosos", "Define sinais entre navios à vista para manobra, ultrapassagem, dúvida/perigo e curvas de canal.", signals=("📣 1 curto: guinar para estibordo", "📣 2 curtos: guinar para bombordo", "📣 3 curtos: máquina a ré", "📣 5 curtos: dúvida/perigo", "💡 relâmpagos luminosos equivalentes podem complementar")),
    ColregRule(35, "Sinais sonoros em visibilidade reduzida", "Parte D - Sinais sonoros e luminosos", "Define sinais de nevoeiro conforme o estado do navio.", signals=("📣 em marcha com seguimento: 1 prolongado até 2/2 min", "📣 pairando/sem seguimento: 2 prolongados até 2/2 min", "📣 vela, pesca, desgovernado, RAM, calado ou rebocador: 1 prolongado + 2 curtos", "📣 rebocado: 1 prolongado + 3 curtos", "🔔 fundeado/encalhado: sino/gongo conforme comprimento")),
    ColregRule(36, "Sinais para chamar a atenção", "Parte D - Sinais sonoros e luminosos", "Permite sinais luminosos ou sonoros para chamar atenção, desde que não confundam outros sinais.", signals=("💡/📣 chamar atenção sem criar confusão com sinais regulamentares")),
    ColregRule(37, "Sinais de perigo", "Parte D - Sinais sonoros e luminosos", "Usar sinais de perigo quando há perigo ou necessidade de auxílio.", signals=("🆘 sinais de perigo reconhecidos", "📣/💡/📻 conforme meios disponíveis")),
    ColregRule(38, "Isenções", "Parte E - Isenções", "Define isenções aplicáveis a certos navios existentes quanto a requisitos técnicos de luzes, marcas ou equipamento."),
)


COLREG_BY_NUMBER = {rule.number: rule for rule in COLREG_RULES}


def parse_colreg_rule_number(argument: str) -> int | None:
    match = re.search(r"\b(?:regra\s*)?([1-9]|[1-2][0-9]|3[0-8])\b", str(argument or ""), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def format_colreg_catalog() -> str:
    lines = [
        "RIEAM/COLREG - regras por número e título:",
    ]
    current_part = ""
    for rule in COLREG_RULES:
        if rule.part != current_part:
            current_part = rule.part
            lines.append("")
            lines.append(current_part)
        lines.append(f"{rule.number:02d}. {rule.title}")
    lines.extend(
        [
            "",
            "Usa `/colreg 19` ou `/regra-colreg 35` para ver uma regra específica.",
        ]
    )
    return "\n".join(lines)


def format_colreg_rule(number: int | None) -> str:
    if number is None:
        return format_colreg_catalog()
    rule = COLREG_BY_NUMBER.get(number)
    if not rule:
        return f"Não encontrei a regra COLREG {number}.\n\n{format_colreg_catalog()}"

    lines = [
        f"RIEAM/COLREG Regra {rule.number} - {rule.title}",
        rule.part,
        "",
        rule.summary,
    ]
    if rule.operational:
        lines.append("")
        lines.append("Pontos operacionais:")
        lines.extend(f"- {item}" for item in rule.operational)
    if rule.signals:
        lines.append("")
        lines.append("Luzes, marcas e sinais:")
        lines.extend(f"- {item}" for item in rule.signals)
        lines.append("Legenda visual: ⚪ luz branca · 🔴 vermelha/BB · 🟢 verde/EB · 🟡 amarela · ⚫ balão · ◆ losango/bicone · ⬛ cilindro · 📣 som · 💡 sinal luminoso.")
    lines.extend(
        [
            "",
            "Nota: resumo operacional. Em caso real, aplicar o texto oficial, regras locais, VTS e instruções de pilotagem.",
        ]
    )
    return "\n".join(lines)
