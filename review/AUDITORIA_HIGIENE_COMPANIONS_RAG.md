# Auditoria - higiene dos companions RAG

Data da auditoria: 2026-05-01

## Objetivo

Validar perguntas truncadas nos ficheiros `knowledge/companions/*.json`, porque
estas perguntas são usadas como contexto de recuperação e podem degradar o RAG
mesmo quando a resposta factual está correta.

## Correções aplicadas

Foram completadas perguntas truncadas, alinhando-as com as perguntas completas
existentes nos TXT correspondentes:

- `companions/AdmiraltyPilot_PortoSetubal.json`
- `companions/IT-006_TMS2.json`
- `companions/IT-007_AutoEuropa.json`
- `companions/IT-008_EcoOil.json`
- `companions/IT-009_Secil.json`
- `companions/IT-012_PraiasSado.json`
- `companions/IT-014_Lisnave.json`
- `companions/IT-029_SAPEC.json`
- `companions/Marcar_manobra_repontos_mare.json`

Também foram reforçadas algumas `keywords` óbvias quando a palavra estava na
pergunta completa mas não no companion truncado, por exemplo `auto/europa`,
`secil`, `noite`, `tps`, `carochas`, `1,0`, `0,5`.

## Resultado

- Não foram alteradas regras operacionais nem respostas factuais.
- As perguntas FAQ dos companions deixam de ter entradas truncadas sem `?`.
- As correções seguem o texto já existente nos TXT processados, não introduzem
  interpretação nova.

## Validações

- `jq empty` nos companions alterados.
- Varredura global: nenhuma pergunta FAQ em `knowledge/companions/*.json`
  ficou sem ponto de interrogação final.
- `git diff --check`
- `python3 scripts/run_rag_evals.py --knowledge-dir knowledge --fail-on-fail`
- `python3 -m pytest -q`
