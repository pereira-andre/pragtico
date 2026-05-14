# Fila de Revisão QA - 2026-05-13

Este ficheiro contém os casos que a auditoria marcou como `review`.
Objetivo: responderes só ao essencial para cada caso, sem precisares mexer no JSON.

Para cada pergunta, escolhe uma decisão e escreve a resposta operacional certa em 1-3 linhas quando souberes.

Decisões sugeridas:
- `correto`: a resposta/factos esperados estão bons; falta só melhorar suporte no knowledge.
- `errado`: a pergunta ou expectativa está errada e deve ser corrigida/removida.
- `falta_knowledge`: a resposta está certa, mas a knowledge ainda não tem base suficiente.
- `live_ou_comando`: não deve entrar na memória QA estática.
- `duplicado_remover`: caso redundante ou sem valor.

Total para rever: **30**

## 1. Entrada para a Alstom desde a Barra com vento 15 kts pode avançar?

- Grupo: `Diagnostico operacional`
- Risco: `Critico`
- Origem: `railway_150`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- Local: ALSTOM
- atracam apenas por estibordo
- reponto de preia-mar
- 1h30
- inferior a 15 kt
- atinge/excede o limite local

**Factos com suporte fraco/ausente:**
- `partial` score `0.651`: atinge/excede o limite local (doc: `AdmiraltyPilot_PortoSetubal.txt`; evidência: expected:atinge limite local | context:15 barra desde entrada operacional vento)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Não deve avançar: na ALSTOM o vento tem de ser inferior a 15 kt; vento igual a 15 kt já bloqueia a manobra. A entrada desde a Barra deve ser marcada 1h30 antes da preia-mar para chegar no reponto de preia-mar, e a manobra deve ser diurna.
```

- O que devo fazer depois: manter o caso; reforçar no audit/knowledge que 15 kt já é limite bloqueante, incluindo rajada nesse limite.

---

## 2. Marquei manobra de entrada para a Secil E as 1925. Está correto?

- Grupo: `Diagnostico operacional`
- Risco: `Critico`
- Origem: `railway_150`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Local: SECIL
- Doca/cais: SECIL E/Este
- Hora referida: 19:25

**Factos com suporte fraco/ausente:**
- `partial` score `0.647`: Hora referida: 19:25 (doc: `Notas_Pilotagem.txt`; evidência: expected:19 25 hora | context:entrada esta manobra operacional secil)

**Pontos proibidos atuais:**
- Local: LISNAVE
- 6 rebocador
- nevoeiro

**A tua revisão:**

- Decisão: falta_knowledge
- Resposta operacional certa:

```text
Não dá para confirmar só com a hora 19:25. Na Secil E/Este é preciso saber se estamos em marés vivas, qual é o reponto aplicável e de onde vem o navio; entradas de fora da Barra/Fundeadouro Norte marcam-se normalmente 30-45 min antes do reponto, e de Tróia/outro cais 45 min-1h antes.
```

- O que devo fazer depois: corrigir a expectativa para exigir reponto, proveniência e marés vivas antes de validar a hora.

---

## 3. Tenho um navio para entrar no hidrolift no preia-mar das 20:03. O navio tem 45 m de boca, pode manobrar?

- Grupo: `Diagnostico operacional`
- Risco: `Critico`
- Origem: `railway_150`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Bloqueio dimensional
- boca maxima 32 m
- Boca: 45 m

**Factos com suporte fraco/ausente:**
- `unsupported` score `0.0`: Bloqueio dimensional (doc: `sem documento`; evidência: sem evidência)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Não pode manobrar para o Hidrolift: as Plataformas 31/32/33 da LISNAVE têm acesso por Hidrolift com boca máxima admissível de 32 m. Um navio com 45 m de boca excede esse limite dimensional, mesmo que a manobra esteja no reponto de preia-mar.
```

- O que devo fazer depois: manter o caso; melhorar o suporte lexical para "bloqueio dimensional" no audit.

---

## 4. A fonte de shiphandling prático cobre bow thruster e squat?

