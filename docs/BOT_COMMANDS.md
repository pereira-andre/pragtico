# Comandos do Bot Operacional

Este documento descreve os comandos suportados pelo bot no chat, o alvo que cada comando espera e o fluxo operacional correto desde a criação da escala até ao arquivo.

## Regras Base

- `Ref` identifica a escala.
- `ID da manobra` identifica uma manobra concreta.
- Se por engano escreveres o `ID da manobra` no campo `Ref`, o bot tenta resolver, mas a forma recomendada continua a ser usar `ID da manobra`.
- Ao criar uma manobra nova, não indiques `ID da manobra`; esse ID é gerado automaticamente.
- A entrada inicial nasce com a escala. Não se cria uma nova entrada com `/criar-manobra`.
- Para uma manobra já existente, podes usar `ID da manobra` ou `Ref + Tipo de manobra`.
- Se existir mais do que uma manobra elegível do mesmo tipo nessa escala, o bot passa a exigir `ID da manobra`.
- Quando faltar informação, o bot devolve um template para completar e depois pede confirmação.

## Fluxo Operacional

### 1. Escala

Usa `/registar-escala` para criar a escala e a entrada inicial.

Exemplo:

```text
/registar-escala
Nome: OCEAN BULKER
ETA de chegada: 29/03/2026, 16:10
Cais previsto: Teporset
Último porto: Casablanca
Próximo destino: Tarragona
IMO: 9482756
Indicativo: C6YZ4
Bandeira: Bahamas
Tipo de navio: Graneleiro
LOA (m): 199,9
Boca (m): 32,2
GT (t): 34.860
DWT (t): 56.400
Calado (m): 11,2
Calado (operacional): 10,8
Rebocadores: 4
Observações: Escala fictícia para operação de entrada.
```

### 2. Planeamento da Entrada Inicial

A entrada já existe dentro da escala criada. Se for preciso alterar hora, origem, destino, rebocadores, calado ou restrições dessa entrada, usa `/editar-manobra`.

Exemplo:

```text
/editar-manobra
Ref: PTSET26OCEA123456
ID da manobra: 7f3c2a91
Tipo de manobra: entrada
Hora prevista: 29/03/2026, 20:00
Origem: Casablanca
Destino: Teporset
Calado: 11
Rebocadores: 5
Restrições: daylight
Observações: Pedir 2 lanchas de amarração
Motivo da alteração: Ajuste operacional
```

### 3. Nova Saída ou Mudança

Para criar uma saída ou mudança de cais usa `/criar-manobra`.

Exemplo de saída:

```text
/criar-manobra
Ref: PTSET26OCEA123456
Tipo de manobra: saída
Hora prevista: 30/03/2026, 08:15
Origem: Teporset
Destino: Tarragona
Calado: 10,8
Rebocadores: 4
Restrições: daylight
Observações: Janela operacional confirmada
```

Exemplo de mudança:

```text
/criar-manobra
Ref: PTSET26OCEA123456
Tipo de manobra: mudança
Hora prevista: 30/03/2026, 11:30
Origem: Teporset
Destino: Cais 8
Calado: 10,8
Rebocadores: 3
Restrições: gas
Observações: Mudança para operação intermédia
```

### 4. Aprovação

Depois do planeamento, o piloto ou admin pode aprovar a manobra.

Exemplo:

```text
/aprovar
Ref: PTSET26OCEA123456
Tipo de manobra: saída
Observações: Piloto a bordo confirmado
```

Também podes usar:

```text
/aprovar
ID da manobra: 7f3c2a91
Observações: Piloto a bordo confirmado
```

Ou, em forma curta:

```text
/aprovar 7f3c2a91
```

### 5. Registo da Execução

Quando a manobra estiver concluída, usa `/registar-manobra`.

Exemplo:

```text
/registar-manobra
Ref: PTSET26OCEA123456
ID da manobra: 7f3c2a91
Tipo de manobra: saída
Início da manobra: 30/03/2026, 08:12
Fim da manobra: 30/03/2026, 08:54
Calado: 10,7
Observações: Manobra concluída sem incidentes
```

