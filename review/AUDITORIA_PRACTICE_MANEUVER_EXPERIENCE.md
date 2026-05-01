# Auditoria - Manobras Pratica.xlsx vs practice_maneuver_experience.json

Data: 2026-04-30

Objetivo: validar se `knowledge/practice_maneuver_experience.json` confere com `review/Manobras Pratica.xlsx`.

## Resultado curto

O JSON está estruturalmente válido e a maior parte dos `source_rows` amostrados bate com o Excel, mas há inconsistências relevantes:

1. O JSON está desatualizado face ao Excel atual: faltam os registos `No 1389` a `No 1407`.
2. As manobras de `Mudança` com `/` no campo `Cais` não estão corretamente estruturadas como origem/destino.
3. Uma linha cancelada sem número (`Four Wind`, linha Excel 890) entrou no JSON como padrão prático.
4. Há dois problemas pontuais de parsing nos dados amostrados: `Rebocadores=5.2` e dimensão `3 x 29/18`.
5. O campo textual `vessel_snapshot.gt_t` tem valores de GT truncados em vários padrões, apesar de `feature_snapshot.vessel_gt_t` estar correto.

## Fontes analisadas

- Original: `review/Manobras Pratica.xlsx`
- Folha: `Dados`
- JSON: `knowledge/practice_maneuver_experience.json`

## Contagens

### Excel atual

- Linhas numeradas válidas: `1407`, com `No` de `1` a `1407`.
- Tipos nas linhas numeradas:
  - `Entrada`: 668
  - `Saída`: 548
  - `Mudança`: 190
  - `Fundear`: 1
- Linhas sem número mas com conteúdo operacional: 2
- Linhas finais não operacionais/fórmulas/totais: 12

### JSON atual

- `records`: 513 padrões agregados.
- Soma de `practice_metrics.case_count`: 1389 casos.
- Tipos no JSON:
  - `Entrada`: 662
  - `Saída`: 538
  - `Mudança`: 188
  - `Fundear`: 1
- `generated_at`: 2026-04-14T16:17:26Z

### Cobertura inferida

Por contagem, o JSON corresponde a:

- Linhas numeradas `No 1` a `No 1388`;
- mais a linha sem número `Four Wind` de 2024-06-26;
- exclui os registos numerados `No 1389` a `No 1407`.

Isto explica a diferença total: `1408` casos operacionais possíveis no Excel atual se a linha `Four Wind` for incluída, contra `1389` no JSON.

## Registos numerados em falta no JSON

| No | Data | Tipo | Navio | Tipo navio | Dimensão | Calado | Rebocadores | Cais | Comentário |
|---:|---|---|---|---|---|---:|---:|---|---|
| 1389 | 2026-04-14 | Saída | Toscana | RORO | 200/32 | 8.1 | 2 | Auto Europa |  |
| 1390 | 2026-04-14 | Saída | Blue Note | Carga Geral | 90/16 | 4.0 | 0 | SAPEC |  |
| 1391 | 2026-04-15 | Saída | Aquarius Ace | RORO | 175/29 | 6.9 | 1 | Auto Europa |  |
| 1392 | 2026-04-15 | Entrada | Herbeira | Carga Geral | 119/15 | 4.6 | 0 | TMS 1 |  |
| 1393 | 2026-04-15 | Entrada | Cement Trader | Cimenteiro | 106/15 | 4.7 | 0 | Secil E |  |
| 1394 | 2026-04-15 | Saída | Manisa Kate | Carga Geral | 108/19 | 4.6 | 0 | Teporset |  |
| 1395 | 2026-04-15 | Mudança | Grande Anversa | RORO | 176/31 | 9.0 | 2 | F.S./Auto Europa |  |
| 1396 | 2026-04-16 | Mudança | Wilson Clyde | Carga Geral | 100/13 | 5.4 | 0 | F. N./ TMS 1 |  |
| 1397 | 2026-04-20 | Mudança | Neptune Ethos | RORO | 183/32 | 8.6 | 2 | F. S./ Auto Europa |  |
| 1398 | 2026-04-21 | Entrada | Faith | Contentores | 155/22 | 6.1 | 2 | TMS 2 | Sem Bowthruster |
| 1399 | 2026-04-21 | Entrada | Amisia | Carga Geral | 111/15 | 4.4 | 0 | TMS 2 |  |
| 1400 | 2026-04-21 | Saída | Faith | Contentores | 155/22 | 6.9 | 0 | TMS 2 |  |
| 1401 | 2026-04-22 | Saída | Herbeira | Carga Geral | 119/15 | 7.1 | 0 | TMS 1 |  |
| 1402 | 2026-04-22 | Entrada | Containerships Arctic | Contentores | 184/27 | 9.1 | 1 | TMS 2 |  |
| 1403 | 2026-04-24 | Entrada | Cement Trader | Cimenteiro | 106/15 | 4.7 | 0 | Secil E |  |
| 1404 | 2026-04-24 | Saída | Smaland | Carga Geral | 120/15 | 7.1 | 0 | Praias do Sado |  |
| 1405 | 2026-04-25 | Saída | Wilson Avonmouth | Carga Geral | 88/12 | 4.9 | 0 | TMS 2 |  |
| 1406 | 2026-04-25 | Saída | Wolfsburg | RORO | 200/38 | 8.6 | 1 | Auto Europa |  |
| 1407 | 2026-04-25 | Saída | NQ Lilium | Tanque | 115/18 | 5.3 | 0 | SAPEC LIQ |  |

## Validação dos `source_rows`

Foram verificados `1276` `source_rows` guardados no JSON.

Resultado:

