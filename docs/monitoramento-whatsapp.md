# Monitoramento do WhatsApp

O canal WhatsApp API Oficial do GPT Maker está conectado ao número comercial existente. O agente Monitoramento Fazendo Fita permanece inativo. Nos testes de 20/10/2026, o evento Nova mensagem encaminhou tanto mensagens do cliente (role=user) como respostas enviadas pelo celular (role=assistant). Isso não identifica o atendente nem prova que toda mensagem assistant é humana caso a configuração do agente mude.

O fluxo My workflow 2 no n8n usa Webhook POST, resposta imediata. A URL de produção foi salva no GPT Maker e o recebimento em produção foi validado nos dois sentidos. O fluxo anterior do outro número é independente.

## Encaminhamento ainda necessário no n8n

Adicionar HTTP Request depois do Webhook existente, sem substituir seu caminho ou criar outro gatilho:

- Method: POST.
- URL: https://fazendo-fita-webhook.onrender.com/monitor/events
- Authentication: Generic Credential Type; Generic Auth Type: Basic Auth.
- Criar credencial privada com usuário admin e a senha atual do painel CRM. Nunca colocar a senha no corpo, URL, exportação do fluxo, Git ou captura de tela.
- Send Body: ligado; Body Content Type: JSON; Specify Body: Using JSON.
- Campo JSON em Expression: `{{ $json.body }}` (somente o corpo original do Webhook).
- Resposta em texto ou autodetecção. Sucesso retorna EVENT_STORED. Erros devem interromper a execução, sem Never Error/Continue On Fail. Configurar tentativas do nó para falhas transitórias; duplicatas idênticas são seguras.
- Salvar/publicar a nova versão. Testar uma nova mensagem de cliente e, depois, uma resposta no celular. Conferir as duas execuções e o painel privado /monitor. Não ativar o agente.

As execuções anteriores não são importadas automaticamente. O Webhook responde ao GPT Maker antes do armazenamento no Render; falhas posteriores aparecem no n8n e exigem nova tentativa. Não há garantia de captura integral nem reconciliação automática com a API do provedor nesta etapa.

## Armazenamento e painel

O endpoint e /monitor exigem a autenticação do painel existente. O conteúdo de texto, nome, telefone e identificadores originais fica cifrado com a chave Fernet já configurada no Render. Não trocar essa chave: ela também protege as credenciais das integrações existentes. Identificadores para indexação/deduplicação são derivados por HMAC com chave estável. Data normalizada, papel da mensagem e data de recebimento ficam disponíveis para ordenação. URLs e arquivos de mídia não são guardados nem baixados; registra-se apenas a quantidade de anexos.

Deduplicação por agente e messageId; duplicata conflitante é rejeitada. O agente aceito é o ID da configuração validada, substituível por GPTMAKER_MONITOR_AGENT_ID. O painel escolhe a última mensagem de cada contexto pela data do evento, mesmo quando chegam fora de ordem. Empates entre cliente e resposta ficam para revisão. Mostra até 200 conversas com prioridade para última mensagem do cliente.

Última mensagem do cliente é um indicador para revisão, não uma classificação de intenção nem uma confirmação de pergunta pendente. Não há prazo de atraso, envio automático, resumo de WhatsApp por e-mail, download de anexos, importação retroativa ou exclusão programada nesta etapa. Não há mudanças no cronograma dos resumos de pedidos.

Autenticar n8n→Render protege esse trecho. O Webhook público inicial do GPT Maker→n8n ainda não valida assinatura do provedor: campos como assistantId não são prova criptográfica de origem. O painel deve ser tratado como apoio à revisão, sem disparar decisões ou respostas automáticas com base apenas nesses eventos. Antes de automações de atendimento, implementar autenticação de origem/reconciliação pelo provedor e definir a retenção do histórico.