- Grupo: `Fontes`
- Risco: `Critico`
- Origem: `railway_150`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- Shiphandling_Pratico.txt
- bow thruster
- squat

**Factos com suporte fraco/ausente:**
- `partial` score `0.554`: Shiphandling_Pratico.txt (doc: `companions/Notas_Pilotagem.json`; evidência: expected:pratico txt | context:pratico)

**A tua revisão:**

- Decisão: errado
- Resposta operacional certa:

```text
A fonte prática de shiphandling cobre bow thruster, pivot point, uso de rebocadores e ferro/emergência. Não vejo squat como tópico dessa fonte; o squat aparece antes na SAPEC/IT-029, associado à velocidade inferior a 5 nós no canal de acesso.
```

- O que devo fazer depois: corrigir/remover a expectativa de squat nesta pergunta ou mudar a pergunta para a fonte SAPEC. Aqui temos de separar o que é ciencia e ai o shiphandling é mestre, e o que é regra para esse local 5 kts é o ideal.

---

## 5. Se estiver 31 kts de vento, posso sair com 4 rebocadores?

- Grupo: `Limites de seguranca`
- Risco: `Critico`
- Origem: `railway_150`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- 31 kt
- manobras ficam suspensas
- mais rebocadores não anula
- menos de 25 kt

**Factos com suporte fraco/ausente:**
- `partial` score `0.671`: mais rebocadores não anula (doc: `operational_safety_limits.json`; evidência: expected:mais nao rebocadores | context:4 estiver kts limites rebocadores seguranca vento)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Não. Com vento sustentado ou rajada superior a 25 kt, as manobras ficam suspensas por segurança; 31 kt já fica muito para lá do limite praticável. Ter 4 rebocadores não anula a suspensão, e a retoma após suspensão por vento só deve acontecer abaixo de 25 kt.
```

- O que devo fazer depois: manter o caso; reforçar que "mais rebocadores" não substitui o limite de suspensão. Limite é 25 kts! mais que isso já cancela tudo!

---

## 6. De acordo com a próxima maré, a que horas marco uma entrada para a SECIL?

- Grupo: `Marés, Repontos e Meteorologia`
- Risco: `Crítico`
- Origem: `complementary_questions`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- 30-45 min
- 45 min a 1 h
- não uses 15 min

**Factos com suporte fraco/ausente:**
- `partial` score `0.666`: não uses 15 min (doc: `companions/Marcar_manobra_repontos_mare.json`; evidência: expected:15 min nao | context:entrada horas mare repontos secil)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Para uma entrada para a SECIL, marcamos 30-45 min antes do reponto se vier de fora da Barra ou Fundeadouro Norte; se vier de Tróia ou de outro cais, marca 45 min a 1h antes.
```

- O que devo fazer depois: manter o caso e reforçar que 15 min é só para saídas da Secil.

---

## 7. Da Bóia 12 CS para a Lisnave a 6 nós, saída às 10:00, qual ETA?

- Grupo: `Percursos e distancias`
- Risco: `Critico`
- Origem: `railway_150`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Canal Sul para LISNAVE
- 1,0 milha náutica
- A 6,0 kt
- ETA ao destino: 10:10

**Factos com suporte fraco/ausente:**
- `partial` score `0.608`: ETA ao destino: 10:10 (doc: `companions/Nocoes_Basicas_Navegacao_Unidades.json`; evidência: expected:10 eta | context:10 12 6 eta)

**A tua revisão:**

- Decisão: falta_knowledge
- Resposta operacional certa:

```text
Se a distância operacional assumida da Bóia 12CS à LISNAVE for 1,0 NM, a 6 kt demora 10 minutos; saída às 10:00 dá ETA 10:10. Antes de promover, convém confirmar/explicitar essa distância na fonte de rotas, porque a conta está certa mas a pernada direta não está suficientemente clara na knowledge.
```

- O que devo fazer depois: manter como caso útil, mas reforçar a distância 12CS-LISNAVE na knowledge ou ajustar a pergunta à pernada documentada.

---

## 8. Um Graneleiro de 190m a sair do TMS2 com vento norte forte, quantos rebocadores leva?

