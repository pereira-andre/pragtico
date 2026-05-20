# Simulação de 50 Conversas do Bot

- Gerado em: 2026-05-20T09:04:45
- Modo: isolated handle_chat_turn simulation with in-memory store and eval cases
- Conversas: 50
- Mensagens por conversa: 5
- Mensagens de utilizador: 250
- Respostas OK: 250
- Falhas: 0
- Fallbacks LLM inesperados: 0
- Exceções: 0
- Tempo total: 1.21 s

## Origens de resposta
- `berth_profile_fact`: 52
- `operational_route_transit`: 49
- `operational_tug_guidance`: 40
- `document_companion_global`: 18
- `document_companion`: 18
- `operational_safety_limit`: 16
- `operational_rule`: 12
- `secil_reponto_rule`: 12
- `operational_tide_scheduling`: 12
- `operational_priority`: 8
- `barra_draft_rule`: 5
- `cost_formula`: 4
- `operational_tide_rule`: 4

## Alertas
- Nenhum alerta detetado.

## Amostra por conversa
### sim-001 - eval-window-01
- U1: Qual é o fundeadouro principal para navios em espera de cais?
  - Bot `document_companion_global`: 📋 A resposta direta: O fundeadouro principal é o Fundeadouro Norte, situado a norte da entrada do porto e delimitado pelos vértices A a H.
- U2: A tabela de rebocadores é obrigatória?
  - Bot `document_companion`: 📋 Na generalidade, não. A tabela é orientativa e a decisão final resulta da avaliação conjunta do Piloto e do Comandante. A exceção são os navios de cargas perigosas com LOA superior a 70 metros, em que os valores indicados são mínimos obrigatórios.
- U3: Qual é o calado máximo na barra do Porto de Setúbal?
  - Bot `barra_draft_rule`: 🌊 Na barra do Porto de Setúbal há duas referências que não devem ser confundidas: - Calado máximo absoluto: 12,0 m. - Calado operacional pela barra: 10,30 m + altura da maré no momento da entrada/saída, limitado ao máximo absoluto de 12,0 m. - Esta referência pressupõe ondulação inferior a 1 m; com ondulação superior, 
- U4: Tenho um navio para fazer regulação de agulhas à noite, o que dizem as regras sobre isso?
  - Bot `document_companion`: 📋 Depende do LOA, da maré e do espaço livre. De noite, a RA não se efetua com navios de LOA superior a 225 metros. Para LOA igual ou inferior a 225 metros, continua a ser necessário não existirem navios fundeados a menos de 0,7 milhas e escolher uma condição de maré admissível: reponto de maré, corrente em marés mortas
- U5: Até onde vai a área de responsabilidade do VTS Portuário de Setúbal?
  - Bot `document_companion_global`: 📋 A resposta direta: Vai do interior do porto até ao arco de 4 milhas centrado na Baliza número 2. A oeste desse arco a responsabilidade passa ao VTS Costeiro.
### sim-002 - eval-window-02
- U1: Qual é a distância da entrada da Barra até ao estaleiro da LISNAVE?
  - Bot `operational_route_transit`: 🧭 Do Pilar 2 / entrada da Barra até aos estaleiros da LISNAVE/Mitrena são cerca de 10,5 milhas náuticas pelo Canal Sul completo. Pelo atalho ou corta-mato, considera cerca de 10,0 milhas náuticas.
- U2: Qual é a sonda do Cais 1 A da LISNAVE?
  - Bot `berth_profile_fact`: 📋 Comprimentos/dimensões críticas da LISNAVE (IT-014_Lisnave.txt): - Cais 1: comprimento operacional total 260 metros; Cais 1 A/W/Oeste/Setúbal tem sonda 7,14 m ao ZH e Cais 1 B/E/Este/Alcácer tem sonda 7,40 m ao ZH. - Faces Pontes-Cais 1, 2 e 3: 1 A, 1 B, 2 A, 2 B, 3 A e 3 B. Nota: o site pode usar slots para o quadro
