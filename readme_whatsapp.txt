Guia simples para ligar o PRAGtico ao WhatsApp Cloud API
========================================================

Objetivo
--------
Este guia resume os passos que usamos para por o bot do PRAGtico a funcionar no WhatsApp.
Cada passo inclui uma explicacao curta do motivo, para nao ficares so com uma lista de cliques.


1. Criar a app no Meta for Developers
-------------------------------------
No Meta for Developers, criar uma app normal e escolher o caso de uso relacionado com WhatsApp
("Connect on WhatsApp" ou equivalente).

Porque este passo existe:
- A app e o contentor tecnico da integracao.
- E nela que ficam o webhook, o produto WhatsApp, as permissoes e os tokens.


2. Adicionar o produto WhatsApp
-------------------------------
Dentro da app, adicionar o produto WhatsApp.

Porque este passo existe:
- Isto ativa a WhatsApp Cloud API para a app.
- Em modo de teste, a Meta cria normalmente uma test WABA e um test number.


3. Adicionar o telemovel de teste a lista permitida
---------------------------------------------------
Se estiveres a usar o numero de teste da Meta, adicionar o teu numero pessoal a lista de
destinatarios permitidos.

Porque este passo existe:
- O test number nao envia para qualquer telefone.
- So permite enviar mensagens para numeros explicitamente autorizados.


4. Criar um System User Admin no Business Settings
--------------------------------------------------
Na Meta Business Suite, ir a Business Settings > Users > System Users e criar um utilizador
de sistema com papel Admin.

Porque este passo existe:
- O token temporario do Explorer/API Setup expira e da erros 401.
- Para backend real, o token certo e um System User Access Token.


5. Dar acesso do System User a app e a WABA
-------------------------------------------
Com o System User selecionado, dar acesso total:
- a app PRAGtico
- a WhatsApp Business Account (WABA)

Porque este passo existe:
- O utilizador do sistema sozinho nao basta.
- Ele precisa de acesso real aos assets que vai usar para emitir tokens e enviar mensagens.


6. Gerar o token do System User
-------------------------------
No mesmo ecran do System User, clicar em Generate new token, escolher a app PRAGtico e pedir
as permissoes:
- business_management
- whatsapp_business_management
- whatsapp_business_messaging

Porque este passo existe:
- business_management da acesso ao business portfolio
- whatsapp_business_management da acesso a WABA, templates e numeros
- whatsapp_business_messaging permite enviar e receber mensagens

Nota:
- Se houver opcao de validade longa ou "Never", usar essa opcao para o backend.


7. Ir buscar os IDs certos
--------------------------
No painel WhatsApp da app, copiar:
- WhatsApp Business Account ID (WABA ID)
- Phone Number ID

Porque este passo existe:
- A API usa estes IDs internamente.
- O backend nao trabalha apenas com o numero visivel no ecran.


8. Registar o numero por API se o UI falhar
-------------------------------------------
Se o painel mostrar erro ao registar o numero, fazer o registo por API com:
- POST /<PHONE_NUMBER_ID>/register

Porque este passo existe:
- No nosso caso, o UI da Meta falhou.
- O registo pela API funcionou e desbloqueou o numero.


9. Configurar o webhook publico
-------------------------------
No produto WhatsApp > Webhooks, configurar:
- Callback URL
- Verify Token

Porque este passo existe:
- A Meta tem de validar que o endpoint e mesmo teu antes de enviar eventos reais.
- O endpoint tem de responder ao desafio hub.challenge no GET de verificacao.

No nosso caso:
- Callback URL: https://pragtico.up.railway.app/webhooks/whatsapp


10. Subscrever o campo messages
-------------------------------
Na configuracao do webhook da app, no objeto WhatsApp Business Account, subscrever o campo:
- messages

Porque este passo existe:
- Sem isto, a Meta pode validar o webhook mas nao entrega mensagens reais ao teu backend.


11. Configurar as variaveis no backend
--------------------------------------
No Railway, garantir pelo menos:
- WHATSAPP_ENABLED=1
- WHATSAPP_VERIFY_TOKEN=...
- WHATSAPP_ACCESS_TOKEN=...
- WHATSAPP_PHONE_NUMBER_ID=...
- WHATSAPP_BUSINESS_ACCOUNT_ID=...
- WHATSAPP_GRAPH_API_VERSION=v25.0

Se estiveres em teste controlado, tambem podes definir:
- WHATSAPP_ALLOWED_NUMBERS=351962063664

Porque este passo existe:
- Estas variaveis ligam o backend aos recursos certos da Meta.
- Se o token estiver errado ou expirado, a rececao pode funcionar mas a resposta falha com 401.


12. Fazer redeploy do backend
-----------------------------
Depois de mudar variaveis ou codigo, fazer redeploy no Railway.

Porque este passo existe:
- O webhook publico usa sempre a versao ativa em producao.
- Se o Railway estiver numa versao antiga, a Meta pode receber 404 ou 503.


13. Testar a verificacao do webhook
-----------------------------------
Validar com um curl deste genero:

curl "https://pragtico.up.railway.app/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=TEU_TOKEN&hub.challenge=abc123"

Resultado esperado:
- responder apenas com abc123

Porque este passo existe:
- Confirma que a URL publica e o token estao corretos antes de testar mensagens reais.


14. Testar envio e rececao reais
--------------------------------
Mandar uma mensagem do teu WhatsApp para o numero do PRAGtico.

Resultado esperado:
- a Meta faz POST ao webhook
- o PRAGtico recebe a mensagem
- o bot responde no WhatsApp

Porque este passo existe:
- Este e o unico teste que valida o fluxo fim a fim.


15. Entender o papel dos templates
----------------------------------
Nao e preciso template para o bot responder a uma mensagem que o utilizador enviou.
So e preciso template aprovado para iniciar conversas outbound/proativas.

Porque este passo existe:
- Evita perder tempo a mexer em templates quando o objetivo e apenas responder a mensagens inbound.


16. Passar do test number para producao
---------------------------------------
Quando quiseres sair do sandbox:
- trocar o test number pelo numero real
- manter o token de System User
- manter o webhook e a subscricao messages

Porque este passo existe:
- O test number serve para validar a integracao.
- O numero real e o passo seguinte para uso operacional.


17. Problemas que apareceram no nosso caso
------------------------------------------
1. O painel da Meta falhava ao registar o numero.
   Solucao: registar por API.

2. O webhook validava, mas nao chegavam mensagens reais.
   Solucao: subscrever o campo messages.

3. O inbound chegava, mas o bot nao respondia.
   Solucao: trocar o token temporario por um token valido de System User.

4. O Railway respondia 404/503 ao webhook.
   Solucao: garantir deploy correto e variaveis certas em producao.


18. Estado final que confirma sucesso
-------------------------------------
Consideramos a integracao operacional quando:
- o webhook valida com abc123
- o POST /webhooks/whatsapp entra no Railway
- o bot responde no WhatsApp
- o log deixa de mostrar 401 na chamada a /messages


Resumo curto
------------
Para isto funcionar bem, os pontos realmente criticos foram:
- app com produto WhatsApp
- System User com permissoes certas
- token valido no Railway
- webhook publico validado
- campo messages subscrito
- numero de teste permitido

Se estes pontos estiverem corretos, o resto costuma ser detalhe de configuracao.
