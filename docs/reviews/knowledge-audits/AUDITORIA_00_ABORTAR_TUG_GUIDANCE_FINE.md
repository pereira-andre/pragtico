# Auditoria - 00_abortar e revisao fina de rebocadores

Data da auditoria: 2026-05-01

## 1. Fonte original do 00_abortar

Ficheiros revistos:

- `knowledge/00_abortar.txt`
- `knowledge/companions/00_abortar.json`
- `review/Notas Pilotagem.docx`
- `review/PILOTOS INSTRUCOES DE TRABALHO/P-19_Pilotagem.pdf`
- `review/PILOTOS INSTRUCOES DE TRABALHO/RG_14_Regulamento Interno Pilotagem.pdf`
- restantes ficheiros PDF/DOCX pesquisaveis em `review`

Resultado:

- Nao foi localizada fonte original direta para `knowledge/00_abortar.txt` dentro de `review`.
- A pesquisa por frases distintivas do texto processado nao encontrou correspondencia fora do proprio ficheiro em `knowledge`.
- O codigo da aplicacao contem fluxos para cancelar/abortar manobras, mas isso valida o comportamento da app, nao a origem documental deste texto.
- O conteudo deve ficar classificado como conhecimento JUL/operacional local sem validacao documental local, ate aparecer a circular, email, instrucao JUL ou outro original.

Conteudo operacional atualmente em `00_abortar`:

- Quando uma manobra pedida pelo agente e marcada pelo Piloto Coordenador nao se realiza conforme previsto depois de o Piloto ter ido a bordo, deve usar-se `ABORTAR`.
- Nestas situacoes, `CANCELAR` e `ANULAR` deixam de ser usados.
- `ABORTAR` permite registo de pilotagem pelo Piloto, pontos de relato pelo VTS e novo pedido pelo agente.
- Exemplos: saida que afinal tem de fundear/atracar; manobra que nao se inicia por meteorologia, avaria ou outro motivo com Piloto ja a bordo.

Decisao recomendada:

- Manter o ficheiro no conhecimento indexavel, mas sinalizado mentalmente como regra operacional local sem original presente.
- Se aparecer a fonte original, substituir esta classificacao por validacao documental e comparar linha a linha.

## 2. Como o tug_operational_guidance.json e usado

`knowledge/tug_operational_guidance.json` nao e apenas texto para conhecimento indexavel. O sistema tem um caminho deterministico:

- `domain/tug_guidance.py` carrega o JSON.
- O codigo identifica perguntas sobre rebocadores.
- Extrai sinais da pergunta: tipo de navio, entrada/saida, vento, LOA, boca, bowthruster e Lisnave.
- Se encontrar regra aplicavel, cria uma fonte operacional chamada `operational_tug_guidance`.
- `core/operational_sources.py` usa essa fonte para responder diretamente, por exemplo "Recomendo 3 rebocadores grandes".

Isto significa que alteracoes neste JSON mudam diretamente respostas do sistema, nao apenas a pesquisa documental.

## 3. Pontos que ficam alinhados com a tua orientacao

Estes pontos ja refletem o que confirmaste durante a revisao:

- A pratica de rebocadores deve ser mantida mesmo quando for mais conservadora do que as regras formais.
- A IT-016 continua a servir para minimos formais, DWT, cargas perigosas, estado carregado/vazio e thrusters, mas nao deve reduzir uma recomendacao pratica mais conservadora.
- A resposta geral sobre estabelecimento do cabo pode usar `6 nos sobre a agua`, embora o protocolo documental tenha a tabela `5/6/8 nos` para proa/costado/popa.
- Se faltarem dados importantes, o sistema deve responder por cenarios e dizer exatamente o que falta confirmar.
- Com bowthruster operacional em navios grandes, a regra pratica e favorecer rebocador a popa para controlar a popa.
- Sem bowthruster, se houver dois rebocadores, a regra pratica normal e `1 a proa + 1 a popa`.
- Ro-Ro com dois rebocadores podem operar com um a popa e outro ao costado, em efeito tipo push-pull.
- A orientacao deve assumir rebocadores convencionais e ser conservadora quando houver duvida.
- Visibilidade live igual ou inferior ao limiar tecnico de `1,0 km` deve ser tratada pelo sistema como visibilidade reduzida/nevoeiro operacional.

## 4. Matriz pratica atual no JSON

### Ro-Ro

| Vento | Manobra | Recomendacao atual |
| --- | --- | --- |
| Sul / SW como caso critico; W so em TMS2/Autoeuropa | Entrada | 2 rebocadores |
| Sul / SW como caso critico; W so em TMS2/Autoeuropa | Saida | 2 rebocadores |
| Norte; E so em TMS2/Autoeuropa | Entrada | 3 rebocadores |
| Norte; E so em TMS2/Autoeuropa | Saida | 2 rebocadores |

Excecao confirmada: Ro-Ro com mais de `220 m` e vento Norte forte deve considerar `4 rebocadores grandes` em casos extremos.