- U3: Qual é a sonda do Cais 1 W da LISNAVE?
  - Bot `berth_profile_fact`: 📋 Comprimentos/dimensões críticas da LISNAVE (IT-014_Lisnave.txt): - Cais 1: comprimento operacional total 260 metros; Cais 1 A/W/Oeste/Setúbal tem sonda 7,14 m ao ZH e Cais 1 B/E/Este/Alcácer tem sonda 7,40 m ao ZH. - Faces Pontes-Cais 1, 2 e 3: 1 A, 1 B, 2 A, 2 B, 3 A e 3 B. Nota: o site pode usar slots para o quadro
- U4: Na LISNAVE, a face B fica de que lado?
  - Bot `berth_profile_fact`: 📋 Na LISNAVE, a face B corresponde ao lado E/este, referência Alcácer do Sal. A face A corresponde ao lado W/oeste, referência Setúbal.
- U5: Quais são as faces dos Pontes-Cais 1, 2 e 3 da LISNAVE?
  - Bot `berth_profile_fact`: 📋 Comprimentos/dimensões críticas da LISNAVE (IT-014_Lisnave.txt): - Cais 1: comprimento operacional total 260 metros; Cais 1 A/W/Oeste/Setúbal tem sonda 7,14 m ao ZH e Cais 1 B/E/Este/Alcácer tem sonda 7,40 m ao ZH. - Faces Pontes-Cais 1, 2 e 3: 1 A, 1 B, 2 A, 2 B, 3 A e 3 B. Nota: o site pode usar slots para o quadro
### sim-003 - eval-window-03
- U1: Qual é o cais da LISNAVE com maior profundidade disponível?
  - Bot `berth_profile_fact`: 📋 Na LISNAVE, o cais com maior profundidade disponível indicada no IT-014 é o Cais 3 B, com sonda de referência 8,60 m ao ZH a 10 m da face.
- U2: O Piloto Coordenador pode aceitar uma saída com 30 minutos de antecedência?
  - Bot `document_companion_global`: 📋 Em regra, não. O PC não aceita requisições de saída ou serviços no rio com menos de 1 hora de antecedência, salvo com anuência do Piloto que vai executar o serviço e sem alteração da escala.
- U3: Quando dois navios pretendem o mesmo cais, qual tem prioridade?
  - Bot `operational_priority`: 📋 Quando dois navios disputam o mesmo cais/destino de acostagem, aplica-se primeiro a regra P-13: tem prioridade o primeiro navio a cruzar o arco de 8 milhas náuticas centrado na Baliza número 2 da Barra, salvo impedimento técnico ou decisão operacional justificada. - Esta é a referência de chegada em frente da barra p
- U4: Qual é a fórmula da TUP para um navio de contentores?
  - Bot `cost_formula`: 📋 Fórmula TUP para navio de contentores: - TUP = GT x UP/taxa aplicável por período, conforme o tarifário em vigor. - Contentores: 0,1144 €/GT no primeiro período/dia e 0,0263 €/GT nos períodos/dias seguintes. - Referências internas disponíveis: UP normal 9.2578 e UP de mudança ao longo do cais 3.3628. - Para fechar o 
- U5: Qual é o comprimento máximo para manobrar na LISNAVE à noite?
  - Bot `berth_profile_fact`: 📋 Na LISNAVE, o comprimento máximo para manobra de noite é 280 metros de LOA. Até esse limite pode manobrar de dia e de noite; acima de 280 metros, a manobra fica limitada ao período diurno. Em ambos os casos, usar reponto de maré.
### sim-004 - eval-window-04
- U1: Um navio com 300 metros de LOA pode entrar no LISNAVE de noite?
  - Bot `berth_profile_fact`: 📋 Não. Na LISNAVE, 300 m é superior a 280 metros; acima desse limite a manobra fica limitada ao período diurno. Ou seja, só de dia / no período diurno; mesmo assim, a manobra deve ser no reponto de maré e validada para o cais/doca concreto.