### 6. Revisão de Registo

Se o registo estiver errado, usa `/editar-registo-manobra`.

Exemplo:

```text
/editar-registo-manobra
ID da manobra: 7f3c2a91
Início da manobra: 30/03/2026, 08:10
Fim da manobra: 30/03/2026, 08:56
Calado: 10,7
Observações: Corrigir horas finais
Motivo da alteração: Ajuste após conferência do piloto
```

### 7. Abortos e Cancelamentos

Usa `/abortar` para cancelar ou abortar a manobra, consoante o estado.

Exemplo:

```text
/abortar
ID da manobra: 7f3c2a91
Motivo: Janela operacional fechada
```

### 8. Remoção

Para apagar uma manobra planeada:

```text
/apagar-manobra
ID da manobra: 7f3c2a91
```

Para apagar um registo executado:

```text
/apagar-registo-manobra
ID da manobra: 7f3c2a91
```

Para editar ou apagar a própria escala:

```text
/editar-escala
Ref: PTSET26OCEA123456
...
```

```text
/apagar-escala
Ref: PTSET26OCEA123456
```

## Catálogo de Comandos

### Consulta

- `/help` mostra a ajuda disponível para o perfil atual.
- `/avisos-locais` lista os avisos locais.
- `/ondulacao` mostra a leitura costeira atual.
- `/mares hoje` mostra marés para hoje ou para a data indicada.
- `/meteorologia hoje` mostra a previsão meteorológica.
- `/regra 015` consulta uma regra operacional por código.

### Escalas

- `/registar-escala` cria uma nova escala.
- `/editar-escala` altera os dados da escala; usa `Ref`.
- `/apagar-escala` remove a escala; usa `Ref`.

### Manobras

- `/criar-manobra` cria `saída` ou `mudança`.
- `/editar-manobra` altera o planeamento de uma manobra existente; usa `ID da manobra` ou `Ref + Tipo`.
- `/apagar-manobra` remove uma manobra planeada; usa `ID da manobra` ou `Ref + Tipo`.
- `/aprovar` aprova uma manobra pendente; usa `ID da manobra` ou `Ref + Tipo`.
- `/registar-manobra` regista início, fim e calado da manobra executada; usa `ID da manobra` ou `Ref + Tipo`.
- `/editar-registo-manobra` altera um registo já executado; usa `ID da manobra` ou `Ref + Tipo`.
- `/abortar` cancela ou aborta a manobra; usa `ID da manobra` ou `Ref + Tipo`.
- `/apagar-registo-manobra` remove o registo operacional executado; usa `ID da manobra` ou `Ref + Tipo`.

## O Que Usar em Cada Caso

- Quero criar a escala do navio: `/registar-escala`
- Quero mexer na entrada inicial dessa escala: `/editar-manobra`
- Quero criar uma saída: `/criar-manobra`
- Quero criar uma mudança de cais: `/criar-manobra`
- Quero rever uma hora planeada: `/editar-manobra`
- Quero validar a manobra: `/aprovar`
- Quero lançar horas reais e calado: `/registar-manobra`
- Quero corrigir um registo já feito: `/editar-registo-manobra`
- Quero cancelar uma manobra: `/abortar`
- Quero remover planeamento: `/apagar-manobra`
- Quero remover o registo executado: `/apagar-registo-manobra`
- Quero alterar dados cadastrais da escala: `/editar-escala`

## Notas de Perfil

- `agente`: regista escala, cria/edita/apaga planeamento, cancela antes da aprovação.
- `piloto`: aprova, regista execução, revê registos e aborta manobras aprovadas.
- `admin`: pode executar todos os comandos operacionais.

## Observações Importantes

- Se enviares `/editar-escala` com campos de manobra, o bot redireciona para `/editar-manobra`.
- Se tentares `/criar-manobra` com `Tipo de manobra: entrada`, o bot recusa e explica que a entrada inicial já pertence à escala.
- Se usares `ID da manobra`, não precisas de repetir `Ref` nem `Tipo de manobra`.
- O arquivo operacional passa a refletir as manobras depois de concluídas e registadas, ou quando ficam abortadas.
