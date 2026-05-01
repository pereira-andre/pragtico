# Auditoria - fontes parciais e ficheiros sem par direto

Data da auditoria: 2026-05-01

## Ficheiros revistos

- `knowledge/AdmiraltyPilot_PortoSetubal.txt` e companion
- `knowledge/Tarifas_APSS_2024.txt` e companion
- `knowledge/00_abortar.txt` e companion
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/Protocolo operacional.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/CURSO PRATICAGEM SETÚBAL.pdf`

## Resultado

1. `AdmiraltyPilot_PortoSetubal.txt` foi comparado com o PDF NP 67, capítulo
   Porto de Setúbal. O ficheiro está corretamente marcado como fonte histórica
   de 2004/2005 e não deve substituir as IT/APSS atuais.

   Correções aplicadas:
   - Ponta do Adoxe: `1¼ NM` a este do Outão, não `1½ NM`;
   - naufrágio perigoso: `5½ NM` ESE do Marco Luminoso n.º 2, não `5¼ NM`;
   - Cabo de Ares: `2½ NM` a leste, não `2¼ NM`;
   - Marco Luminoso n.º 5: `5½ cabos` a este do Outão, não `5¼`;
   - João Farto e Albarquel: `1½ NM` NE, não `1¼ NM`;
   - Canal Norte interior: `2¼ NM`, não `2½ NM`;
   - Cais Novo da Secil: `1¼ NM` a oeste da Ponta do Adoxe, não `1½ NM`;
   - `gas-freeing` corrigido para `desgaseificação`, não `gaseificação`;
   - summary do companion reescrito para deixar claro que é fonte histórica.

2. `Tarifas_APSS_2024.txt` confere com o PDF nos pontos tarifários principais:
   - fórmula de pilotagem `T = UP × √GT`;
   - UP `3,3628` para correr ao longo do cais e `9,2578` para outros serviços;
   - pilotagem à ordem `74,6432 €/h` + 25% da taxa;
   - reduções/cancelamentos de pilotagem;
   - TUP por tipo de navio e reduções de linha regular;
   - taxa indireta de resíduos `0,0088 €/GT`.

   Correção aplicada:
   - o cabeçalho deixou de afirmar simplesmente "em vigor"; passou a dizer que
     o Artigo 32.º indica entrada em vigor em `1 de janeiro de 2024`, sendo o
     original um `projeto de regulamento`.

3. `00_abortar.txt` não tem fonte original direta localizada em `review`.
   A pesquisa por frases-chave não encontrou correspondência fora do próprio
   ficheiro processado. O conteúdo fica classificado como conhecimento JUL
   operacional sem validação documental local.

   Correção aplicada:
   - summary e key points do companion foram limpos porque estavam truncados.

4. `Protocolo operacional.pdf` é imagem/scan de uma página. O OCR/validação
   visual confirma a tabela original de velocidades para estabelecimento de cabo:
   - rebocador à proa: `5 nós` sobre a água;
   - rebocador ao costado: `6 nós` sobre a água;
   - rebocador à popa: `8 nós` sobre a água.

   Como o utilizador confirmou que quer a resposta geral do bot normalizada em
   `6 kts` / `6 nós sobre a água`, o conhecimento foi ajustado para distinguir:
   - tabela documental do protocolo;
   - normalização prática conservadora usada pelo bot.

5. `CURSO PRATICAGEM SETÚBAL.pdf` é um PDF digitalizado de 75 páginas. O
   `pdftotext` não extrai texto útil; OCR parcial das primeiras páginas mostra
   conteúdo geral de curso/descrição portuária, mas não há TXT/JSON em
   `knowledge` com correspondência direta identificada. Fica como fonte de
   contexto não indexada diretamente, pendente de OCR completo se for para
   integrar no RAG.

## Validações

- `pdftotext` para PDFs com texto pesquisável.
- Renderização visual/OCR do `Protocolo operacional.pdf`.
- OCR amostral das primeiras páginas do `CURSO PRATICAGEM SETÚBAL.pdf`.
- `jq empty` nos JSON/companions.
- `python3 -m py_compile domain/berth_profiles.py domain/tug_guidance.py domain/operational_safety.py core/operational_sources.py scripts/generate_practice_maneuver_experience.py`
- `git diff --check`
- `python3 scripts/run_rag_evals.py --knowledge-dir knowledge --fail-on-fail`
- `python3 -m pytest -q`
