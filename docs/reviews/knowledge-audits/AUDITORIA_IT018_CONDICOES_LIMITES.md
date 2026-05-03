# Auditoria - IT-018, Regras Especiais, meteorologia e limites de segurança

Data da auditoria: 2026-05-01

## Ficheiros revistos

- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-018_RAM_Normas_Especiais.pdf`
- `review/Regras Especiais.doc`
- `knowledge/IT-018_NormasEspeciais.txt`
- `knowledge/companions/IT-018_NormasEspeciais.json`
- `knowledge/Condicoes_Meteorologicas_Prioridades.txt`
- `knowledge/companions/Condicoes_Meteorologicas_Prioridades.json`
- `knowledge/operational_safety_limits.json`
- `knowledge/tug_operational_guidance.json`

## Resultado

1. `IT-018_NormasEspeciais.txt` confere com o PDF IT-018 Revisão 04 nos pontos
   operacionais principais:
   - calado Barra/Canal Norte = `10,30 m + altura de maré`, teto `12,0 m`,
     válido para ondulação inferior a `1 m`;
   - calado específico Estaleiro da Mitrena = `7,50 m`;
   - > `220 m` para Mitrena e navios com dificuldades de máquina/governo:
     pelo menos 1 rebocador durante o percurso;
   - vento > `20 nós`: Piloto e Comandante avaliam viabilidade;
   - embarque/desembarque no Outão: LOA < `145 m`, calado < `7,5 m`,
     tempo e mar regulares;
   - evitar cruzamentos entre pilares 2 e 4 e canais SAPEC, MAGUE e LISNAVE;
   - regras de saída de navios do Estaleiro da Mitrena por DWT;
   - manobra de correr ao longo do cais carece de parecer prévio da Pilotagem,
     com referência de vento de `10 nós` para designação de Piloto.

2. `Regras Especiais.doc` é uma fonte paralela/legada. Contém praticamente a
   base do IT-018, mas também junta matérias de outros documentos e notas antigas:
   regulação de agulhas, fundeadouros, dados de terminais, curiosidades da barra,
   notas de canais e notas de rebocadores. Deve ser usado como contexto histórico
   ou prático, mas não para substituir ITs atuais mais específicos.

3. Foram identificados exemplos claros de conteúdo legado no `.doc` que não deve
   sobrepor a base atual:
   - Termitrena aparece com limites antigos (`10,50 m`, `10,60 m` com PM >=
     `2,7 m`, `8,2 m` a qualquer hora). O conhecimento atual fica pelo IT-011:
     `8,8 m + baixa-mar de referência`, teto `10,0 m`.
   - A nota antiga "rebocadores devem ser estabelecidos a v=5/6 nós" fica
     subordinada à confirmação operacional/protocolo atual: estabelecer a `6 nós`.
   - Vários dados de cais no `.doc` são resumos antigos e não substituem os ITs
     atuais de cada terminal.

4. `Condicoes_Meteorologicas_Prioridades.txt` foi corrigido em dois pontos:
   - prioridades deixam de dizer que Teporset/Termitrena são "reponto específico";
     passam a ser "reponto ou janela crítica de maré/profundidade", mantendo
     Teporset/Termitrena como preia-mar / maior água quando o calado condiciona;
   - a secção de rebocadores em vento forte foi alinhada com a prática confirmada:
     com bowthruster operacional em navios grandes, privilegiar popa; sem
     bowthruster, equilibrar proa/popa; em Ro-Ro com 2 rebocadores, pode usar-se
     1 à popa e 1 ao costado a empurrar.

5. `operational_safety_limits.json` foi tornado mais explícito:
   - `20 kt`: limiar formal IT-018 para avaliação de viabilidade;
   - `25 kt`: limite prático acima do qual a manobra é geralmente impraticável;
   - `30 kt`: suspensão operacional por vento/rajada;
   - retoma após suspensão por vento apenas abaixo de `25 kt`;
   - `fog_visibility_km_reference=1.0` fica marcado como referência técnica
     confirmada para dados live, não como regra documental autónoma.

## Confirmação do utilizador

- Confirmado pelo utilizador: manter `1,0 km` como limiar técnico de
  visibilidade live para o sistema tratar como "visibilidade reduzida/nevoeiro",
  mesmo quando a meteorologia não diga literalmente "nevoeiro".

## Validações

- `jq empty` nos JSON alterados.
- `python3 -m py_compile domain/operational_safety.py core/operational_sources.py`
- `python3 scripts/run_conhecimento indexavel_evals.py --knowledge-dir knowledge --fail-on-fail`
- `python3 -m pytest -q`
