# Webhook da Fazendo Fita — primeira etapa

Primeira implementação do webhook. O serviço ainda precisa ser implantado e validado no Render.

## O que está pronto
- GET /webhook: valida o token e devolve o desafio da Meta.
- POST /webhook: verifica a assinatura HMAC SHA-256 com o segredo do app.
- Grava o evento completo em PostgreSQL antes de responder 200.
- Eventos com conteúdo exatamente igual são gravados apenas uma vez.
- /healthz verifica a conexão com o banco, sem expor dados.
- Não envia mensagens, baixa anexos nem classifica conversas nesta etapa.

## Configuração no Render
Os arquivos de execução ficam na raiz do repositório; os testes ficam em tests/ e este guia em docs/. A documentação de negócio existente foi preservada.

1. Criar PostgreSQL pago, na mesma região do serviço. Conferir preço, armazenamento e política de backups no painel. É uma cobrança separada do serviço de US$ 7 mostrado na captura.
2. No serviço fazendo-fita-webhook: Language Docker; Branch main; Root Directory vazio; Dockerfile Path ./Dockerfile. Deixar Docker Command vazio.
3. Health Check Path: /healthz.
4. Configurar variáveis privadas no Render:
   - DATABASE_URL: Internal Database URL do PostgreSQL.
   - META_APP_SECRET: segredo do aplicativo da Meta, localizado nas configurações básicas do app. Não é o token de acesso.
   - VERIFY_TOKEN: valor aleatório longo criado por você e usado também na verificação do webhook da Meta. Não é o token de acesso.
5. Não colocar essas credenciais em código, capturas, arquivos versionados ou mensagens do chat.
6. Depois da implantação bem-sucedida, abrir https://ENDERECO-DO-SERVICO/healthz e conferir a resposta ok.
7. Na Meta: URL de callback https://ENDERECO-DO-SERVICO/webhook; token igual a VERIFY_TOKEN; certificado de cliente desativado para esta configuração.
8. Clicar Verificar e salvar. Assinar o campo messages quando a opção estiver disponível e usar o teste do painel.
9. Conferir a gravação no banco por acesso privado, nunca criando um endpoint público de listagem de mensagens.

A tela da Meta informa que eventos de produção dependem da publicação do app. A verificação do webhook não conecta automaticamente o número comercial. Preservar a conexão atual; a associação da conta real será uma etapa separada.

## Testes locais
Na pasta desta entrega, executar: python -m unittest discover -s tests -v
Os testes usam um banco SQLite temporário. No Render, DATABASE_URL é obrigatória; não utilizar SQLite no armazenamento efêmero do serviço.

## Limites desta etapa
Ainda faltam validar a imagem Docker e o PostgreSQL no ambiente real, definir retenção e backups, extrair mensagens por identificador, consolidar eventos equivalentes enviados em corpos diferentes, correlacionar respostas da empresa e implantar análise contextual. O armazenamento atual preserva o evento bruto; não representa um histórico completo das conversas.

Depois: configurar envio autorizado para a esposa, modelos de mensagem conforme regras da Meta, análise e agendamento persistente de 12:00 e 16:30 em America/Sao_Paulo. Não há agendador nesta versão. Antes de operar, definir acesso aos dados e rotina de exclusão/retencão.

## Referências
- https://whatsapp.github.io/WhatsApp-Nodejs-SDK/api-reference/webhooks/start/
- https://render.com/docs/web-services
- https://render.com/docs/docker
- https://render.com/docs/disks