- Grupo: `Rebocadores e vento`
- Risco: `Critico`
- Origem: `railway_150`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Recomendo 4 rebocadores grandes
- vento Norte forte
- Tanquisado
- Eco-Oil

**Factos com suporte fraco/ausente:**
- `partial` score `0.681`: Recomendo 4 rebocadores grandes (doc: `tug_operational_guidance.json`; evidência: expected:4 grandes rebocadores | context:forte graneleiro norte rebocadores sair tms2 vento)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Para um graneleiro de 190 m a sair do TMS2 com vento Norte forte, a recomendação prática é considerar 4 rebocadores grandes (também para TMS 1). A exceção de avaliar caso a caso aplica-se a Lisnave, Tanquisado e Eco-Oil.
```

- O que devo fazer depois: manter o caso, mas deixar claro que Tanquisado/Eco-Oil são exceções e não o destino deste cenário.

---

## 9. Em rumos cruzados, quem deve manobrar quando vejo o outro por estibordo?

- Grupo: `COLREG / RIEAM`
- Risco: `Alto`
- Origem: `complementary_questions`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- Regra 15
- cede passagem
- estibordo

**Factos com suporte fraco/ausente:**
- `partial` score `0.493`: cede passagem (doc: `RIEAM_COLREG_Regras_Estrada.txt`; evidência: expected:passagem | context:colreg cruzados deve estibordo manobrar outro quando quem rieam rumos)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Pela Regra 15, em rumos cruzados entre navios de propulsão mecânica, se vejo o outro por estibordo sou eu que devo manobrar: afasto-me do caminho dele e, se possível, evito cortar-lhe a proa. A manobra deve ser cedo, clara e franca, conforme as Regras 8 e 16.
```

- O que devo fazer depois: manter o caso; o suporte existe no RIEAM/COLREG.

---

## 10. Após falar da Lisnave, perguntar: Marquei entrada para Secil E às 19:25. Está correto?

- Grupo: `Contexto conversacional`
- Risco: `Alto`
- Origem: `critical_matrix`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- Deve tratar Secil E como novo caso, sem herdar rebocadores, nevoeiro ou LOA da Lisnave.

**Factos com suporte fraco/ausente:**
- `partial` score `0.716`: Deve tratar Secil E como novo caso, sem herdar rebocadores, nevoeiro ou LOA da Lisnave. (doc: `AdmiraltyPilot_PortoSetubal.txt`; evidência: expected:caso como deve lisnave loa nevoeiro novo rebocadores secil sem | context:25 apos entrada esta lisnave secil)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Deve tratar a pergunta da Secil E como um novo caso, sem herdar automaticamente contexto da Lisnave. Para validar 19:25 faltam o reponto aplicável, a proveniência do navio; não deve puxar rebocadores, LOA ou nevoeiro da conversa anterior.
```

- O que devo fazer depois: manter o caso como regressão de contexto conversacional.

---

## 11. Qual a ordem dos cais de entrada pelo Canal Norte e pelo Canal Sul?

- Grupo: `Conversas a testar manualmente`
- Risco: `Alto`
- Origem: `critical_matrix`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- Tem de responder pela fonte de ordem dos cais, separando entrada/saida e Norte/Sul.

**Factos com suporte fraco/ausente:**
- `partial` score `0.648`: Tem de responder pela fonte de ordem dos cais, separando entrada/saida e Norte/Sul. (doc: `tug_operational_guidance.json`; evidência: expected:cais entrada norte pela responder saida sul | context:cais canal entrada norte pelo sul)

**A tua revisão:**

- Decisão: falta_knowledge
- Resposta operacional certa:

```text
Canal Norte, entrada: Pilar 2/Outão, João Farto, 1CC/3CC, TMS1, 5CC, TMS2, Autoeuropa, Praias do Sado, SAPEC e ALSTOM; à saída é o inverso. Canal Sul, entrada: Pilar 2/Outão, João Farto, 4CS, 6CS, 12CS, 14CS e depois zona Tanquisado/Eco-Oil/LISNAVE (cais 0 B /A, docas 21/22, cais 1 B /A, cais 2 B /A, cais 3B /A e hidrolift com docas 31/32/33))/Termitrena/Teporset conforme destino; à saída é o inverso.
```

- O que devo fazer depois: manter o caso, mas transformar a ordem Norte/Sul numa fonte estruturada mais explícita para o bot não improvisar.

---

## 12. Quando marco uma saida da Doca 22 da Lisnave e uma entrada para Tanquisado?

- Grupo: `Conversas a testar manualmente`
- Risco: `Alto`
- Origem: `critical_matrix`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- Deve combinar a matriz de marcacoes com as regras de cais, sem inventar mares.

**Factos com suporte fraco/ausente:**
- `partial` score `0.597`: Deve combinar a matriz de marcacoes com as regras de cais, sem inventar mares. (doc: `Notas_Pilotagem.txt`; evidência: expected:cais combinar deve mares regras sem | context:22 doca entrada lisnave quando saida tanquisado)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
São duas marcações diferentes e não se deve inventar maré. Saída da Doca 22 da LISNAVE: marcar 2h antes da preia-mar prevista; entrada para Tanquisado: 2h antes do reponto se vier de fora da Barra, 1h30 se vier do Fundeadouro Norte, ou 1h se vier de Tróia.
```

