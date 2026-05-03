# Matriz de correspondência review -> knowledge

Data: 2026-04-30

Objetivo desta fase: mapear os ficheiros originais em `review` contra os ficheiros processados em `knowledge`, antes da validação de conteúdo.

Estado:
- Fontes em `review`: 31 ficheiros relevantes, excluindo `.DS_Store` e este ficheiro de matriz.
- TXT em `knowledge`: 30 ficheiros.
- JSON em `knowledge`: 35 ficheiros.
- JSON verificados sintaticamente: todos válidos.

Notas de leitura:
- `knowledge/companions/*.json` são companions associados aos TXT homónimos. Nesta fase ficam ligados ao mesmo original do TXT.
- `berth_profiles.json`, `tug_operational_guidance.json`, `operational_safety_limits.json` e `practice_maneuver_experience.json` são conhecimento estruturado usado fora do conhecimento textual principal.
- Esta matriz ainda não confirma que os valores estão corretos. Serve para saber que fonte comparar com que output.
- Marés: não foi adicionado ficheiro original de marés nesta matriz; ficam tratadas como corretas por confirmação do utilizador.

## 1. Mapeamento direto ou provável

| Original em `review` | Processado em `knowledge` | Estado | Observações |
|---|---|---|---|
| `Manobras Pratica.xlsx` | `practice_maneuver_experience.json` | Direto | Bloco auditado em `AUDITORIA_PRACTICE_MANEUVER_EXPERIENCE.md`; JSON regenerado com canceladas/abortadas excluídas e caso especial `3 x 29/18` tratado como 3 barcaças amarradas. |
| `chat_sistema.txt` | `whatsapp_chats/chat_sistema.txt` | Direto | Ficheiros idênticos por comparação binária (`cmp`). Histórico/conversa de validação operacional, não documento normativo. |
| `Notas Pilotagem.docx` | `Notas_Pilotagem.txt`; `companions/Notas_Pilotagem.json` | Combinado | O TXT declara fonte combinada: notas pessoais sobre carta + notas de pilotagem. |
| `Notas Pessoais Carta Setubal.docx` | `Notas_Pilotagem.txt`; `companions/Notas_Pilotagem.json` | Combinado | Contém proas, distâncias, resguardos, canais e bacias de manobra. |
| `np67-west-coast-of-spain-and-portugal-9-edition-2005-pr_0cd321a0a1aa2a98e387b5cbabab9ff4.pdf` | `AdmiraltyPilot_PortoSetubal.txt`; `companions/AdmiraltyPilot_PortoSetubal.json` | Direto parcial / extraído | Bloco auditado em `AUDITORIA_FONTES_PARCIAIS_SEM_PAR_DIRETO.md`; fonte histórica 2004/2005, várias distâncias corrigidas. |
| `Projeto de Regulamento de Tarifas da APSS.pdf` | `Tarifas_APSS_2024.txt`; `companions/Tarifas_APSS_2024.json` | Direto parcial / extraído | Bloco auditado em `AUDITORIA_FONTES_PARCIAIS_SEM_PAR_DIRETO.md`; tarifas principais conferidas e cabeçalho ajustado para indicar que o original é projeto. |
| `Regras Especiais.doc` | `IT-018_NormasEspeciais.txt`; `companions/IT-018_NormasEspeciais.json`; `operational_safety_limits.json` | Fonte paralela / legado | Bloco auditado em `AUDITORIA_IT018_CONDICOES_LIMITES.md`. O DOC confirma a base do IT-018, mas também contém dados antigos de terminais, fundeadouros e notas práticas que não substituem ITs atuais. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/CURSO PRATICAGEM SETÚBAL.pdf` | Sem processado direto localizado | Sem par direto | Bloco auditado em `AUDITORIA_FONTES_PARCIAIS_SEM_PAR_DIRETO.md`; PDF imagem, OCR parcial possível, sem TXT/JSON diretamente ligado ao conhecimento indexavel atual. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-005_RAM_T_Multiusos_Z1.pdf` | `IT-005_TMS1.txt`; `companions/IT-005_TMS1.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `tms1` conferido. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-006_RAM_T_Multiusos_Z2.pdf` | `IT-006_TMS2.txt`; `companions/IT-006_TMS2.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `tms2` conferido. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-007_RAM_T_Autoeuropa.pdf` | `IT-007_AutoEuropa.txt`; `companions/IT-007_AutoEuropa.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `auto_europa` adicionado/conferido. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-008_RAM_T_Ecoil.pdf` | `IT-008_EcoOil.txt`; `companions/IT-008_EcoOil.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `eco_oil` conferido. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-009_RAM_T_Secil.pdf` | `IT-009_Secil.txt`; `companions/IT-009_Secil.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `secil` conferido. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-010_RAM_T_Tanquisado.pdf` | `IT-010_Tanquisado.txt`; `companions/IT-010_Tanquisado.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `tanquisado` conferido/corrigido. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-011_RAM_T_TERMITRENA ex-Eurominas.pdf` | `IT-011_Termitrena.txt`; `companions/IT-011_Termitrena.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `termitrena` adicionado e nota prática do baixo Termitrena/Teporset acrescentada. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-012_RAM_T_Praias do Sado.pdf` | `IT-012_PraiasSado.txt`; `companions/IT-012_PraiasSado.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `praias_sado` conferido. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-014_RAM_Lisnave.pdf` | `IT-014_Lisnave.txt`; `companions/IT-014_Lisnave.json`; `berth_profiles.json`; `Marcar_manobra_repontos_mare.txt`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; duplicado `id=lisnave` removido e Doca 20/Plataformas 31-33 corrigidas. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-015_RAM_Fundeadouros.pdf` | `IT-015_Fundeadouros.txt`; `companions/IT-015_Fundeadouros.json` | Direto | Bloco auditado em `AUDITORIA_DOCUMENTOS_OPERACIONAIS_GERAIS.md`; coordenadas conferidas. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-016_RAM_Rebocadores.pdf` | `IT-016_Rebocadores.txt`; `companions/IT-016_Rebocadores.json`; `tug_operational_guidance.json` | Direto + prático | Bloco auditado em `AUDITORIA_REBOCADORES_IT016_TUG_GUIDANCE.md`. A tabela IT-016 confere; o JSON de rebocadores é prático e deve ser mantido mesmo se divergir das regras formais. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-017_Pilotagem_Assistida.pdf` | `IT-017_PilotagemAssistida.txt`; `companions/IT-017_PilotagemAssistida.json` | Direto | Bloco auditado em `AUDITORIA_DOCUMENTOS_OPERACIONAIS_GERAIS.md`; removida inferência não documentada sobre o rumo 040. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-018_RAM_Normas_Especiais.pdf` | `IT-018_NormasEspeciais.txt`; `companions/IT-018_NormasEspeciais.json`; `operational_safety_limits.json` | Direto + derivado | Bloco auditado em `AUDITORIA_IT018_CONDICOES_LIMITES.md`; o TXT confere com o PDF Rev. 04 nos pontos operacionais principais. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-029_Regras aplicáveis a manobras-CAIS da SAPEC (TPS e TGL).pdf` | `IT-029_SAPEC.txt`; `companions/IT-029_SAPEC.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `sapec` conferido. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-036_Regras de regulação agulhas_convertido.pdf` | `IT-036_RegulacaoAgulhas.txt`; `companions/IT-036_RegulacaoAgulhas.json` | Direto | Bloco auditado em `AUDITORIA_DOCUMENTOS_OPERACIONAIS_GERAIS.md`; limites exatos tratados como fora da autorização automática documentada. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-038_RAM_Cais_Alstom.pdf` | `IT-038_Alstom.txt`; `companions/IT-038_Alstom.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `alstom` conferido. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-041_Entrada e Saida de Navios.pdf` | `IT-041_EntradaSaida.txt`; `companions/IT-041_EntradaSaida.json` | Direto | Bloco auditado em `AUDITORIA_DOCUMENTOS_OPERACIONAIS_GERAIS.md`; procedimentos de entrada/saída conferidos. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-042_Recomendacoes Navios Canal Norte.pdf` | `IT-042_CanalNorte.txt`; `companions/IT-042_CanalNorte.json` | Direto | Bloco auditado em `AUDITORIA_DOCUMENTOS_OPERACIONAIS_GERAIS.md`; corrigida unidade de `0,15 NM`. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/IT-062 - Cais da Teporset.pdf` | `IT-062_Teporset.txt`; `companions/IT-062_Teporset.json`; `berth_profiles.json`; `Porto_Setubal_Terminais_Cais.txt`; `Marcar_manobra_repontos_mare.txt` | Direto + derivado | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; perfil `teporset` conferido e nota prática do baixo Termitrena/Teporset acrescentada. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/P-13_Planeamento e Gestao Portuaria.pdf` | `P-13_PlaneamentoGestao.txt`; `companions/P-13_PlaneamentoGestao.json` | Direto | Bloco auditado em `AUDITORIA_DOCUMENTOS_OPERACIONAIS_GERAIS.md`; prioridades pelo arco de 8 NM conferidas. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/P-19_Pilotagem.pdf` | `P-19_Pilotagem.txt`; `companions/P-19_Pilotagem.json`; possivelmente `Condicoes_Meteorologicas_Prioridades.txt` | Direto + derivado possível | Bloco auditado em `AUDITORIA_DOCUMENTOS_OPERACIONAIS_GERAIS.md`; aceitação/nomeação/comunicações conferidas. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/Protocolo operacional.pdf` | `IT-016_Rebocadores.txt`; `companions/IT-016_Rebocadores.json`; possivelmente `tug_operational_guidance.json` | Combinado | Bloco auditado em `AUDITORIA_FONTES_PARCIAIS_SEM_PAR_DIRETO.md`; OCR/visual confirma tabela documental `5/6/8 nós`, mantendo resposta geral do sistema normalizada em `6 nós` por validação prática. |
| `PILOTOS INSTRUÇÕES DE TRABALHO/RG_14_Regulamento Interno Pilotagem.pdf` | `RG-14_RegulamentoInterno.txt`; `companions/RG-14_RegulamentoInterno.json` | Direto | Bloco auditado em `AUDITORIA_DOCUMENTOS_OPERACIONAIS_GERAIS.md`; regras operacionais conferidas. |

## 2. Ficheiros em `knowledge` sem original direto localizado em `review`

| Ficheiro em `knowledge` | Tipo | Estado | Observações |
|---|---|---|---|
| `00_abortar.txt`; `companions/00_abortar.json` | TXT + companion | Sem fonte direta em `review` | Bloco auditado em `AUDITORIA_FONTES_PARCIAIS_SEM_PAR_DIRETO.md`; pesquisa local não encontrou original direto, companion limpo. |
| `Condicoes_Meteorologicas_Prioridades.txt`; `companions/Condicoes_Meteorologicas_Prioridades.json` | TXT + companion | Síntese prática / sem fonte única | Bloco auditado em `AUDITORIA_IT018_CONDICOES_LIMITES.md`; prioridades e rebocadores em vento forte alinhados com prática confirmada. |
| `Marcar_manobra_repontos_mare.txt`; `companions/Marcar_manobra_repontos_mare.json` | TXT + companion | Síntese operacional | Bloco auditado em `AUDITORIA_MARCACOES_REPONTOS_MARE.md`; deriva de IT-005/006/008/009/010/011/014/029/062 e prática de marcação. |
| `Porto_Setubal_Terminais_Cais.txt`; `companions/Porto_Setubal_Terminais_Cais.json` | TXT + companion | Síntese derivada | Bloco auditado em `AUDITORIA_TERMINAIS_INVENTARIO_TXT.md`; Doca 20/Plataformas 31-33 e nota Termitrena/Teporset alinhadas. |
| `berth_profiles.json` | JSON estruturado | Derivado | Usa 11 perfis a partir de vários TXT de terminais. Não tem original único. |
| `operational_safety_limits.json` | JSON estruturado | Derivado / prático | Bloco auditado em `AUDITORIA_IT018_CONDICOES_LIMITES.md`; explicita 20/25/30 kt, retoma abaixo de 25 kt e limiar live de visibilidade de 1,0 km confirmado. |
| `tug_operational_guidance.json` | JSON estruturado | Prática operacional | Matriz prática de rebocadores. Deve ser preservado como regra prática, mesmo que haja conflitos formais a classificar. |
| `evals/golden_operational_companion_evals.json` | JSON de avaliação | Derivado | Casos de teste, não fonte documental. |
| `evals/critical_document_companion_evals.json` | JSON de avaliação | Derivado | Casos de teste, não fonte documental. |

## 3. Achados estruturais imediatos para confirmar na próxima fase

1. Resolvido: `berth_profiles.json` tinha dois perfis com `id=lisnave`; o duplicado foi removido.
2. Resolvido: `berth_profiles.json` não tinha perfis explícitos para `IT-007_AutoEuropa.txt` nem `IT-011_Termitrena.txt`; foram adicionados os perfis `auto_europa` e `termitrena`.
3. `CURSO PRATICAGEM SETÚBAL.pdf` parece digitalizado ou imagem: `pdftotext` não extraiu texto útil. Para comparação de conteúdo será preciso OCR ou validação visual/manual. O `Protocolo operacional.pdf` também é imagem, mas a regra de 6 kts já foi validada operacionalmente.
4. Resolvido parcialmente: `Regras Especiais.doc` foi auditado contra `IT-018_NormasEspeciais.txt`. É fonte paralela/legada; confirma a base IT-018, mas não deve sobrepor ITs atuais quando contém valores antigos.
5. `Condicoes_Meteorologicas_Prioridades.txt`, `Marcar_manobra_repontos_mare.txt`, `tug_operational_guidance.json` e `operational_safety_limits.json` devem ser tratados como conhecimento prático/sintético: quando não houver fonte documental única, a validação correta passa por confirmação tua.
6. Resolvido: perguntas truncadas nos companions foram corrigidas em `AUDITORIA_HIGIENE_COMPANIONS_conhecimento indexavel.md`, sem alteração de respostas factuais.

## 4. Ordem recomendada para a validação seguinte

1. Concluído: validar `practice_maneuver_experience.json` contra `Manobras Pratica.xlsx`.
2. Concluído: validar `tug_operational_guidance.json` e `IT-016_Rebocadores.txt` contra IT-016 + protocolo + prática.
3. Concluído: validar `berth_profiles.json` contra os ITs de cada terminal, resolvendo duplicação LISNAVE e ausências AutoEuropa/Termitrena.
4. Concluído: validar `Marcar_manobra_repontos_mare.txt` contra IT-005, IT-006, IT-008, IT-009, IT-010, IT-011, IT-014, IT-029, IT-062 e prática.
5. Concluído: validar `IT-018_NormasEspeciais.txt`, `Regras Especiais.doc`, `Condicoes_Meteorologicas_Prioridades.txt` e `operational_safety_limits.json` em conjunto.
6. Concluído: validar TXT/companions dos terminais IT-005, IT-006, IT-007, IT-008, IT-009, IT-010, IT-011, IT-012, IT-014, IT-029, IT-038 e IT-062, incluindo `Porto_Setubal_Terminais_Cais.txt`.
7. Concluído: validar restantes TXT/companions por documento: IT-015, IT-017, IT-036, IT-041, IT-042, P-13, P-19, RG-14.