- U2: Um navio com 250 metros de LOA pode entrar no LISNAVE de noite?
  - Bot `berth_profile_fact`: 📋 Sim. Na LISNAVE, LOA até 280 metros pode manobrar de dia e de noite, desde que seja no reponto de maré. O navio indicado tem 250 m, portanto fica dentro do limite noturno.
- U3: Porque é que as manobras no LISNAVE têm de ser no reponto?
  - Bot `berth_profile_fact`: 📋 Na LISNAVE, as manobras devem ser feitas nos repontos de mare porque os cais estão dispostos perpendicularmente à corrente de maré. No reponto a corrente nula ou praticamente nula reduz o esforço lateral no navio e dá controlo para atracar, largar ou entrar em doca.
- U4: Podem dois navios grandes manobrar ao mesmo tempo no LISNAVE?
  - Bot `berth_profile_fact`: 📋 Na LISNAVE, em marés vivas, quando estão em causa navios com LOA superior a 200 metros, a orientação operacional é uma manobra por reponto. Assim, dois navios grandes não devem ser tratados como rotina para manobrar ao mesmo tempo no mesmo reponto; só com validação expressa do Piloto Coordenador para a bacia, cais/do
- U5: Qual é a profundidade disponível na entrada da Doca 21 com a comporta aberta?
  - Bot `berth_profile_fact`: 📋 Doca 21 / LISNAVE, soleira ao ZH: - Com comporta aberta: 6,10 metros ao ZH (20 pés). - Com comporta fechada: 5,49 metros ao ZH (18 pés). - Para calado praticável, somar a altura de água e aplicar margem operacional; não tratar como calado único global da LISNAVE.
### sim-005 - eval-window-05
- U1: Qual o tipo de defensas que o TMS 1 dispõe?
  - Bot `berth_profile_fact`: 📋 No TMS 1 são utilizadas defensas de borracha do tipo Yokohama.
- U2: A que distância está a posição de embarque dos pilotos da entrada da barra?
  - Bot `operational_route_transit`: 🧭 A posição de embarque dos pilotos fica 1 milha náutica fora da entrada da Barra / Pilar 2. Na malha de percurso, é o segmento Pilot station / posição de embarque -> Pilar 2 / entrada da Barra, rumo 040°, distância 1,0 NM.
- U3: Quanto tempo um navio leva desde a entrada da barra até ao estaleiro da Lisnave?
  - Bot `operational_route_transit`: 🧭 Da entrada da Barra / Pilar 2 até aos estaleiros da LISNAVE/Mitrena, o tempo operacional prático é cerca de 1 hora e 30 minutos a 2 horas pelo Canal Sul. A distância de referência é 10,5 milhas náuticas pelo Canal Sul completo, ou cerca de 10,0 milhas pelo corta-mato.
- U4: Quanto tempo um navio leva desde a entrada da barra até ao Tanquisado ou Eco-Oil?
  - Bot `operational_route_transit`: 🧭 Da entrada da Barra para os destinos principais do Canal Sul (Tanquisado, Eco-Oil, LISNAVE, Termitrena ou Teporset), conta com cerca de 1 hora e 30 minutos a 2 horas.
- U5: Quanto tempo demora da barra ao TMS2 e à Autoeuropa?
  - Bot `operational_route_transit`: 🧭 Da entrada da Barra pelo Canal Norte até ao TMS2 / TMS 2 e à Autoeuropa/Ro-Ro, conta com cerca de 1 hora em condições normais. O TMS2 fica antes da Autoeuropa na sequência TMS 1 -> TMS 2 -> Autoeuropa/Ro-Ro.
### sim-006 - eval-window-06
- U1: Quanto tempo leva até à Secil, Praias do Sado, SAPEC e fundeadouros?
  - Bot `document_companion_global`: 📋 Da entrada da Barra, a SECIL demora cerca de 30 minutos; Praias do Sado e SAPEC demoram cerca de 1 hora e 20 minutos; o Fundeadouro Norte demora cerca de 45 minutos; e o Fundeadouro Sul demora cerca de 45 minutos a 1 hora, conforme a posição seja mais perto do PAN/Tróia ou no fim do canal.