- O que devo fazer depois: manter o caso; reforçar que Doca 22 usa preia-mar e Tanquisado usa reponto. 

---

## 13. A fonte de balizagem/luzes de Setubal esta indexavel no conhecimento?

- Grupo: `Fontes de conhecimento`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- SISTEMA IALA
- IALA A
- Boia N.º 1CN
- Fl G 3s
- Boia N.º 2CS
- Doca Pesca

**Factos com suporte fraco/ausente:**
- `unsupported` score `0.0`: Fl G 3s (doc: `sem documento`; evidência: sem evidência)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Sim, a fonte de luzes/balizagem de Setúbal está indexável. Deve permitir responder que Setúbal usa IALA A e consultar marcas como 1CN, 2CS e Doca Pesca, incluindo característica luminosa, posição, alcance e descrição quando disponíveis.
```

- O que devo fazer depois: manter o caso; a auditoria falhou suporte lexical para "Fl G 3s", mas a fonte existe.
- Devemos rever o formato como a balizagem é mostrada, podemos usar emoji com cores e forma para identificar melhor, assim como mostrar um melhor resumo de cada uma delas mais organizado talvez por topicos por exemplo.

---

## 14. Qual e a caracteristica da Boia 1CN?

- Grupo: `Fontes de conhecimento`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Boia N.º 1CN
- Fl G 3s
- 38º30,33'N
- 8º51,46'W
- IALA A

**Factos com suporte fraco/ausente:**
- `unsupported` score `0.0`: Fl G 3s (doc: `sem documento`; evidência: sem evidência)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
A Boia n.º 1CN tem característica Fl G 3s, posição 38º30,33'N 8º51,46'W, alcance 3 M, é verde com alvo cónico e refletor radar. Em Setúbal aplica-se o sistema IALA A.
```

- O que devo fazer depois: manter o caso; reforçar a normalização de "Fl G 3s" no audit.

---

## 15. Qual a distância do TMS 1 até ao Outão?

- Grupo: `Percursos e distancias`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- TMS 1
- Outão
- 3,0 milhas náuticas
- pode ser somada

**Factos com suporte fraco/ausente:**
- `partial` score `0.487`: pode ser somada (doc: `companions/Notas_Pilotagem.json`; evidência: expected:ser | context:1 ate distancia distancias outao tms)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Do TMS1 até ao Outão são 3,0 NM. É uma distância de referência operacional que pode ser usada para estimar tempo, desde que a conta declare a velocidade assumida e não seja tratada como posição hidrográfica exata.
```

- O que devo fazer depois: manter o caso; a frase "pode ser somada" só precisa de melhor suporte/normalização.

---

## 16. Qual a distância do TMS 1 até fora da Barra?

- Grupo: `Percursos e distancias`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- TMS 1
- fora da Barra
- 6,0 milhas náuticas
- pode ser somada

**Factos com suporte fraco/ausente:**
- `partial` score `0.488`: pode ser somada (doc: `companions/Notas_Pilotagem.json`; evidência: expected:ser | context:1 ate barra distancia distancias fora tms)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Do TMS1 até fora da Barra/Pilar 2 são 6,0 NM. Pode servir para estimar tempo de trânsito, desde que se indique a velocidade usada e se trate como referência operacional.
```

