# Auditoria - berth_profiles.json

Data da auditoria: 2026-05-01

## Ficheiros revistos

- `knowledge/berth_profiles.json`
- `domain/berth_profiles.py`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-005_RAM_T_Multiusos_Z1.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-006_RAM_T_Multiusos_Z2.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-007_RAM_T_Autoeuropa.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-008_RAM_T_Ecoil.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-009_RAM_T_Secil.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-010_RAM_T_Tanquisado.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-011_RAM_T_TERMITRENA ex-Eurominas.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-012_RAM_T_Praias do Sado.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-014_RAM_Lisnave.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-029_Regras aplicáveis a manobras-CAIS da SAPEC (TPS e TGL).pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-038_RAM_Cais_Alstom.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-062 - Cais da Teporset.pdf`

## Resultado

1. `berth_profiles.json` tinha 11 perfis, mas com `lisnave` duplicado. O duplicado
   foi removido. O ficheiro ficou com 12 perfis únicos.

2. Faltavam perfis estruturados para:
   - `auto_europa`, derivado do IT-007;
   - `termitrena`, derivado do IT-011.

3. Foi corrigida uma incoerência no perfil `tanquisado`:
   - antes: saída noturna = `4,8 m + altura de água`, com teto de `8,0 m`;
   - correto: o teto de `8,0 m` aplica-se à atracação noturna de navios com LOA
     superior a 110 m nos dias em que as duas preia-mares são noturnas;
   - saída noturna: `4,8 m + altura de água`, sem teto adicional explícito além
     do calado máximo absoluto do terminal, `9,5 m`.

4. Os restantes perfis revistos ficaram coerentes com os respetivos ITs nos
   campos críticos: LOA/comprimento, calado, regras de maré/reponto, noite,
   vento quando aplicável e notas de rebocadores.

5. Após esclarecimento operacional, os perfis `termitrena` e `teporset` passaram
   a incluir uma recomendação prática, separada da regra documental: para navios
   de maior calado, privilegiar a preia-mar / janela de maior água na aproximação,
   porque existe um baixo de cerca de `7 m` entre os dois cais que pode puxar a
   proa do navio para o baixo. Isto fica marcado como recomendação operacional,
   não como regra formal de reponto.

## Perfis após correção

| Perfil | Fonte | Estado |
|---|---|---|
| `eco_oil` | IT-008 | Conferido |
| `tanquisado` | IT-010 | Corrigido |
| `praias_sado` | IT-012 | Conferido |
| `sapec` | IT-029 | Conferido |
| `alstom` | IT-038 | Conferido |
| `secil` | IT-009 | Conferido |
| `lisnave` | IT-014 | Duplicado removido; perfil mantido |
| `tms1` | IT-005 | Conferido |
| `tms2` | IT-006 | Conferido |
| `auto_europa` | IT-007 | Adicionado |
| `termitrena` | IT-011 | Adicionado |
| `teporset` | IT-062 | Conferido |

## AutoEuropa - dados introduzidos

- Cais 10: comprimento total `363 m`.
- Cais 10: calado permitido `10,0 m`.
- Cais 11 / rampa: comprimento de referência do módulo navio-rampa `172 m`.
- Cais 11 / rampa: calado permitido `10,0 m`.
- Rampa: atracação com enchente ou vazante.
- Rampa: evitar meias-marés vivas.
- Rampa: viabilidade depende da capacidade do navio e do uso ou não de rebocadores.
- Rampa: navio deve conseguir manter-se seguro com um só cabo de lançante à boia
  de amarração.

## Termitrena - dados introduzidos

- Comprimento físico do cais: `154 m`.
- LOA máximo permitido: `200 m`.
- Calado máximo: `8,8 m + altura da baixa-mar de referência`.
- Teto absoluto de calado: `10,0 m`.
- Navio a carregar: a baixa-mar de referência é a que antecede a preia-mar para
  a qual existe certeza de largada.
- Navio de chegada com carga: a baixa-mar de referência é a imediatamente após a
  preia-mar de atracação, ou as seguintes se não iniciar descarga em tempo.
- Atracação recomendada com cerca de `2 m` de água acima do ZH para facilitar os
  lançantes exteriores.
- Para navios de maior calado, recomendação operacional forte para usar a
  preia-mar / janela de maior água na aproximação, devido ao baixo de cerca de
  `7 m` entre Termitrena e Teporset.
- `Eurominas` e `Termitrena` foram tratados como a mesma instalação.

## Teporset - nota operacional

- A regra documental mantém-se: calado máximo = `7,4 m + altura da preia-mar`,
  com teto absoluto de `11,0 m`.
- Para navios de maior calado, foi acrescentada a recomendação operacional forte
  para privilegiar a preia-mar / janela de maior água, pelo mesmo risco do baixo
  de cerca de `7 m` entre Termitrena e Teporset.

## Validações executadas

- `jq empty knowledge/berth_profiles.json`
- `python3 -m py_compile domain/berth_profiles.py`
- testes diretos de matching para AutoEuropa, Cais 11, Termitrena, Eurominas e
  Lisnave noturna
- `python3 scripts/run_rag_evals.py --knowledge-dir knowledge --fail-on-fail`
- `python3 -m pytest tests/test_knowledge_chunking.py tests/test_route_transit.py tests/test_operational_sources_direct.py -q`
- `python3 -m pytest tests/test_berth_layout.py tests/test_slash_planning.py -q`
- `python3 -m pytest -q`

Resultado das evals RAG locais: `8/8` passaram.

Resultado da suíte completa: `85 passed, 6 subtests passed`.

## Próximo bloco recomendado

Validar `Marcar_manobra_repontos_mare.txt` contra IT-008, IT-010, IT-011,
IT-014, IT-062 e prática operacional.