- U2: Quanto tempo leva do Fundeadouro Norte para os cais a norte, cais a sul e a SECIL?
  - Bot `operational_route_transit`: 🧭 Do Fundeadouro Norte para os cais a norte, isto é, de TMS 1 até SAPEC Líquidos, conta com 15 a 25 minutos a navegar, até SAPEC. Para a SECIL, cerca de 20 minutos. Para os cais a sul, isto é, Tanquisado, Eco-Oil, LISNAVE, Termitrena ou Teporset, conta com cerca de 1 hora e 30 minutos.
- U3: Quanto tempo leva do Canal Sul para cais do sul, cais a norte e SECIL?
  - Bot `operational_route_transit`: 🧭 Do Canal Sul para cais do sul, isto é, Tanquisado, Eco-Oil, LISNAVE, Termitrena ou Teporset, conta com cerca de 30 minutos a 1 hora. Do Canal Sul para cais a norte, isto é, de TMS 1 até SAPEC Líquidos, conta com cerca de 1 hora a 1 hora e 20 minutos. Do Canal Sul para a SECIL, cerca de 40 minutos.
- U4: Quanto tempo leva do Fundeadouro Sul / Tróia até à Autoeuropa?
  - Bot `operational_route_transit`: 🧭 Do Fundeadouro Sul / Tróia até à Autoeuropa/Ro-Ro (Cais 10/11), trata como uma mudança interna direta dentro do porto, não como Fundeadouro Sul -> Barra -> Canal Norte. A referência prática dos casebooks é cerca de 1 hora e 50 minutos a 2 horas, ajustando à posição concreta no fundeadouro, corrente e tráfego.
- U5: Quanto tempo leva da entrada da Barra até aos estaleiros da LISNAVE?
  - Bot `operational_route_transit`: 🧭 Da entrada da Barra / Pilar 2 até aos estaleiros da LISNAVE/Mitrena, o tempo operacional prático é cerca de 1 hora e 30 minutos a 2 horas pelo Canal Sul. A distância de referência é 10,5 milhas náuticas pelo Canal Sul completo, ou cerca de 10,0 milhas pelo corta-mato.
### sim-007 - eval-window-07
- U1: Qual a distância do pilar 2 até à LISNAVE?
  - Bot `operational_route_transit`: 🧭 Do Pilar 2 / entrada da Barra até aos estaleiros da LISNAVE/Mitrena são cerca de 10,5 milhas náuticas pelo Canal Sul completo. Pelo atalho ou corta-mato, considera cerca de 10,0 milhas náuticas.
- U2: E quanto tempo levo do fundeadouro sul até ao cais da Autoeuropa?
  - Bot `operational_route_transit`: 🧭 Do Fundeadouro Sul / Tróia até à Autoeuropa/Ro-Ro (Cais 10/11), trata como uma mudança interna direta dentro do porto, não como Fundeadouro Sul -> Barra -> Canal Norte. A referência prática dos casebooks é cerca de 1 hora e 50 minutos a 2 horas, ajustando à posição concreta no fundeadouro, corrente e tráfego.
- U3: Quanto tempo leva a lancha dos pilotos a embarcar no Outão, fora da barra e nos fundeadouros?
  - Bot `operational_route_transit`: 🧭 Tempos práticos da lancha de pilotos desde a estação: - Outão: cerca de 15 minutos. - Fora da Barra / posição oficial de embarque: cerca de 30 minutos. - Fundeadouro Norte: cerca de 5 minutos. - Fundeadouro Sul / Tróia: cerca de 15 minutos. São tempos de referência; ajustar sempre a vento, ondulação, corrente e posiç