- O que devo fazer depois: manter o caso; reforçar o texto das distâncias para cálculos de ETA.

---

## 17. Qual a distância do TMS 1 até à Alstom?

- Grupo: `Percursos e distancias`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- TMS 1
- Cais ALSTOM
- 3,5 milhas náuticas
- pode ser somada

**Factos com suporte fraco/ausente:**
- `partial` score `0.478`: pode ser somada (doc: `Notas_Pilotagem.txt`; evidência: expected:ser | context:1 alstom ate distancia distancias tms)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Do TMS1 até ao Cais ALSTOM são 3,5 NM. É uma referência operacional útil para tempo/distância, devendo a resposta separar distância de condições de manobra da ALSTOM.
```

- O que devo fazer depois: manter o caso.

---

## 18. Qual a distância do TMS 1 até à Autoeuropa em NM?

- Grupo: `Percursos e distancias`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- TMS 1
- Autoeuropa
- 1,0 milha náutica
- pode ser somada

**Factos com suporte fraco/ausente:**
- `partial` score `0.487`: pode ser somada (doc: `companions/Notas_Pilotagem.json`; evidência: expected:ser | context:1 ate autoeuropa distancia distancias tms)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Do TMS1 até à Autoeuropa/Ro-Ro são 1,0 NM. Pode ser usado para estimar tempo de trânsito se a resposta indicar a velocidade assumida.
```

- O que devo fazer depois: manter o caso.

---

## 19. Qual a distância do TMS 1 até à bóia João Farto?

- Grupo: `Percursos e distancias`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- TMS 1
- Bóia João Farto
- 1,6 milhas náuticas
- pode ser somada

**Factos com suporte fraco/ausente:**
- `partial` score `0.481`: pode ser somada (doc: `Notas_Pilotagem.txt`; evidência: expected:ser | context:1 ate boia distancia distancias farto joao tms)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Do TMS1 até à Bóia João Farto são 1,6 NM. É uma referência operacional de distância; para ETA, dividir pela velocidade em nós e converter horas em minutos.
```

- O que devo fazer depois: manter o caso.

---

## 20. Qual a distância do TMS 1 até à SAPEC?

- Grupo: `Percursos e distancias`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- TMS 1
- SAPEC
- 2,2 milhas náuticas
- pode ser somada

**Factos com suporte fraco/ausente:**
- `partial` score `0.487`: pode ser somada (doc: `companions/Notas_Pilotagem.json`; evidência: expected:ser | context:1 ate distancia distancias sapec tms)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Do TMS1 até à SAPEC são 2,2 NM. A resposta pode usar esta distância para estimativa de tempo, mas deve distinguir SAPEC Sólidos/Líquidos se a manobra depender do terminal concreto.
```

- O que devo fazer depois: manter o caso.

---

## 21. Qual a distância do TMS 1 até às Praias do Sado?

- Grupo: `Percursos e distancias`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- TMS 1
- Praias do Sado
- 1,6 milhas náuticas
- pode ser somada