### Graneleiro / reefer / estilha / contentores grande

| Vento | Manobra | Recomendacao atual |
| --- | --- | --- |
| Sul / SW como caso critico; W so em TMS2/Autoeuropa | Entrada | 4 rebocadores grandes |
| Sul / SW como caso critico; W so em TMS2/Autoeuropa | Saida | 2 rebocadores grandes |
| Norte; E so em TMS2/Autoeuropa | Entrada | 4 rebocadores grandes |
| Norte; E so em TMS2/Autoeuropa | Saida | 3 rebocadores grandes |

Excecao confirmada: saida com vento Norte forte deve considerar `4 rebocadores grandes`, exceto estaleiro/Lisnave, Tanquisado e Eco-Oil, que ficam para avaliacao caso a caso.

Regra lateral confirmada para cais atravessados a corrente: na Tanquisado, saida com vento E forte deve considerar `1 rebocador a empurrar ao costado` durante a largada dos cabos; na Eco-Oil, a situacao equivalente e vento W forte. Esta regra e de posicionamento/seguranca, nao uma equivalencia W/E para Norte/Sul.

### Sem bowthruster

| Condicao | Recomendacao atual |
| --- | --- |
| LOA < 120 m | pelo menos 1 rebocador, normalmente pequeno |
| LOA < 120 m e calado >= 8 m | pelo menos 1 rebocador grande, cerca de 35 t |
| 120 m <= LOA <= 150 m | pelo menos 2 rebocadores grandes |
| LOA > 150 m | pelo menos 3 rebocadores grandes |

### Lisnave

| Condicao | Recomendacao atual |
| --- | --- |
| LOA <= 100 m | 3 rebocadores |
| LOA <= 100 m para eclusa/Hidrolift | 4 rebocadores |
| 100 m < LOA <= 150 m | 3 rebocadores |
| 150 m < LOA <= 199 m | 4 rebocadores |
| 200 m <= LOA <= 250 m | 5 rebocadores |
| LOA > 250 m | 6 rebocadores |

## 5. Decisoes confirmadas pelo utilizador

1. Lisnave `100 m < LOA <= 150 m`: usar `3 rebocadores`. Quatro rebocadores sao excessivos para estes navios leves e podem dificultar a manobra. Acima de `150 m`, manter `4 rebocadores`.
2. Equivalencia `W = Sul fraco` e `E = Norte fraco`: aplicar apenas no contexto TMS2/Autoeuropa, cuja orientacao aproximada `120/300 graus` torna W/E quase paralelo mas ligeiramente atracante/desatracante. Nao aplicar automaticamente aos cais do Canal Sul, de Tanquisado ate Teporset.
3. Nevoeiro seguido de SW forte: manter como conhecimento local/contextual de Setubal, mas nao como regra operacional para dimensionar rebocadores.
4. Ro-Ro: matriz base mantida. Excecao acrescentada: Ro-Ro com mais de `220 m` e vento Norte forte deve considerar `4 rebocadores grandes` em casos extremos.
5. Graneleiro/reefer/estilha/contentores grande: em saida com vento Norte forte, considerar `4 rebocadores grandes`; excecoes a avaliar caso a caso no estaleiro/Lisnave, Tanquisado e Eco-Oil, porque a mare e atravessada e muitos navios estao mais leves.
6. Regra sem bowthruster: confirmada como correta.
7. Navios ate `120 m`: normalmente rebocadores pequenos; acima de `120 m`, assumir grandes salvo indicacao operacional em contrario. Excecao: navio ate `120 m` com calado `>= 8 m` deve usar rebocador grande de cerca de `35 t`, nao pequeno de `25 t`.
8. Cais atravessados a corrente: W/E forte e prejudicial lateralmente. Para saida da Tanquisado com vento E forte, usar `1 rebocador a empurrar ao costado` na largada dos cabos; para Eco-Oil, aplicar a logica oposta com vento W forte.

## 6. Validacao tecnica executada nesta etapa

- Pesquisa local por fonte original do `00_abortar` em nomes de ficheiros e conteudo textual.
- Leitura do `00_abortar.txt` e companion.
- Leitura do `tug_operational_guidance.json`.
- Revisao do caminho runtime em `domain/tug_guidance.py` e `core/operational_sources.py`.
- Atualizacao posterior de `knowledge/tug_operational_guidance.json`, `domain/tug_guidance.py` e evals/testes para refletir as decisoes confirmadas.
- Inclusao dos cenarios criticos de rebocadores na pagina admin `Testes operacionais` (`/admin/tests`), modulo `Sistema operacional`.
- `python3 -m pytest tests/test_tug_guidance.py -q`: `12 passed`.
- Evals golden operacionais: `46/46 passed`.
- `python3 scripts/run_conhecimento indexavel_evals.py --knowledge-dir knowledge --fail-on-fail`: `8/8` evals passaram.
- `python3 -m pytest -q`: `97 passed, 6 subtests passed`.