- U4: Vou entrar com um navio de 150 m de comprimento e 40 m de boca para o hidrolift da LISNAVE. Marquei manobra agora às 13:54, 2 horas antes da maré para o piloto que embarca lá fora chegar a tempo do reponto de maré. Alguma observação que me esteja a faltar?
  - Bot `operational_rule`: 📋 Não. Há um bloqueio dimensional antes de discutir a hora da manobra: o Hidrolift/Docas 31-33 da LISNAVE admite boca máxima de 32 m e o navio indicado tem 40 m de boca. Assim, a manobra não deve seguir para o Hidrolift como está marcada; será preciso escolher outro cais/doca ou obter validação operacional específica. 
- U5: Quantos rebocadores são necessários para atracar carregado um navio entre 15.001 e 25.000 DWT sem bow thruster?
  - Bot `operational_tug_guidance`: ⚓ Pela IT-016, para atracar carregado um navio entre 15.001 e 25.000 DWT sem bow thruster, a referência é GGp: 2 rebocadores grandes + 1 pequeno (1 rebocador pequeno), ou seja, 3 rebocadores no total. - Esta linha vale tanto para cargas perigosas como para outras cargas nessa faixa de DWT. - G = rebocador grande, com b
### sim-008 - eval-window-08
- U1: Qual é a diferença entre rebocador grande e rebocador pequeno?
  - Bot `document_companion`: 📋 A resposta direta: Rebocador grande significa bollard pull igual ou superior a 25 toneladas. Rebocador pequeno significa bollard pull inferior a 25 toneladas.
- U2: Qual é a velocidade máxima do navio durante o estabelecimento do cabo de reboque?
  - Bot `operational_tug_guidance`: ⚓ Para estabelecimento do cabo de reboque, usa como referência operacional geral 6 nós sobre a água. - O protocolo documental distingue a posição do rebocador: 5 nós à proa, 6 nós ao costado e 8 nós à popa. - Para o bot, a resposta prática validada é 6 nós sobre a água, por ser conservadora e simples para decisão rápid
- U3: O Mestre do rebocador pode recusar estabelecer o cabo?
  - Bot `document_companion`: 📋 Sim. O Mestre pode questionar e recusar o estabelecimento do cabo se a velocidade do navio exceder os limites definidos ou se, nomeadamente em má visibilidade, considerar que as condições não são seguras.
- U4: Quantos reboques para RORO de 180m a entrar com vento norte?
  - Bot `operational_tug_guidance`: ⚓ Recomendo 3 rebocadores grandes. Regra prática aplicável: Ro-Ro com vento Norte a entrar: 3 rebocadores. Fonte: regra prática de rebocadores; confirmar meteorologia atual quando a decisão depender de vento/rajadas. Confirma DWT, carga perigosa, estado carregado/vazio e thrusters; a IT-016 pode agravar mínimos, mas nã
- U5: Um Graneleiro de 190m a sair com vento sul forte, quantos rebocadores leva?
  - Bot `operational_tug_guidance`: ⚓ Recomendo 2 rebocadores grandes. Regra prática aplicável: Graneleiros, reefers, estilha e contentores grandes com vento Sul a sair: 2 rebocadores. Fonte: regra prática de rebocadores; confirmar meteorologia atual quando a decisão depender de vento/rajadas. Confirma DWT, carga perigosa, estado carregado/vazio e thrust
### sim-009 - eval-window-09
- U1: Quantos reboques para RORO de 230m a entrar com vento norte forte?
  - Bot `operational_tug_guidance`: ⚓ Recomendo 4 rebocadores grandes. Regra prática aplicável: Ro-Ro com mais de 220 m e vento Norte forte: considerar 4 rebocadores grandes em casos extremos, porque 1 rebocador a empurrar pode ser insuficiente. Fonte: regra prática de rebocadores; confirmar meteorologia atual quando a decisão depender de vento/rajadas. 
- U2: Um Graneleiro de 190m a sair do TMS2 com vento norte forte, quantos rebocadores leva?
  - Bot `operational_tug_guidance`: ⚓ Recomendo 4 rebocadores grandes. Regra prática aplicável: Graneleiros, reefers, estilha e contentores grandes a sair com vento Norte forte: considerar 4 rebocadores grandes. Excecao: estaleiro/Lisnave, Tanquisado e Eco-Oil devem ser avaliados caso a caso porque a mare e atravessada e muitos navios estao mais leves. F