**Factos com suporte fraco/ausente:**
- `partial` score `0.488`: pode ser somada (doc: `companions/Notas_Pilotagem.json`; evidência: expected:ser | context:1 ate distancia distancias praias sado tms)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Do TMS1 até às Praias do Sado são 1,6 NM. É uma distância de referência e pode ser usada em cálculo de ETA com velocidade declarada.
```

- O que devo fazer depois: manter o caso.
- Podes usar outras palavras para nao estarmos sempre a repetir: "É uma distância de referência e pode ser usada em cálculo de ETA com velocidade declarada."

---

## 22. Navio sem bowthruster de 110m e calado 8m precisa de quantos rebocadores?

- Grupo: `Rebocadores e vento`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Recomendo 1 rebocador grande
- 35 t
- nao rebocador pequeno

**Factos com suporte fraco/ausente:**
- `partial` score `0.692`: Recomendo 1 rebocador grande (doc: `companions/IT-016_Rebocadores.json`; evidência: expected:1 grande rebocador | context:calado navio rebocadores sem vento)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Para navio sem bowthruster, 110 m de LOA e 8 m de calado, a orientação prática é pelo menos 1 rebocador grande, cerca de 35 t. Não deve ser tratado como caso para rebocador pequeno de 25 t, porque o calado já torna a manobra mais pesada. Contudo, aconselha-se a 2 rebocadores pelo menos para equilibrar melhor as forças na manobra (quando se usa reboques convencionais). E talvez 3 rebocadores no caso de o vento estar forte e ser do quadrante Norte.
```

- O que devo fazer depois: manter o caso; reforçar a exceção "até 120 m com calado >= 8 m".

---

## 23. Um navio de 130m para a Lisnave precisa de quantos rebocadores?

- Grupo: `Rebocadores e vento`
- Risco: `Alto`
- Origem: `railway_150`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Recomendo 3 rebocadores
- acima de 100 m ate 150 m
- Quatro rebocadores sao excessivos