- Referências inexistentes no Excel: `0`.
- Tipo de manobra, tipo de navio e bandas LOA/boca/calado: batem, exceto os casos abaixo.
- Se célula em branco em `Rebocadores` for considerada `0`, quase todas as diferenças desaparecem.

### Diferenças reais encontradas

| No | Problema | Excel | JSON | Observação |
|---:|---|---|---|---|
| 344 | Rebocadores com decimal | `5.2` | `5` | Parece erro/typo no Excel. Como número de rebocadores, `5.2` não faz sentido operacional. |
| 1033 | Dimensão composta | `3 x 29/18` | LOA `3`, boca `29` | Parece representar `3` barcaças de `29 x 18`, mas o JSON leu `3` como LOA. Confirmar tratamento correto. |

## Linhas sem número

| Linha Excel | Data | Navio | Tipo | Cais | Estado no JSON | Observação |
|---:|---|---|---|---|---|---|
| 38 | 2022-04-21 | Kristin C | sem tipo de manobra | sem cais | Excluída | Comentário indica cancelamento por mau tempo; exclusão parece coerente. |
| 890 | 2024-06-26 | Four Wind | Mudança | Eco-oil/ C3A | Incluída | Entrou como `Mudança | Não indicado | Eco-oil/ C3A | sem registo`; comentário diz "Primeira manobra cancelada". Confirmar se deve ficar. |

## Problema de origem/destino nas mudanças com `/`

O Excel tem `98` linhas numeradas até `No 1388` com:

- `Tipo Manobra = Mudança`
- campo `Cais` contendo `/`

Pela regra indicada pelo utilizador, estes casos devem representar origem/destino quando houver separação por `/`. O JSON atual não estrutura isso corretamente: em muitos casos deixa `origin` vazio e guarda só um destino canónico, perdendo a outra ponta da manobra.

Exemplos:

| No | Excel `Cais` | JSON atual |
|---:|---|---|
| 367 | `SAPEC / SECIL W` | `origin=""`, `destination="Secil W"` |
| 790 | `TMS 2/C11` | `origin=""`, `destination="TMS 2"` |
| 894 | `SAPEC/TMS2` | `origin=""`, `destination="TMS 2"` |
| 948 | `Teporset/ F. N` | `origin=""`, `destination="Teporset"` |
| 1033 | `C1A/D32` | `origin=""`, `destination="C1A/D32"` |

Impacto: o bot pode aprender que a manobra é apenas para um cais, quando a experiência original registava uma mudança entre dois pontos.

## Problema em `vessel_snapshot.gt_t`

Foram encontrados `43` padrões onde `vessel_snapshot.gt_t` difere mais de 1% de `feature_snapshot.vessel_gt_t`.

Exemplos:

| Padrão | `vessel_snapshot.gt_t` | `feature_snapshot.vessel_gt_t` |
|---|---:|---:|
| Saída Contentores TMS 2 100-150m | 999 | 9990 |
| Saída RORO Auto Europa 200-250m | 6379 | 63790 |
| Entrada RORO Auto Europa 150-200m | 3868 | 38680 |
| Entrada Tanque Eco-Oil 250-300m | 6132 | 61320 |

Isto parece vir de formatação/truncagem de zeros no snapshot textual. O valor numérico em `feature_snapshot` parece correto, mas o snapshot pode induzir erro se for mostrado ao admin ou usado em prompt.

## Decisões a confirmar

Decisões recebidas do utilizador:

1. `Rebocadores` em branco no Excel não deve ser tratado como `0`. Se está em branco, a manobra não conta para experiência prática porque foi cancelada/abortada. Células com valor numérico `0` contam como zero rebocadores.
2. A linha Excel 890 `Four Wind`, com comentário de manobra cancelada, não deve contar.
3. A dimensão `3 x 29/18` é caso especial: são 3 barcaças amarradas juntas; tratar como LOA total `3 x 29 = 87 m` e boca `18 m`.
4. Para `Mudança`, quando o campo `Cais` tem `/`, assumir origem/destino, exceto aliases conhecidos que não representam rota, como `Secil/Outão W`.
5. Atenção especial: `Secil W/Secil E` é uma mudança real, curta, mas operacionalmente válida.
6. `Rebocadores=5.2` no registo `No 344` será normalizado para `5`, porque o número de rebocadores tem de ser inteiro e o JSON anterior já seguia essa interpretação.
7. O JSON deve ser regenerado a partir do Excel atual e os `vessel_snapshot.gt_t` devem ser corrigidos.

## Correção aplicada

Foi criado o script reproduzível `scripts/generate_practice_maneuver_experience.py` e o ficheiro `knowledge/practice_maneuver_experience.json` foi regenerado a partir do Excel atual.

Resultado final:

- Padrões gerados: `518`
- Manobras incluídas: `1407`
- Tipos: `Entrada (668), Saída (548), Mudança (190), Fundear (1)`
- Linhas sem `source_rows`: `0`
- Linha cancelada `Four Wind` sem número: excluída
- Registo `No 1033`: convertido para LOA `87 m`, boca `18 m`, GT `1566`
- Registo `No 797`: `Secil W -> Secil E`
- Registo `No 1395`: `Fundeadouro Sul / Tróia -> Auto Europa`
- Divergências relevantes entre `vessel_snapshot.gt_t` e `feature_snapshot.vessel_gt_t`: `0`

Validações feitas:

- `jq empty knowledge/practice_maneuver_experience.json`
- carregamento por `domain.practice_experience.load_practice_experience_records_from_json`
- compilação de `scripts/generate_practice_maneuver_experience.py`