- U3: Um navio de 130m para a Lisnave precisa de quantos rebocadores?
  - Bot `operational_tug_guidance`: ⚓ Recomendo 3 rebocadores grandes. Regra prática aplicável: Lisnave acima de 100 m ate 150 m: 3 rebocadores. Quatro rebocadores sao excessivos para estes navios leves e podem tornar a manobra dificil. Fonte: regra prática de rebocadores; confirmar meteorologia atual quando a decisão depender de vento/rajadas. Outras re
- U4: A sair de Tanquisado com vento E forte, onde meto o reboque?
  - Bot `operational_tug_guidance`: ⚓ Para Tanquisado a sair/largar com vento Leste forte, mantém a leitura conservadora local: - Tanquisado com 3 rebocadores é o mínimo prático local; - manter 1 rebocador estabelecido à proa e 1 à popa (1 a proa e 1 a popa) para controlo longitudinal; - Tanquisado a sair com vento E forte: o terceiro fica como 1 rebocad
- U5: A sair da Eco-Oil com vento W forte, onde meto o reboque?
  - Bot `operational_tug_guidance`: Recomendo 3 rebocadores. Regra prática aplicável: Eco-Oil: usar sempre no minimo 3 rebocadores em entrada, saida ou mudanca, independentemente de bowthruster, navio pequeno ou condicoes aparentemente favoraveis. E uma margem operacional de seguranca para cais/corrente atravessados. Fonte: regra prática de rebocadores; 
### sim-010 - eval-window-10
- U1: Onde meto os reboques num navio grande com bowthruster operacional?
  - Bot `operational_tug_guidance`: ⚓ Regra prática de posicionamento dos rebocadores: - Com bowthruster operacional em navios grandes, colocar por norma o rebocador a popa. A prioridade e controlar a popa para nao fugir para o cais nem bater. - Estas regras referem-se sobretudo a rebocadores convencionais. Rebocadores com azipodes tem mais capacidade, m
- U2: Um roro vai sair agora da Autoeuropa. Tem 200 m e já pus 2 reboques. Pode sair? Ah, mas ficou nevoeiro!
  - Bot `operational_safety_limit`: ⚠️ Local: Autoeuropa. Não. Com nevoeiro em porto / visibilidade reduzida, as manobras ficam suspensas até a visibilidade operacional ser restaurada. O número de rebocadores não elimina esta restrição; depois da visibilidade voltar, reavalia-se a manobra e os meios necessários.
- U3: Se não fosse nevoeiro e estivesse 31 kts de vento, já podia sair desde que tivesse 4 reboques?
  - Bot `operational_safety_limit`: 🌬️ Não. Com vento sustentado ou rajada superior a 25 kt (31 kt no caso indicado), as manobras ficam suspensas por segurança. Ter mais rebocadores não anula este limite. Se a suspensão foi acionada por vento, a retoma só deve ser considerada quando o vento baixar para menos de 25 kt. Fonte: limite operacional de seguran
- U4: Que fundeadouro recomendas para um navio de grande calado superior a 9 metros?
  - Bot `operational_rule`: 📋 Para um navio de grande calado superior a 9 metros, recomendo o Fundeadouro Sul / Tróia como referência preferencial: é o mais profundo e mais amplo para esse perfil. - O Fundeadouro Norte pode servir para espera/gestão corrente, mas para grande calado fica mais condicionado. - Em emergência, a escolha final deve con
- U5: Posso atracar um navio com 12 m no cais da SAPEC Líquidos?
  - Bot `operational_rule`: 📋 Não. No SAPEC Líquidos / TGL, trato esse valor como calado porque a pergunta é de atracação/compatibilidade operacional. - Carga IMO/perigosa: calado máximo 9,5 metros. - Carga não IMO: calado máximo 10,0 metros. - Um navio com 12 m de calado ultrapassa ambos os limites e não deve ser aceite em condições normais. Só 
