# Monitoramento do WhatsApp

O canal WhatsApp API Oficial do GPT Maker está conectado ao número comercial existente. O agente Monitoramento Fazendo Fita permanece inativo. Nos testes de 20/10/2026, o evento Nova mensagem encaminhou tanto mensagens do cliente (role=user) como respostas enviadas pelo celular (role=assistant). Isso não identifica o atendente nem prova que toda mensagem assistant é humana caso a configuração do agente mude.

O fluxo My workflow 2 no n8n usa Webhook POST, resposta imediata. A URL de produção foi salva no GPT Maker e o recebimento em produção foi validado nos dois sentidos. O fluxo anterior do outro número é independente.

## Encaminhamento no n8n (validado em produção em 20/10/2026)

Configuração validada: HTTP Request depois do Webhook existente, sem substituir seu caminho ou criar outro gatilho:

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

Deduplicação por agente e messageId; duplicata conflitante é rejeitada. O agente aceito é o ID da configuração validada, substituível por GPTMAKER_MONITOR_AGENT_ID. O painel reconstrói cada contexto pela data dos eventos, mesmo quando chegam fora de ordem. Empates entre cliente e resposta ficam para revisão. Mostra até 200 conversas com prioridade para prazos vencidos.

Última mensagem do cliente é um indicador para revisão, não uma classificação de intenção nem uma confirmação de pergunta pendente. Não há envio automático ao cliente, download de anexos, importação retroativa ou exclusão programada nesta etapa. Não há mudanças no cronograma dos resumos de pedidos.

Autenticar n8n→Render protege esse trecho. O Webhook público inicial do GPT Maker→n8n ainda não valida assinatura do provedor: campos como assistantId não são prova criptográfica de origem. O painel deve ser tratado como apoio à revisão, sem disparar decisões ou respostas automáticas com base apenas nesses eventos. Antes de automações de atendimento, implementar autenticação de origem/reconciliação pelo provedor e definir a retenção do histórico.


## Prazo de resposta e aviso interno — autorizado em 20/10/2026

O responsável definiu prazo máximo de uma hora útil, de segunda a sexta, das 8h às 18h em America/Sao_Paulo. Feriados não são tratados como exceção neste calendário semanal. A primeira mensagem após a última resposta inicia a espera; as seguintes não reiniciam o prazo. Segunda 17h40 vence terça 8h40; sexta 19h vence segunda 9h. O limite exato já conta como vencido. Resposta registrada de atendimento encerra a espera. O painel é recalculado ao atualizar a página.

A rotina de e-mail existente verifica os prazos aproximadamente a cada 30 segundos, quando EMAIL_ENABLED=true. Envia um aviso por período de espera a cada um dos três destinatários internos já autorizados. O identificador usa o contexto e a última resposta anterior, para uma mensagem de cliente mais antiga recebida com atraso não gerar outro aviso. O assunto identifica o atraso; o corpo mostra somente os quatro últimos dígitos do contato, início da espera, limite e link para o painel privado. Não envia texto da conversa nem nome completo. Os resumos de pedidos continuam nos horários existentes. Uma espera já vencida ao reiniciar também é avisada; não exige chegada de nova mensagem. SMTP com resultado incerto não é repetido automaticamente; consultar o status de e-mails no CRM. Aceitação SMTP não prova entrega na caixa.

A Fiorella foi aprovada como saudação única, mas o envio ainda não está configurado. O agente GPT Maker continua inativo. Não ativá-lo com apenas um prompt: isso não garante uma única mensagem nem a distinção de origem nos webhooks. A tabela monitor_automatic_messages permite registrar IDs retornados por um futuro envio confiável, inclusive antes de o webhook chegar. Mensagens registradas como automáticas não encerram espera. Como proteção adicional, o texto exato da saudação aprovada também mantém a espera, inclusive se copiado manualmente; não é prova de origem. Qualquer variação enviada por um agente ativo pode ser confundida com resposta, portanto a ativação depende de um fluxo validado com identificação confiável de mensagens e envio uma vez por atendimento.

Texto aprovado: Olá! Sou a Fiorella, assistente virtual da Fazendo Fita. Recebemos sua mensagem! Uma pessoa da nossa equipe dará continuidade ao seu atendimento assim que possível. 🌷


## Piloto da Fiorella — somente conversa do responsável

A chave GPTMAKER_API_KEY fica apenas no ambiente do Render. fiorella.py implementa um piloto limitado ao telefone do responsável já validado nos testes. Somente a mensagem exata `Teste Fiorella`, role=user, recebida há até cinco minutos pode disparar uma saudação. Outros contatos e textos não disparam envio. Não ativa o agente de IA e não modifica seu comportamento.

Antes do envio, o serviço consulta os workspaces, os primeiros 100 chats de até três workspaces e o histórico da conversa. O filtro de agente da API retornou vazio no atendimento humano validado; por isso, exige correspondência única entre telefone do responsável e o contexto já observado no monitor, além de ID da mensagem, papel user, texto e horário recente na API. O ingresso do monitor continua limitado ao assistantId configurado. Falha de consulta ou esquema diferente impede envio, sem perder o evento já salvo. O acesso público ao webhook sozinho não autoriza mensagem a um cliente arbitrário. Em 20/10/2026, uma consulta somente de leitura no servidor confirmou a chave, correspondência do contexto e correspondência dos IDs de mensagens; entrega da saudação ainda depende de uma nova mensagem de teste do responsável.

Usa POST /v2/chat/{chatId}/send-message somente com o texto fixo aprovado. A resposta documentada contém apenas success, sem ID da mensagem. Após aceitação, consulta novamente o histórico para registrar IDs de saudações correspondentes como automáticos; a comparação exata do texto aprovado também preserva o relógio durante essa reconciliação. Não responde dúvidas nem gera texto por IA.

A tentativa é registrada antes do POST no banco, uma vez para a conversa de teste (versão v1 do piloto). Resultado incerto não é repetido automaticamente; accepted indica somente aceitação da API. Eventos novos, repetições e reinícios não geram outra saudação nessa conversa. Isso é uma restrição do piloto, não a definição final de abertura de novos atendimentos.

GET /monitor/fiorella exige o login privado e mostra chave presente, modo e estados das tentativas, sem chave ou conteúdo das conversas. GET /monitor/fiorella/probe verifica leitura na API e expõe apenas nomes de campos e indicadores de correspondência, sem enviar mensagens. Nenhuma dessas páginas habilita envio geral. A liberação aos clientes ainda depende de validar entrega, retorno do webhook, preservação do prazo e a regra para uma nova saudação em um novo atendimento.
