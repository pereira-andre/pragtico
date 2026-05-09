from __future__ import annotations

import re
from dataclasses import dataclass


COLREG_SOURCE_DOCUMENT = "RIEAM_COLREG_Regras_Estrada.txt"
COLREG_INTERPRETATION_RE = re.compile(
    r"\b(colreg|rieam|abalroamento|roda\s+a\s+roda|proa\s+com\s+proa|rumos?\s+cruzad|"
    r"rumo\s+de\s+colis[aã]o|rota\s+de\s+colis[aã]o|risco\s+de\s+(?:colis[aã]o|abalroamento)|"
    r"navio\s+(?:pelo|por)\s+(?:meu\s+)?(?:bombordo|estibordo)|apresenta-se\s+um\s+navio|"
    r"alcanc\w+|ultrapass\w+|canal\s+estreito|esquema\s+de\s+separacao|separação\s+de\s+tr[aá]fego|"
    r"sinais?\s+(?:sonor|de\s+manobra|de\s+nevoeiro|de\s+perigo)|sons?\s+(?:curtos?|prolongados?)|"
    r"apito|sino|tanta|tant[aã]|nevoeiro|visibilidade\s+reduzida|barco\s+de\s+piloto|"
    r"pilotagem|fundeado|encalhado|desgovernado|capacidade\s+de\s+manobra\s+reduzida|"
    r"condicionado\s+pelo\s+calado|dragag\w+|mergulhador\w+|reboqu\w+|empurr\w+|"
    r"pesca|arrasto|vela)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ColregRule:
    number: int
    title: str
    part: str
    summary: str
    operational: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()


COLREG_RULES: tuple[ColregRule, ...] = (
    ColregRule(
        1,
        "Campo de aplicação",
        "Parte A - Generalidades",
        "Define onde o RIEAM/COLREG se aplica e admite regras locais complementares em portos, rios, radas e vias interiores.",
        operational=(
            "Aplica-se no alto mar e em águas comunicantes com o mar praticáveis por navegação marítima.",
            "Regras locais de porto, VTS, Capitania e instruções de pilotagem podem complementar; devem ser compatíveis quanto possível.",
            "Luzes/sinais especiais de navios de guerra, comboios, pesca em grupo ou navios de construção especial não devem confundir-se com sinais COLREG.",
        ),
    ),
    ColregRule(
        2,
        "Responsabilidade",
        "Parte A - Generalidades",
        "Nenhuma regra desculpa negligência. O navio deve atender aos perigos, às circunstâncias especiais e ao risco imediato.",
        operational=(
            "Cumprir a regra literal não chega se a prática marinheira exigir precaução adicional.",
            "Circunstâncias especiais e limitações dos navios podem exigir afastar-se da regra estrita para evitar perigo imediato.",
        ),
    ),
    ColregRule(
        3,
        "Definições gerais",
        "Parte A - Generalidades",
        "Fixa conceitos como navio de propulsão mecânica, vela, pesca, desgovernado, capacidade de manobra reduzida, condicionado pelo calado, em marcha e visibilidade reduzida.",
        operational=(
            "Pesca só conta como tal quando a arte reduz a capacidade de manobra; corripo/linhas que não limitam manobra não entram nesta definição.",
            "Capacidade de manobra reduzida inclui dragagem, trabalhos submarinos, manutenção de marcas/cabos, reabastecimento/transbordo, operações aéreas, caça-minas e certos reboques restritivos.",
            "Em marcha significa não fundeado, não amarrado a terra e não encalhado.",
        ),
    ),
    ColregRule(4, "Aplicação das regras de governo", "Parte B - Governo e navegação", "As regras da secção aplicam-se em qualquer condição de visibilidade."),
    ColregRule(5, "Vigia", "Parte B - Governo e navegação", "Manter vigia visual e auditiva permanente, usando todos os meios adequados às circunstâncias.", operational=("Radar, AIS e VHF ajudam, mas não substituem vigia visual/auditiva.",)),
    ColregRule(
        6,
        "Velocidade de segurança",
        "Parte B - Governo e navegação",
        "Navegar a velocidade que permita agir eficazmente para evitar abalroamento e parar em distância adequada.",
        operational=(
            "Considerar visibilidade, densidade de tráfego, manobrabilidade, distância de paragem, giração, luzes de fundo, vento, mar, corrente, perigos próximos e calado/profundidade.",
            "Com radar, considerar eficiência, escala, interferência do mar/meteo, pequenos ecos não detetados e plotting/observação sistemática.",
        ),
    ),
    ColregRule(7, "Risco de abalroamento", "Parte B - Governo e navegação", "Usar todos os meios disponíveis para avaliar risco. Na dúvida, assumir que o risco existe.", operational=("Marcação constante com distância a diminuir indica risco; com navio grande, reboque ou curta distância pode haver risco mesmo com alguma variação de marcação.",)),
    ColregRule(8, "Manobra para evitar abalroamento", "Parte B - Governo e navegação", "A manobra deve ser feita cedo, de forma clara, ampla e controlada até o outro navio estar passado e safo.", operational=("Evitar sucessão de pequenas alterações; se houver espaço, alteração de rumo ampla e cedo pode ser mais eficaz.", "Se necessário, reduzir velocidade, parar ou inverter propulsão para ganhar tempo e evitar aproximação excessiva.")),
    ColregRule(
        9,
        "Canais estreitos",
        "Parte B - Governo e navegação",
        "Navegar tão perto quanto possível do limite exterior de estibordo, sem dificultar navios que só possam navegar em segurança no canal.",
        operational=(
            "Navios <20 m, navios à vela e navios em pesca não devem dificultar quem só pode navegar em segurança no canal.",
            "Não atravessar se isso dificultar navio que só possa navegar ali; se houver dúvida, pode ser usado o sinal de perigo/dúvida da Regra 34.",
            "Ultrapassagem que exija ação do navio alcançado precisa de sinais da Regra 34 e acordo antes de executar.",
            "Aproximar curvas/zonas encobertas com prudência, vigilância e som prolongado; evitar fundear em canal estreito se possível.",
        ),
    ),
    ColregRule(
        10,
        "Esquemas de separação de tráfego",
        "Parte B - Governo e navegação",
        "Usar o corredor correto, na direção geral do tráfego, evitando linhas/zonas de separação e cruzando perpendicularmente quando necessário.",
        operational=(
            "Entrar/sair normalmente pelos extremos; se lateralmente, fazer ângulo pequeno com a direção geral do tráfego.",
            "Evitar cruzar; se tiver de cruzar, fazê-lo tão perpendicular quanto possível.",
            "Não fundear dentro do esquema ou perto dos extremos salvo necessidade; navegar com cuidado especial nos extremos.",
            "Pesca, vela e navios <20 m não devem dificultar navios de propulsão mecânica no corredor.",
        ),
    ),
    ColregRule(11, "Aplicação a navios à vista", "Parte B - Navios à vista", "As regras seguintes aplicam-se a navios que estão à vista uns dos outros."),
    ColregRule(12, "Navios à vela", "Parte B - Navios à vista", "Define prioridades entre veleiros conforme bordo de amuras e posição a barlavento/sotavento.", operational=("Vento por bordos diferentes: quem recebe por bombordo afasta-se.", "Mesmo bordo: o navio a barlavento afasta-se do que está a sotavento.", "Se recebe vento por bombordo e não consegue determinar o bordo do outro a barlavento, afasta-se.")),
    ColregRule(13, "Navio que alcança", "Parte B - Navios à vista", "Quem alcança deve sempre afastar-se do caminho do navio alcançado. Na dúvida, assume que está a alcançar."),
    ColregRule(14, "Roda a roda", "Parte B - Navios à vista", "Dois navios de propulsão mecânica em roda a roda, com risco, guinam ambos para estibordo para passar bombordo com bombordo."),
    ColregRule(15, "Rumos cruzados", "Parte B - Navios à vista", "O navio que vê o outro por estibordo deve afastar-se e, se possível, evitar cortar-lhe a proa."),
    ColregRule(16, "Navio sem prioridade", "Parte B - Navios à vista", "O navio que deve afastar-se deve manobrar cedo e francamente para se manter suficientemente afastado."),
    ColregRule(17, "Navio com prioridade", "Parte B - Navios à vista", "Mantém rumo e velocidade no início, mas pode/deve agir se o outro não manobrar ou se o risco já não puder ser evitado só pelo outro."),
    ColregRule(18, "Responsabilidades recíprocas", "Parte B - Navios à vista", "Ordena responsabilidades entre propulsão mecânica, vela, pesca, desgovernados, capacidade de manobra reduzida e condicionados pelo calado.", operational=("Salvo Regras 9, 10 e 13: máquina afasta-se de desgovernado, RAM, pesca e vela.", "Vela afasta-se de desgovernado, RAM e pesca; pesca afasta-se, tanto quanto possível, de desgovernado e RAM.", "Qualquer navio, exceto desgovernado/RAM, deve evitar dificultar navio condicionado pelo calado que mostre Regra 28.", "Hidroavião amarado mantém-se afastado de navios e evita dificultar navegação.")),
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
    ColregRule(22, "Alcance das luzes", "Parte C - Luzes e marcas", "Define alcances mínimos das luzes conforme o comprimento do navio.", signals=("🔭 >=50 m: mastro 6 M; borda/popa/reboque/todo-horizonte 3 M", "🔭 12-50 m: mastro 5 M (3 M se <20 m); restantes 2 M", "🔭 <12 m: mastro 2 M; borda 1 M; popa/reboque/todo-horizonte 2 M")),
    ColregRule(
        23,
        "Navios de propulsão mecânica a navegar",
        "Parte C - Luzes e marcas",
        "Navio de máquina em marcha mostra farol de mastro, luzes de borda e luz de popa; navios maiores podem exigir segundo farol de mastro.",
        operational=("Navios <50 m não são obrigados ao segundo farol de mastro, mas podem mostrá-lo.", "Navio <7 m e velocidade máxima <=7 nós pode mostrar luz branca todo-horizonte e, se possível, luzes de borda.", "Aerobarco sem casco mergulhado acrescenta luz amarela de relâmpagos todo-horizonte."),
        signals=("⚪ farol de mastro a vante", "⚪ segundo farol de mastro se aplicável", "🔴 BB + 🟢 EB", "⚪ luz de popa", "🟡 relâmpagos para aerobarco"),
    ),
    ColregRule(
        24,
        "Reboque e empurrar",
        "Parte C - Luzes e marcas",
        "Define luzes/marcas para navios a rebocar, empurrar ou de braço dado.",
        operational=("Comprimento do reboque mede-se da popa do rebocador ao extremo posterior do último rebocado.", "Empurrador e empurrado ligados rigidamente formam unidade composta e mostram luzes de navio de máquina.", "Se o rebocado não conseguir mostrar luzes, iluminar ou indicar a sua presença por todos os meios possíveis."),
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
        operational=("Só navio em faina de pesca deve mostrar estes sinais; navio que não está em faina mostra sinais normais do seu comprimento.", "Quando tem seguimento, acrescenta luzes de borda e popa.", "Se arte diferente de arrasto se estender mais de 150 m, indicar direção da arte com luz branca todo-horizonte ou cone com vértice para cima."),
        signals=("🟢 sobre ⚪ para arrasto", "🔴 sobre ⚪ para outras artes de pesca", "⚪ luz/cone na direção da arte se a arte se estende mais de 150 m", "🔺🔻 marca de dois cones unidos pelos vértices"),
    ),
    ColregRule(
        27,
        "Desgovernado ou capacidade de manobra reduzida",
        "Parte C - Luzes e marcas",
        "Define sinais para navio desgovernado e navio com capacidade de manobra reduzida.",
        operational=("Se desgovernado/RAM tiver seguimento, acrescenta luzes de borda e popa.", "Dragagem/trabalhos submarinos: vermelho-vermelho ou duas bolas no bordo obstruído; verde-verde ou dois bicones no bordo livre.", "Mergulhadores: réplica rígida da bandeira A quando não puder mostrar balões.", "Caça-minas: três verdes ou três bolas; perigoso aproximar a menos de 1000 m pela popa ou 500 m pelos bordos.", "Sinais desta regra não são sinais de perigo; perigo está no Anexo IV."),
        signals=("🔴🔴 navio desgovernado", "⚫⚫ duas bolas de dia para desgovernado", "🔴⚪🔴 capacidade de manobra reduzida", "⚫◆⚫ bola-losango-bola de dia", "🔴🔴 bordo obstruído · 🟢🟢 bordo livre em dragagem"),
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
        operational=("Só usa sinais de piloto quando está em serviço de pilotagem; fora desse serviço mostra luzes/marcas do seu comprimento.",),
        signals=("⚪ sobre 🔴 no tope", "🔴/🟢 e ⚪ popa quando em marcha", "⚓ luzes/marcas de fundeado quando fundeado"),
    ),
    ColregRule(
        30,
        "Fundeados e encalhados",
        "Parte C - Luzes e marcas",
        "Define luzes e marcas de navios fundeados ou encalhados.",
        operational=("Navio fundeado >=100 m deve usar luzes de trabalho/equivalentes para iluminação geral.", "Navio <7 m não é obrigado aos sinais de fundeado/encalhado, salvo em canal estreito, via de acesso, fundeadouro ou zona frequentada."),
        signals=("⚓ fundeado: ⚪ luz todo o horizonte à proa; se >=50 m, outra ⚪ à popa mais baixa", "⚫ bola de dia para fundeado", "Encalhado: luzes de fundeado + 🔴🔴 e ⚫⚫⚫ de dia"),
    ),
    ColregRule(31, "Hidroaviões", "Parte C - Luzes e marcas", "Hidroaviões devem cumprir as luzes e marcas tanto quanto possível; se não for possível, mostram luzes/marcas tão semelhantes quanto praticável."),
    ColregRule(32, "Definições de sinais sonoros", "Parte D - Sinais sonoros e luminosos", "Define apito, som curto e som prolongado.", signals=("📣 curto: cerca de 1 s", "📣 prolongado: 4 a 6 s")),
    ColregRule(33, "Equipamento para sinais sonoros", "Parte D - Sinais sonoros e luminosos", "Define equipamento de sinais sonoros exigido conforme o comprimento do navio.", operational=(">=12 m: apito e sino; >=100 m: também tantã/gongo.", "<12 m não é obrigado a esse equipamento, mas deve poder produzir sinal sonoro eficaz."), signals=("📣 apito", "🔔 sino", "🥁 tantã/gongo quando aplicável")),
    ColregRule(34, "Sinais de manobra e aviso", "Parte D - Sinais sonoros e luminosos", "Define sinais entre navios à vista para manobra, ultrapassagem, dúvida/perigo e curvas de canal.", operational=("Sinais de manobra aplicam-se quando os navios estão à vista.", "Ultrapassagem em canal estreito: 2 prolongados + 1 curto = tenciono ultrapassar pelo teu estibordo; 2 prolongados + 2 curtos = pelo teu bombordo.", "Acordo do navio alcançado: 1 prolongado + 1 curto + 1 prolongado + 1 curto.", "Curva/obstáculo em canal: 1 prolongado; quem ouve do outro lado responde 1 prolongado."), signals=("📣 1 curto: guinar para estibordo", "📣 2 curtos: guinar para bombordo", "📣 3 curtos: máquina a ré", "📣 5 curtos: dúvida/perigo", "💡 relâmpagos luminosos equivalentes podem complementar")),
    ColregRule(35, "Sinais sonoros em visibilidade reduzida", "Parte D - Sinais sonoros e luminosos", "Define sinais de nevoeiro conforme o estado do navio.", operational=("Sinais aplicam-se de dia e de noite, dentro ou perto de visibilidade reduzida.", "Unidade composta empurrador+empurrado rígida emite como um navio de propulsão mecânica.", "Barco de pilotos em serviço pode acrescentar 4 sons curtos como identificação."), signals=("📣 em marcha com seguimento: 1 prolongado até 2/2 min", "📣 pairando/sem seguimento: 2 prolongados até 2/2 min", "📣 vela, pesca, desgovernado, RAM, calado ou rebocador: 1 prolongado + 2 curtos", "📣 rebocado: 1 prolongado + 3 curtos", "🔔 fundeado/encalhado: sino/gongo conforme comprimento", "🔔 fundeado: sino rápido 5 s a cada 1 min; se >=100 m, sino à proa + tantã à ré", "🔔 encalhado: sinal de fundeado + 3 toques de sino antes e depois")),
    ColregRule(36, "Sinais para chamar a atenção", "Parte D - Sinais sonoros e luminosos", "Permite sinais luminosos ou sonoros para chamar atenção, desde que não confundam outros sinais.", signals=("💡/📣 chamar atenção sem criar confusão com sinais regulamentares")),
    ColregRule(37, "Sinais de perigo", "Parte D - Sinais sonoros e luminosos", "Usar sinais de perigo quando há perigo ou necessidade de auxílio.", operational=("Não usar sinais de perigo salvo perigo real ou necessidade de assistência; também é proibido usar sinais confundíveis.",), signals=("🆘 SOS em Morse", "📻 Mayday", "🚩 NC do Código Internacional de Sinais", "🔴 foguete/facho vermelho", "🟧 fumo laranja", "🙆 braços lentos para cima e para baixo")),
    ColregRule(38, "Isenções", "Parte E - Isenções", "Define isenções aplicáveis a certos navios existentes quanto a requisitos técnicos de luzes, marcas ou equipamento."),
)


COLREG_BY_NUMBER = {rule.number: rule for rule in COLREG_RULES}


def _normalize_colreg_text(value: str | None) -> str:
    text = str(value or "").lower()
    replacements = str.maketrans("áàâãéêíóôõúç", "aaaaeeiooouc")
    text = text.translate(replacements)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _colreg_source(snippet: str) -> dict:
    return {
        "document": COLREG_SOURCE_DOCUMENT,
        "source_id": "COLREG_INTERPRETATION",
        "chunk_id": 0,
        "score": 1.0,
        "retrieval_mode": "colreg_interpretation",
        "snippet": snippet,
        "text": snippet,
    }


def _colreg_payload(answer: str, snippet: str | None = None) -> dict:
    return {
        "answer": answer,
        "sources": [_colreg_source(snippet or answer)],
        "answer_origin": "colreg_interpretation",
    }


def answer_colreg_interpretation_direct(question: str) -> dict | None:
    clean = _normalize_colreg_text(question)
    if not clean or not COLREG_INTERPRETATION_RE.search(question or ""):
        return None

    explicit_rule = parse_colreg_rule_number(question)
    if explicit_rule is not None and re.search(r"\b(colreg|rieam|regra\s*)\b", question or "", flags=re.IGNORECASE):
        return _colreg_payload(format_colreg_rule(explicit_rule))

    own_port_crossing = (
        "bombordo" in clean
        and (
            "rumo de colisao" in clean
            or "rota de colisao" in clean
            or "risco de colisao" in clean
            or "risco de abalroamento" in clean
            or "apresenta se" in clean
            or "apresenta um navio" in clean
        )
        and any(token in clean for token in ("navio", "barco", "embarcacao"))
    )
    if own_port_crossing:
        answer = (
            "Pelo RIEAM/COLREG, assumindo dois navios de propulsão mecânica à vista em rumos cruzados e com risco de abalroamento:\n"
            "- se o outro navio se apresenta pelo teu bombordo, tu és o navio com prioridade/stand-on: mantém rumo e velocidade inicialmente (Regra 17);\n"
            "- o outro navio tem-te por estibordo, portanto é o navio sem prioridade/give-way: deve manobrar cedo e francamente para se manter afastado, evitando cortar-te a proa (Regras 15 e 16);\n"
            "- no Canal Norte/canal estreito acresce a Regra 9: mantém-te tão perto quanto seja seguro do limite exterior de estibordo do canal; um navio que cruza não deve dificultar quem só consegue navegar em segurança no canal;\n"
            "- acompanha marcação/CPA e intenção do outro navio. Se houver dúvida/perigo sobre a manobra dele, usa pelo menos 5 sons curtos e coordena com VTS/Setúbal Port Control se necessário (Regra 34);\n"
            "- se ficar evidente que o outro não está a manobrar, podes agir para evitar o abalroamento; se já não puder ser evitado só pela ação do outro, deves agir pelo melhor meio disponível (Regra 17);\n"
            "- nessa ação, se as circunstâncias permitirem, evita guinar para bombordo para um navio que está pelo teu bombordo; considera reduzir, parar/inverter máquina ou guinar para estibordo se houver água, espaço e segurança."
        )
        return _colreg_payload(answer)

    own_starboard_crossing = (
        "estibordo" in clean
        and (
            "rumo de colisao" in clean
            or "rota de colisao" in clean
            or "risco de colisao" in clean
            or "risco de abalroamento" in clean
            or "apresenta se" in clean
        )
        and any(token in clean for token in ("navio", "barco", "embarcacao"))
    )
    if own_starboard_crossing:
        answer = (
            "Pelo RIEAM/COLREG, assumindo dois navios de propulsão mecânica à vista em rumos cruzados e com risco de abalroamento:\n"
            "- se o outro navio se apresenta pelo teu estibordo, tu és o navio sem prioridade/give-way (Regra 15);\n"
            "- deves manobrar cedo e francamente para te manteres bem afastado, evitando cortar-lhe a proa (Regra 16);\n"
            "- a manobra deve ser clara e suficientemente ampla, evitando pequenas alterações sucessivas (Regra 8);\n"
            "- em canal estreito, mantém-te tão perto quanto seguro do limite exterior de estibordo e não dificultes navio que só possa navegar em segurança no canal (Regra 9);\n"
            "- se houver dúvida/perigo sobre intenções, usa pelo menos 5 sons curtos e coordena com VTS/Setúbal Port Control se necessário (Regra 34)."
        )
        return _colreg_payload(answer)

    if "canal estreito" in clean and any(token in clean for token in ("ultrapass", "alcanc")):
        answer = (
            "Pelo RIEAM/COLREG, numa ultrapassagem em canal estreito aplica-se a Regra 9 em conjunto com a Regra 34:\n"
            "- o navio que pretende ultrapassar só deve fazê-lo se a manobra puder ser executada com segurança;\n"
            "- se precisar que o navio alcançado manobre, deve pedir acordo por sinal sonoro;\n"
            "- 2 sons prolongados + 1 curto: tenciono ultrapassar pelo estibordo do navio alcançado;\n"
            "- 2 sons prolongados + 2 curtos: tenciono ultrapassar pelo bombordo do navio alcançado;\n"
            "- se o navio alcançado concordar: 1 prolongado + 1 curto + 1 prolongado + 1 curto;\n"
            "- se houver dúvida: pelo menos 5 sons curtos."
        )
        return _colreg_payload(answer)

    if "canal estreito" in clean:
        answer = (
            "Regra 9 - Canal estreito: navegar tão perto quanto possível do limite exterior de estibordo, "
            "sem dificultar navios que só possam navegar em segurança nesse canal. Navios pequenos, à vela "
            "ou em pesca não devem dificultar a passagem. Evitar atravessar se isso dificultar quem segue no canal "
            "e evitar fundear no canal se as circunstâncias permitirem."
        )
        return _colreg_payload(answer)

    if "esquema de separacao" in clean or "separacao de trafego" in clean:
        answer = (
            "Regra 10 - Esquema de separação de tráfego: seguir no corredor correto e na direção geral do tráfego; "
            "manter-se afastado da linha/zona de separação; entrar e sair preferencialmente pelos extremos; "
            "se tiver de cruzar, cruzar tão perpendicular quanto possível; evitar fundear no esquema ou junto aos extremos."
        )
        return _colreg_payload(answer)

    if any(token in clean for token in ("roda a roda", "proa com proa", "quase roda a roda")):
        answer = (
            "Regra 14 - Roda a roda: se dois navios de propulsão mecânica se aproximam de proa ou quase de proa "
            "com risco de abalroamento, ambos devem guinar para estibordo para passar bombordo com bombordo. "
            "Se houver dúvida se é roda a roda, tratar como roda a roda."
        )
        return _colreg_payload(answer)

    if "rumos cruzad" in clean or ("estibordo" in clean and "cortar" in clean and "proa" in clean):
        answer = (
            "Regra 15 - Rumos cruzados: entre dois navios de propulsão mecânica com risco de abalroamento, "
            "o navio que vê o outro por estibordo deve afastar-se e, se as circunstâncias permitirem, evitar cortar-lhe a proa. "
            "A manobra deve ser cedo e franca, como exigem as Regras 8 e 16."
        )
        return _colreg_payload(answer)

    if any(token in clean for token in ("alcanca", "alcancar", "alcancante", "ultrapassar", "ultrapassagem")):
        answer = (
            "Regra 13 - Navio que alcança: quem alcança deve sempre manter-se afastado do caminho do navio alcançado. "
            "Se houver dúvida se está a alcançar, assume que está a alcançar. A alteração posterior da marcação não retira essa obrigação "
            "até estar definitivamente passado e safo."
        )
        return _colreg_payload(answer)

    if "prioridade" in clean or "stand on" in clean or "manter rumo" in clean:
        answer = (
            "Regra 17 - Navio com prioridade: inicialmente mantém rumo e velocidade. Pode manobrar quando ficar evidente que o navio "
            "sem prioridade não está a agir corretamente; deve manobrar quando o abalroamento já não puder ser evitado só pelo outro. "
            "Em rumos cruzados entre navios de máquina, evitar guinar para bombordo se o outro estiver por bombordo, se possível."
        )
        return _colreg_payload(answer)

    if "5 sons" in clean or "cinco sons" in clean or "duvida" in clean and "sinal" in clean:
        answer = (
            "Regra 34 - Dúvida/perigo: quando dois navios estão à vista e um não compreende as intenções ou duvida que o outro "
            "esteja a manobrar corretamente, deve emitir pelo menos 5 sons curtos de apito. Pode complementar com pelo menos "
            "5 relâmpagos curtos em sucessão rápida."
        )
        return _colreg_payload(answer)

    if "curva" in clean and ("canal" in clean or "obstaculo" in clean):
        answer = (
            "Regra 34 - Curva ou zona encoberta em canal/via de acesso: emitir 1 som prolongado. "
            "Qualquer navio que oiça esse sinal do outro lado da curva ou do obstáculo deve responder com 1 som prolongado."
        )
        return _colreg_payload(answer)

    if "nevoeiro" in clean or "visibilidade reduzida" in clean or "sinal de nevoeiro" in clean:
        answer = (
            "Regras 19 e 35 - Visibilidade reduzida: navegar a velocidade de segurança, máquinas prontas, usar radar e todos os meios disponíveis. "
            "Se ouvir sinal para vante ou não conseguir evitar aproximação excessiva, reduzir ao mínimo para governar, anular seguimento se necessário "
            "e navegar com extrema precaução. Sinais principais: com seguimento, 1 prolongado até 2/2 min; pairando, 2 prolongados; "
            "vela/pesca/desgovernado/RAM/calado/rebocador, 1 prolongado + 2 curtos; rebocado, 1 prolongado + 3 curtos."
        )
        return _colreg_payload(answer)

    if "rebo" in clean and any(token in clean for token in ("luz", "farol", "marca", "balao", "sinal")):
        answer = (
            "Regra 24 - Reboque/empurrar: rebocador a rebocar mostra 2 faróis de mastro verticais; se o comprimento do reboque exceder 200 m, mostra 3. "
            "Mostra também luzes de borda, luz de popa e luz amarela de reboque por cima da luz de popa. Se o reboque exceder 200 m, usa marca bicónica de dia. "
            "Empurrador e empurrado ligados rigidamente contam como unidade composta."
        )
        return _colreg_payload(answer)

    if "arrasto" in clean or ("pesca" in clean and any(token in clean for token in ("luz", "farol", "marca", "sinal"))):
        answer = (
            "Regra 26 - Pesca: arrasto mostra verde sobre branco; outras artes de pesca mostram vermelho sobre branco. "
            "Quando tem seguimento acrescenta luzes de borda e popa. Se a arte se estender mais de 150 m, indica a direção da arte "
            "com luz branca todo-horizonte ou cone com vértice para cima. Navio que não está em faina de pesca não deve mostrar estes sinais."
        )
        return _colreg_payload(answer)

    if "drag" in clean or "mergulh" in clean or "capacidade de manobra reduzida" in clean:
        answer = (
            "Regra 27 - Capacidade de manobra reduzida: sinal geral vermelho-branco-vermelho, ou bola-losango-bola de dia. "
            "Em dragagem/trabalhos submarinos com obstrução: vermelho-vermelho ou duas bolas no bordo obstruído; verde-verde ou dois bicones no bordo por onde se pode passar. "
            "Operações de mergulho podem usar réplica rígida da bandeira A quando não puderem mostrar os balões."
        )
        return _colreg_payload(answer)

    if "desgovernado" in clean:
        answer = (
            "Regra 27 - Navio desgovernado: dois faróis vermelhos todo-horizonte na vertical; de dia, duas bolas. "
            "Se tiver seguimento, acrescenta luzes de borda e popa. Estes sinais não são sinais de perigo; perigo/assistência é Regra 37 e Anexo IV."
        )
        return _colreg_payload(answer)

    if "piloto" in clean or "pilotagem" in clean:
        answer = (
            "Regra 29 - Barco de pilotos em serviço: branco sobre vermelho no topo; quando em marcha acrescenta luzes de borda e popa; "
            "quando fundeado acrescenta sinais de fundeado. Se não estiver em serviço de pilotagem, mostra os sinais normais do seu comprimento."
        )
        return _colreg_payload(answer)

    if "fundeado" in clean or "encalhado" in clean:
        answer = (
            "Regra 30 - Fundeado/encalhado: fundeado mostra luz branca todo-horizonte à proa e, se >=50 m, outra branca à popa mais baixa; de dia, uma bola. "
            "Encalhado mostra sinais de fundeado mais dois vermelhos verticais e três bolas de dia. Navio fundeado >=100 m deve iluminar o navio com luzes de trabalho/equivalentes."
        )
        return _colreg_payload(answer)

    if "perigo" in clean or "mayday" in clean or "sos" in clean:
        answer = (
            "Regra 37 e Anexo IV - Sinais de perigo: usar quando há perigo e necessidade de assistência. Exemplos: SOS em Morse, Mayday, sinal NC, "
            "foguetes/fachos vermelhos, fumo laranja, som contínuo de sinal de nevoeiro, movimentos lentos dos braços e radiobaliza de localização de sinistros. "
            "É proibido usar estes sinais sem perigo real ou usar sinais que possam ser confundidos com eles."
        )
        return _colreg_payload(answer)

    if re.search(r"\b(colreg|rieam)\b", question or "", flags=re.IGNORECASE):
        return _colreg_payload(format_colreg_catalog())

    return None


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
