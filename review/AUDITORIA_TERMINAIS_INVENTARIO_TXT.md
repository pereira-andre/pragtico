# Auditoria - terminais, cais e inventário operacional

Data da auditoria: 2026-05-01

## Ficheiros revistos

- `knowledge/Porto_Setubal_Terminais_Cais.txt`
- `knowledge/companions/Porto_Setubal_Terminais_Cais.json`
- `knowledge/IT-005_TMS1.txt` e companion
- `knowledge/IT-006_TMS2.txt` e companion
- `knowledge/IT-007_AutoEuropa.txt` e companion
- `knowledge/IT-008_EcoOil.txt` e companion
- `knowledge/IT-009_Secil.txt` e companion
- `knowledge/IT-010_Tanquisado.txt` e companion
- `knowledge/IT-011_Termitrena.txt` e companion
- `knowledge/IT-012_PraiasSado.txt` e companion
- `knowledge/IT-014_Lisnave.txt` e companion
- `knowledge/IT-029_SAPEC.txt` e companion
- `knowledge/IT-038_Alstom.txt` e companion
- `knowledge/IT-062_Teporset.txt` e companion
- `knowledge/berth_profiles.json`

## Fontes originais usadas

- IT-005, IT-006, IT-007, IT-008, IT-009, IT-010, IT-011, IT-012, IT-014,
  IT-029, IT-038 e IT-062 em `review/PILOTOS INSTRUÇÕES DE TRABALHO/`.

## Resultado

1. Os valores principais dos ITs de terminais conferem com os PDFs nos pontos
   críticos: comprimentos, calados, fórmulas de maré, limites noturnos, regras
   de reponto/preia-mar, carochas, vento e validação pelo Piloto Coordenador.

2. Foi corrigida uma incoerência documental no bloco LISNAVE:
   - antes: alguns ficheiros tratavam `Docas 20, 21 e 22` como "docas secas";
   - correto segundo o IT-014:
     - `Doca 20` = Plataforma de Construção;
     - `Docas 21 e 22` = docas com soleira documentada;
     - `Plataformas 31, 32 e 33` = acesso por um único Hidrolift, boca máxima
       `32 m`, sonda de acesso `5,5 m`.
   - corrigido em `IT-014_Lisnave.txt`, companion, `Porto_Setubal_Terminais_Cais`
     e `berth_profiles.json`.

3. A recomendação operacional Termitrena/Teporset foi propagada para os TXT
   diretos e companions:
   - `IT-011_Termitrena.txt`;
   - `companions/IT-011_Termitrena.json`;
   - `IT-062_Teporset.txt`;
   - `companions/IT-062_Teporset.json`;
   - inventário de terminais.

   Regra formal preservada:
   - Termitrena: `8,8 m + baixa-mar de referência`, teto `10,0 m`;
   - Teporset: `7,4 m + altura da preia-mar`, teto `11,0 m`.

   Nota prática adicionada:
   - para navios de maior calado, privilegiar preia-mar / janela de maior água
     na aproximação, devido ao baixo de cerca de `7 m` entre Termitrena e
     Teporset que pode puxar a proa para o baixo.

4. `Porto_Setubal_Terminais_Cais.txt` mantém-se como inventário/síntese para
   perguntas amplas. Não substitui os ITs de cada terminal para decisões de
   calado, maré, noite ou meios.

## Estado por terminal

| Documento | Estado |
|---|---|
| IT-005 TMS1 | Conferido |
| IT-006 TMS2 | Conferido |
| IT-007 AutoEuropa | Conferido |
| IT-008 Eco-Oil | Conferido |
| IT-009 Secil | Conferido |
| IT-010 Tanquisado | Conferido |
| IT-011 Termitrena | Conferido + nota operacional prática acrescentada |
| IT-012 Praias do Sado | Conferido |
| IT-014 Lisnave | Corrigido no ponto Doca 20 / Plataformas 31-33 |
| IT-029 SAPEC | Conferido |
| IT-038 Alstom | Conferido |
| IT-062 Teporset | Conferido + nota operacional prática acrescentada |

## Validações

- `jq empty` nos companions/JSON alterados.
- Pesquisa por valores legados: não ficaram referências problemáticas a
  Termitrena antiga (`10,50`, `10,60`, `8,2 m`) nem a rebocadores `5/6 nós`.
- `python3 scripts/run_rag_evals.py --knowledge-dir knowledge --fail-on-fail`
- `python3 -m pytest -q`