**Factos com suporte fraco/ausente:**
- `partial` score `0.615`: Recomendo 3 rebocadores (doc: `tug_operational_guidance.json`; evidência: expected:3 rebocadores | context:lisnave navio precisa rebocadores vento)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Para a LISNAVE, um navio de 130 m cai na regra prática acima de 100 m até 150 m: 3 rebocadores. Quatro rebocadores seriam excessivos para esta faixa e podem complicar a manobra, salvo ordem contrária por parte do Piloto Coordenador.
```

- O que devo fazer depois: rever o caso.

---

## 24. A indústria conserveira teve importância em Setúbal?

- Grupo: `Cultura Geral de Setúbal`
- Risco: `Médio`
- Origem: `complementary_questions`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- conserveira
- pesca
- Setúbal

**Factos com suporte fraco/ausente:**
- `unsupported` score `0.0`: conserveira (doc: `sem documento`; evidência: sem evidência)

**A tua revisão:**

- Decisão: falta_knowledge
- Resposta operacional certa:

```text
Sim. A indústria conserveira teve importância em Setúbal pela ligação histórica da cidade à pesca, ao Sado e à transformação de pescado; é um tema cultural, não operacional.
```

- O que devo fazer depois: confirmar com o utilizador e acrescentar explicitamente "indústria conserveira" à knowledge cultural antes de promover.

---

## 25. Porque é conhecido o estuário do Sado?

- Grupo: `Cultura Geral de Setúbal`
- Risco: `Médio`
- Origem: `complementary_questions`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Sado
- golfinhos
- estuário

**Factos com suporte fraco/ausente:**
- `unsupported` score `0.0`: golfinhos (doc: `sem documento`; evidência: sem evidência)

**A tua revisão:**

- Decisão: falta_knowledge
- Resposta operacional certa:

```text
O estuário do Sado é conhecido pela sua paisagem natural, pela relação com Setúbal e Tróia, e pela presença de golfinhos-roazes. É contexto cultural/natural e não deve ser usado para decisões operacionais.
```

- O que devo fazer depois: confirmar e acrescentar "golfinhos-roazes do Sado" à knowledge cultural.

---

## 26. Porque é conhecido o Mercado do Livramento?

- Grupo: `Cultura Geral de Setúbal`
- Risco: `Médio`
- Origem: `complementary_questions`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Mercado do Livramento
- Setúbal
- peixe

**Factos com suporte fraco/ausente:**
- `unsupported` score `0.0`: Mercado do Livramento (doc: `sem documento`; evidência: sem evidência)

**A tua revisão:**

- Decisão: falta_knowledge
- Resposta operacional certa:

```text
O Mercado do Livramento é conhecido em Setúbal pela qualidade e variedade do peixe fresco, marisco e produtos locais, sendo uma referência da ligação da cidade ao Sado e ao mar.
```

- O que devo fazer depois: confirmar e acrescentar o Mercado do Livramento à knowledge cultural.

---

## 27. Que interesse tem a Serra da Arrábida para Setúbal?

- Grupo: `Cultura Geral de Setúbal`
- Risco: `Médio`
- Origem: `complementary_questions`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Arrábida
- Setúbal
- paisagem

**Factos com suporte fraco/ausente:**
- `unsupported` score `0.0`: paisagem (doc: `sem documento`; evidência: sem evidência)

**A tua revisão:**

- Decisão: falta_knowledge
- Resposta operacional certa:

```text
A Serra da Arrábida é uma referência paisagística e natural essencial para Setúbal: enquadra a baía, a barra e a ligação visual entre cidade, mar, Sado e Tróia.
```

- O que devo fazer depois: confirmar e reforçar a knowledge cultural com Arrábida/paisagem.

---

## 28. Que produto regional está associado a Azeitão?

- Grupo: `Cultura Geral de Setúbal`
- Risco: `Médio`
- Origem: `complementary_questions`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Azeitão
- queijo
- Setúbal

**Factos com suporte fraco/ausente:**
- `unsupported` score `0.0`: queijo (doc: `sem documento`; evidência: sem evidência)

**A tua revisão:**

- Decisão: falta_knowledge
- Resposta operacional certa:

```text
Azeitão está muito associado ao queijo de Azeitão; também aparecem como produtos regionais as tortas de Azeitão e o Moscatel de Setúbal.
```

- O que devo fazer depois: confirmar e acrescentar queijo de Azeitão à knowledge cultural, porque a knowledge atual só explicita tortas e Moscatel.

---

## 29. Quem foi Bocage e qual a ligação a Setúbal?

- Grupo: `Cultura Geral de Setúbal`
- Risco: `Médio`
- Origem: `complementary_questions`
- Motivo da auditoria: Há factos esperados sem suporte claro na knowledge atual.

**Factos esperados atuais:**
- Bocage
- Setúbal
- poeta

**Factos com suporte fraco/ausente:**
- `unsupported` score `0.0`: Bocage (doc: `sem documento`; evidência: sem evidência)
- `unsupported` score `0.0`: poeta (doc: `sem documento`; evidência: sem evidência)

**A tua revisão:**

- Decisão: falta_knowledge
- Resposta operacional certa:

```text
Bocage foi um poeta português nascido em Setúbal, uma das figuras literárias mais associadas à cidade. A referência deve ficar no bloco cultural, sem interferir com respostas operacionais.
```

- O que devo fazer depois: confirmar e acrescentar Bocage à knowledge cultural.

---

## 30. Se não fosse nevoeiro e estivesse 31 kts de vento, já podia sair desde que tivesse 4 reboques?

- Grupo: `operational_safety_limits.json`
- Risco: `--`
- Origem: `knowledge_eval:knowledge/evals/golden_operational_companion_evals.json`
- Motivo da auditoria: Alguns factos só têm suporte parcial na knowledge atual.

**Factos esperados atuais:**
- Não
- 31 kt
- manobras ficam suspensas
- mais rebocadores não anula
- menos de 25 kt

**Factos com suporte fraco/ausente:**
- `partial` score `0.675`: mais rebocadores não anula (doc: `companions/Condicoes_Meteorologicas_Prioridades.json`; evidência: expected:mais nao rebocadores | context:4 json nao nevoeiro operational sair vento)

**A tua revisão:**

- Decisão: correto
- Resposta operacional certa:

```text
Não. Mesmo sem nevoeiro, 31 kt de vento sustentado ou rajada implica suspensão das manobras, porque o limite prático é superior a 25 kt; 4 reboques não anulam esse limite. Depois de suspensão por vento, só se deve retomar abaixo de 25 kt.
```

- O que devo fazer depois: manter o caso; reforçar que rebocadores adicionais não levantam uma suspensão por vento.
- 30 kts é impraticavel, quando o vento está nos 25 kts é caso a caso mas a maior parte está cancelado. Só em casos muito específicos e com reboques suficientes, etc.

---
