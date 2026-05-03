# Auditoria - Rebocadores, IT-016 e tug_operational_guidance

Data da auditoria: 2026-05-01

## Ficheiros revistos

- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-016_RAM_Rebocadores.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/Protocolo operacional.pdf`
- `knowledge/IT-016_Rebocadores.txt`
- `knowledge/companions/IT-016_Rebocadores.json`
- `knowledge/tug_operational_guidance.json`
- `knowledge/Notas_Pilotagem.txt`
- `knowledge/evals/golden_operational_companion_evals.json`

## Resultado

1. A tabela oficial da IT-016 foi conferida contra o PDF original. A conversão
   para `knowledge/IT-016_Rebocadores.txt` está coerente quanto a:
   - faixas de DWT;
   - distinção entre cargas perigosas e outras cargas;
   - códigos `G`, `p`, `GG`, `GGp`, `GGG`, etc.;
   - carácter obrigatório apenas para navios de cargas perigosas com LOA superior
     a 70 m;
   - pressupostos da tabela;
   - tabela específica de rebocadores e calados na barra para navios com destino
     à Lisnave-Mitrena.

2. Foi detetada uma incoerência no protocolo operacional:
   - a revisão visual/OCR do `Protocolo operacional.pdf` confirma que o original
     tem tabela por posição: `5 nós` à proa, `6 nós` ao costado e `8 nós` à popa;
   - a validação operacional fornecida pelo André é manter a resposta geral do
     sistema em `6 kts` / `6 nós sobre a água`, por ser a referência prática mais
     útil e conservadora;
   - o TXT, companion, eval e notas práticas foram ajustados para distinguir a
     tabela documental da normalização operacional usada pelo sistema.

3. `knowledge/tug_operational_guidance.json` foi validado como camada prática,
   não como transcrição literal da IT-016. Deve continuar a ter prioridade como
   regra prática conservadora, em conjunto com a IT-016 e com a experiência
   histórica, porque cobre riscos que a regra formal não cobre bem, incluindo
   limitações reais dos rebocadores e manutenção/idade dos meios.

4. As notas práticas mantêm o critério conservador:
   - protocolo original: tabela por posição `5/6/8 nós` sobre a água;
   - prática do sistema: tratar `6 nós` como referência geral e limite conservador;
   - rebocadores convencionais só travam eficazmente a cerca de 3 nós;
   - rebocadores com azipode podem travar até 6 nós em condições adequadas;
   - o `Lisboa` continua assinalado como pouco eficaz à proa a 6 nós.

5. Foi acrescentada a regra prática de posicionamento dos rebocadores:
   - rebocador à proa normalmente só quando não há bowthruster ou quando a
     manobra precisa claramente desse equilíbrio;
   - com dois rebocadores e sem bowthruster, usar normalmente um à proa e um
     à popa para equilibrar forças e impedir que a popa feche demasiado para
     o cais;
   - com bowthruster operacional em navios grandes, colocar por norma o
     rebocador à popa para segurar/controlar a popa;
   - em Ro-Ro com dois rebocadores, pode usar-se um à popa e outro ao costado
     a empurrar, criando um efeito tipo push-pull;
   - esta regra é especialmente relevante para rebocadores convencionais.

6. Revisão fina posterior com validação operacional do André:
   - Lisnave `100 m < LOA <= 150 m`: `3 rebocadores`; acima de `150 m`,
     manter `4 rebocadores`;
   - `W = Sul fraco` e `E = Norte fraco` só são aplicados automaticamente no
     contexto TMS2/Autoeuropa; não são extrapolados para o Canal Sul;
   - nevoeiro seguido de SW forte fica como conhecimento local contextual, não
     como regra para dimensionar rebocadores;
   - Ro-Ro com mais de `220 m` e vento Norte forte: considerar `4 rebocadores`
     em casos extremos;
   - graneleiro/reefer/estilha/contentores grande a sair com vento Norte forte:
     considerar `4 rebocadores`, exceto estaleiro/Lisnave, Tanquisado e Eco-Oil,
     que ficam para avaliação caso a caso;
   - nos cais atravessados a corrente, W/E forte fica tratado como risco lateral
     específico: saída da Tanquisado com vento E forte e saída da Eco-Oil com
     vento W forte devem considerar `1 rebocador a empurrar ao costado` durante
     a largada dos cabos;
   - navio até `120 m` com calado `>= 8 m`: preferir rebocador grande de cerca
     de `35 t`, não pequeno de `25 t`.

## Ficheiros corrigidos

- `knowledge/IT-016_Rebocadores.txt`
- `knowledge/companions/IT-016_Rebocadores.json`
- `knowledge/Notas_Pilotagem.txt`
- `knowledge/evals/golden_operational_companion_evals.json`
- `knowledge/tug_operational_guidance.json`
- `core/operational_sources.py`
- `core/operational_test_suite.py`
- `domain/tug_guidance.py`
- `templates/admin_operational_tests.html`
- `tests/test_tug_guidance.py`
- `review/AUDITORIA_00_ABORTAR_TUG_GUIDANCE_FINE.md`

## Validações executadas

- `jq empty knowledge/companions/IT-016_Rebocadores.json knowledge/tug_operational_guidance.json knowledge/evals/golden_operational_companion_evals.json`
- `python3 -m py_compile domain/tug_guidance.py core/operational_sources.py`
- carregamento direto de `knowledge/tug_operational_guidance.json` pelo runtime
- teste de geração de fonte prática para uma pergunta Ro-Ro / vento Norte
- `python3 -m pytest tests/test_tug_guidance.py -q`
- validação isolada do módulo `Sistema operacional` da página `/admin/tests`
- avaliação de `knowledge/evals/golden_operational_companion_evals.json`
- `python3 scripts/run_conhecimento indexavel_evals.py --knowledge-dir knowledge --fail-on-fail`
- `python3 -m pytest -q`

Resultado das validacoes locais de recuperacao documental: `8/8` passaram.
Resultado das evals golden operacionais: `46/46` passaram.
Resultado dos testes Python: `97 passed, 6 subtests passed`.

## Pendências

Não ficou nenhuma dúvida bloqueante neste bloco depois da revisão fina. A matriz
prática de `tug_operational_guidance.json` continua a dever ser validada como
experiência operacional do André, não como comparação direta contra a IT-016.
