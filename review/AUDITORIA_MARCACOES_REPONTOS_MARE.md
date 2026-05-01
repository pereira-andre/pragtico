# Auditoria - Marcação de manobras por reponto de maré e preia-mar

Data da auditoria: 2026-05-01

## Ficheiros revistos

- `knowledge/Marcar_manobra_repontos_mare.txt`
- `knowledge/companions/Marcar_manobra_repontos_mare.json`
- `knowledge/IT-005_TMS1.txt`
- `knowledge/IT-006_TMS2.txt`
- `knowledge/IT-008_EcoOil.txt`
- `knowledge/IT-009_Secil.txt`
- `knowledge/IT-010_Tanquisado.txt`
- `knowledge/IT-011_Termitrena.txt`
- `knowledge/IT-014_Lisnave.txt`
- `knowledge/IT-029_SAPEC.txt`
- `knowledge/IT-062_Teporset.txt`
- PDFs originais correspondentes em `review/PILOTOS INSTRUÇÕES DE TRABALHO/`

## Resultado

1. O ficheiro de marcações estava correto na estrutura geral, mas a lista de
   fontes estava incompleta. Foi atualizada para incluir IT-005, IT-006, IT-008,
   IT-009, IT-010, IT-011, IT-014, IT-029 e IT-062.

2. A informação prática da Secil existia no companion e no IT-009, mas não estava
   explícita no TXT principal de marcações. Foi adicionada ao TXT:
   - entradas de fora da Barra ou Fundeadouro Norte: `30 a 45 min` antes do
     reponto;
   - entradas de Tróia ou outro cais: `45 min a 1 h` antes do reponto;
   - saídas: cerca de `15 min` antes do reponto;
   - a saída tende a libertar o cais em `10 a 15 min`.

3. Foi corrigida uma simplificação sobre a Tanquisado:
   - o ficheiro dizia, na prática, que as saídas eram nos repontos;
   - o IT-010 também permite saída em vazante se a preia-mar precedente tiver
     altura igual ou inferior a `3 m`;
   - foi acrescentada esta exceção documental, mantendo a regra prática de
     `1 h` antes quando a saída for planeada para o reponto.

4. O companion foi alinhado com o TXT:
   - resumo atualizado;
   - key points com Secil e exceção da Tanquisado;
   - FAQ nova sobre saída da Tanquisado fora do reponto;
   - resposta sobre reponto vs preia-mar ajustada para distinguir documento de
     prática operacional.

5. Após confirmação do André, Teporset e Termitrena deixaram de ser descritos
   como marcação ao reponto. A regra passa a ser:
   - seguir as instruções de trabalho dos respetivos terminais;
   - para navios de maior calado, tratar a preia-mar / janela de maior água como
     recomendação operacional forte;
   - razão operacional: entre a Termitrena e a Teporset existe um baixo de cerca
     de `7 m` que pode puxar a proa dos navios de maior calado para o baixo,
     com risco de encalhe momentâneo.

## Regras confirmadas por fonte documental

| Fonte | Regra |
|---|---|
| IT-008 Eco-Oil | Manobras próximo dos repontos; regras distintas por preia-mar/baixa-mar, dia/noite e LOA. |
| IT-009 Secil | Oeste: reponto para todos; LOA >170 m só com luz do dia e junto da preia-mar. Este: reponto em marés vivas. |
| IT-010 Tanquisado | Atracações nos repontos; saídas nos repontos ou em vazante se PM precedente <= 3 m; regras especiais noturnas. |
| IT-014 Lisnave | Todas as manobras próximo dos repontos; >280 m só de dia; docas 21/22 dependem de preia-mar. |
| IT-005/006 TMS | Calados elevados dependem de janela de preia-mar por profundidade. |
| IT-029 SAPEC TPS | Calados próximos do máximo dependem da preia-mar; regra das carochas por baixa-mar. |
| IT-011 Termitrena | Calado calculado por `8,8 m + baixa-mar de referência`, com teto de `10,0 m`; procurar cerca de `2 m` acima do ZH para lançantes exteriores. |
| IT-062 Teporset | Calado calculado por `7,4 m + altura da preia-mar`, com teto de `11,0 m`. |

## Regras tratadas como prática operacional

- Antecedências concretas de marcação: `2 h`, `1h30`, `1 h`, `30-45 min`,
  `15 min`.
- Tempos de trânsito por origem/destino.
- Teporset e Termitrena com navios de maior calado: recomendação operacional
  forte para usar preia-mar / janela de maior água, devido ao baixo de cerca de
  `7 m` entre os dois cais.

## Validações executadas

- `jq empty knowledge/companions/Marcar_manobra_repontos_mare.json knowledge/berth_profiles.json knowledge/evals/golden_operational_companion_evals.json`
- `python3 scripts/run_rag_evals.py --knowledge-dir knowledge --fail-on-fail`
- `python3 -m pytest -q`

Resultados:
- evals RAG locais: `8/8` passaram;
- suíte completa: `85 passed, 6 subtests passed`.

## Confirmações do André

1. Teporset e Termitrena não devem ser tratados como regra de reponto formal.
   Deve-se adotar o que dizem os ITs, com recomendação forte para preia-mar /
   maior água quando o calado for condicionante.
2. Para mudanças de Fundeadouro Norte/Tróia para Tanquisado ou Eco-Oil, está
   correta a lógica prática dos cais do Canal Sul: `1h30` desde Fundeadouro
   Norte e `1h` desde Tróia.

## Próximo bloco recomendado

Validar `IT-018_NormasEspeciais.txt`, `Regras Especiais.doc`,
`Condicoes_Meteorologicas_Prioridades.txt` e `operational_safety_limits.json`
em conjunto.
