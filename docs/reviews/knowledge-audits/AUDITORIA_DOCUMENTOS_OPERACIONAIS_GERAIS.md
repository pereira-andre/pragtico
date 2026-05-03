# Auditoria - documentos operacionais gerais

Data da auditoria: 2026-05-01

## Ficheiros revistos

- `knowledge/IT-015_Fundeadouros.txt` e companion
- `knowledge/IT-017_PilotagemAssistida.txt` e companion
- `knowledge/IT-036_RegulacaoAgulhas.txt` e companion
- `knowledge/IT-041_EntradaSaida.txt` e companion
- `knowledge/IT-042_CanalNorte.txt` e companion
- `knowledge/P-13_PlaneamentoGestao.txt` e companion
- `knowledge/P-19_Pilotagem.txt` e companion
- `knowledge/RG-14_RegulamentoInterno.txt` e companion

## Fontes originais usadas

- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-015_RAM_Fundeadouros.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-017_Pilotagem_Assistida.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-036_Regras de regulação agulhas_convertido.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-041_Entrada e Saida de Navios.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/IT-042_Recomendacoes Navios Canal Norte.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/P-13_Planeamento e Gestao Portuaria.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/P-19_Pilotagem.pdf`
- `review/PILOTOS INSTRUÇÕES DE TRABALHO/RG_14_Regulamento Interno Pilotagem.pdf`

## Resultado

1. `IT-015_Fundeadouros.txt` confere com o PDF nos vértices WGS84/Datum 73
   dos quatro fundeadouros: Fundeadouro Norte, Tróia 1.ª Zona, Tróia 2.ª Zona
   e Fundeadouro para Embarcações de Tráfego Local ao Serviço Portuário.
   A nota prática sobre preferir Fundeadouro Sul/Tróia para navios de grande
   calado mantém-se como conhecimento operacional, não como regra documental.

2. `IT-017_PilotagemAssistida.txt` confere com o PDF nos pontos principais:
   aceitação do Comandante, OUTÃO, VHF 10, aquisição VTS a 4 milhas, enfiamento
   a 2 milhas, rumo 040, velocidade não inferior a 10 nós, 200 manobras para
   Piloto experiente e proibição de cruzamento sem Piloto a bordo.
   Foi removida uma inferência indevida: o TXT já não classifica o rumo 040 como
   magnético ou verdadeiro, porque o PDF não o qualifica.

3. `IT-036_RegulacaoAgulhas.txt` confere com o PDF nos limites principais:
   - reponto de maré: `LOA < 250 m`;
   - corrente em marés mortas: `PM < 3,0 m` e `LOA < 225 m`;
   - corrente em marés vivas: `LOA < 120 m`;
   - não efetuar RA com navios fundeados num raio de `0,7 NM`;
   - não efetuar RA de noite com `LOA > 225 m`.

   Correção feita: o TXT passou a distinguir entre o conjunto permitido por
   `LOA < ...` e as proibições expressas por `LOA > ...`. Para valores exatamente
   no limite (`250 m`, `225 m`, `120 m`), o sistema deve tratar como fora da
   autorização automática documentada e encaminhar para validação do Piloto
   Coordenador, em vez de apresentar uma proibição literal que o PDF não escreve.

4. `IT-041_EntradaSaida.txt` confere com o PDF:
   - VTS Portuário a montante do arco de `4 NM` centrado na Baliza n.º 2;
   - VTS Costeiro a oeste desse arco;
   - área de Pilotagem Obrigatória até `5 NM` centradas no farol do Outão;
   - entrada: fora das 4 milhas até situação regularizada; referência de 3 milhas
     da barra, nunca menos de 2 milhas, no período de 1 hora antes de receber
     Piloto;
   - transferência formal em VHF 14 por iniciativa do Piloto;
   - saída: Baliza 2/Boia 1, Outão/Boia 3 em condições normais com acordo do
     Comandante, ou entre Outão e João Farto em Pilotagem Assistida por mau tempo.

5. `IT-042_CanalNorte.txt` confere com o PDF nas três recomendações:
   dois rebocadores à popa quando o número designado for suficiente, evitar
   máquina a ré em canal/curva e cumprir `0,15 NM` da linha Norte da costa.
   Correção feita: a distância passou de "0,15 minutos de milha" para
   "0,15 milhas náuticas", mantendo a conversão aproximada para `278 m`.

6. `P-13_PlaneamentoGestao.txt` confere com o PDF nos pontos operacionais:
   âmbito/exclusões, JUL, responsabilidades, análise de processo, prioridade de
   acostagem pelo arco de `8 NM` centrado na Baliza n.º 2, autorização pelo DePCP
   e definição do local de acostagem na JUL.

7. `P-19_Pilotagem.txt` confere com o PDF nos pontos operacionais:
   aceitação pelo PC, nomeação normalmente com 2 horas, análise das condições
   pelo Piloto, comunicações ao OVTS, registo JUL e comunicação de acidentes ao
   Diretor de Pilotagem, OVTS, Capitania e GAMA, com prazos de 6 h / 48 h.

8. `RG-14_RegulamentoInterno.txt` confere com o PDF nos pontos operacionais:
   serviço permanente do PC, rendição às 09h00, horários de expediente, VHF 14/13,
   aviso mínimo de 1 hora, regras de requisição/distribuição, escala de rio e
   protocolo de emergências.

## Correções aplicadas

- `IT-017_PilotagemAssistida.txt`: removida qualificação não documentada do
  rumo 040.
- `companions/IT-017_PilotagemAssistida.json`: corrigida pergunta truncada.
- `IT-036_RegulacaoAgulhas.txt`: clarificados limites exatos e distinção entre
  autorização automática e proibição expressa.
- `companions/IT-036_RegulacaoAgulhas.json`: corrigida regra noturna e pergunta
  truncada.
- `IT-042_CanalNorte.txt`: corrigida a unidade de `0,15 NM`.
- `companions/IT-042_CanalNorte.json`: corrigida a mesma unidade.

## Validações

- `jq empty` nos companions alterados.
- Comparação direta por `pdftotext` dos oito PDFs originais.
- `git diff --check`
- `python3 scripts/run_conhecimento indexavel_evals.py --knowledge-dir knowledge --fail-on-fail`
- `python3 -m pytest -q`
