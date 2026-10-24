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


## Escalada crítica de 24 horas — solicitada em 20/10/2026

Além do prazo de uma hora útil, o responsável solicitou sinal vermelho e uma mensagem de desculpas 24 horas após a saudação da Fiorella, se não houver resposta humana, mesmo sem nova mensagem do cliente. Essas 24 horas são corridas, inclusive à noite e no fim de semana. Não há repetição diária autorizada: um único aviso crítico por saudação ainda sem resposta humana.

O registro monitor_greetings guarda a aceitação da saudação pela API, contexto derivado por HMAC e horário. É reconstruído para o piloto já aceito em reinícios. O painel marca em vermelho quando chega a sent_at + 24h e não há resposta de atendimento posterior; novas mensagens do cliente não adiam esse prazo. Uma resposta humana encerra a espera e remove o sinal vermelho. Uma espera nova depois dessa resposta não herda a saudação antiga. O aviso crítico por e-mail usa o mesmo controle persistente de tentativas por destinatário, separado do aviso de uma hora útil e dos resumos de pedidos. Não depende de uma nova mensagem para disparar.

72 testes locais passaram, incluindo limite exato de 24h, reinício, ausência de saudação aceita, resposta humana, mensagem posterior do cliente e aviso crítico em sábado antes do próximo expediente. Não foi gerado alarme artificial no banco real.

A mensagem ao cliente após 24h continua NÃO IMPLEMENTADA/NÃO ATIVADA. A política oficial do WhatsApp estabelece a janela de 24h a partir da última mensagem do cliente, renovada apenas por mensagem dele; fora dela exige modelo aprovado. Como a saudação normalmente vem depois da mensagem inicial, esperar 24h desde a saudação sem nova mensagem do cliente já excede a janela. O endpoint de texto simples usado no piloto não atende esse caso. É necessário cadastrar/aprovar o modelo e confirmar o recurso de envio de modelos pela API/automação do GPT Maker. O alarme deve funcionar mesmo quando o envio ao cliente estiver pendente ou falhar; o painel informa que a mensagem de desculpas não está configurada.

Fonte consultada em 20/10/2026: https://whatsappbusiness.com/policy/ (seção 2). A saudação inicial geral ainda está desativada; apenas o piloto do responsável está liberado.


## Modelo aprovado e API direta da Meta — 21/10/2026

O responsável apresentou o modelo fiorella_atendimento_pendente_24h ativo, pt_BR, categoria Utilidade. O suporte Bruno confirmou que a API GPT Maker não envia templates, orientando usar a API oficial com as credenciais disponíveis no canal. O responsável salvou WHATSAPP_BUSINESS_ACCOUNT_ID, WHATSAPP_PHONE_NUMBER_ID e WHATSAPP_ACCESS_TOKEN no Render. O número remetente permanece o canal comercial 3209, não o canal separado 5205.

meta_reminder.py consulta /v22.0/{waba}/message_templates e confirma nome exato, APPROVED, UTILITY, pt_BR, somente BODY sem variáveis. A consulta /phone_numbers confirma que o ID configurado pertence à conta esperada. Não registra token em logs nem retorna corpo de erro da Meta. A API é usada com Bearer em cabeçalho, redirecionamentos bloqueados e tamanho/tempo de resposta limitados.

O piloto de envio do modelo é limitado ao telefone do responsável e requer nova mensagem exata `Teste lembrete Fiorella`. Fiorella valida essa mensagem na API do GPT Maker (contexto, telefone, ID, texto, papel e horário) antes de chamar o envio Meta. O payload envia type=template, name=fiorella_atendimento_pendente_24h, language.code=pt_BR, sem componentes dinâmicos. A tentativa é gravada antes do POST e repetição/reinício não reenvia. accepted significa ID wamid retornado pela API, não entrega confirmada. Esse ID fica cifrado no banco. Respostas automáticas com o corpo exato aprovado são reconhecidas por resumo HMAC do texto, pois o webhook GPT Maker pode atribuir outro ID à mensagem.

GET /monitor/meta/probe é privado e verifica credenciais/modelo sem enviar mensagens; /monitor/meta mostra os estados das tentativas sem segredos. O teste pode enviar a mensagem imediatamente ao responsável para validar o caminho de entrega; não reduz o prazo real de 24 horas. O disparo temporizado e a liberação geral aos clientes ainda estão desativados e dependem de validar a chegada do modelo no piloto. O sinal vermelho e os e-mails internos permanecem independentes.

78 testes passaram, incluindo aprovação/categoria/idioma, conta incorreta, destinatário restrito, duplicatas, reinícios, resposta incerta e eco do template preservando a espera.


## Nova tentativa após cadastro de pagamento — 21/10/2026

A tentativa v1 foi aceita pela API em 21/10/2026 às 16h56, sem entrega recebida pelo responsável. O suporte GPT Maker confirmou falha de entrega sem código disponível. O responsável apresentou a conta de mensagens WABA 1607356107416841 com Nenhuma forma de pagamento e informou ter cadastrado o cartão. Isso é uma hipótese compatível, não uma confirmação do código da falha.

Para validar depois dessa alteração, foi criada uma tentativa v2 restrita ao mesmo destinatário de teste e ao mesmo modelo aprovado. O gatilho agora é exatamente `Teste lembrete Fiorella 2`. O antigo texto não dispara a nova tentativa, evitando que a repetição de eventos anteriores faça um reenvio. O registro v1 foi preservado. Uma nova mensagem precisa ser confirmada na API do GPT Maker antes de permitir v2, uma única vez. O envio geral e o disparo temporizado do modelo continuam desativados.


## Diagnóstico da tentativa v3 — 24/10/2026

O responsável confirmou entrega do mesmo modelo pelo chat do GPT Maker em 22/10, mas os dois envios diretos da Meta de 21/10 permanecem sem confirmação. O fluxo Ajuda da Meta levou a Relatar problema/Dar feedback; não houve protocolo nem confirmação nos e-mails consultados. Não há chamado técnico confirmado.

Foi preparado novo teste, ainda restrito ao responsável, com a frase exata `Teste lembrete Fiorella 3`. Não é enviado por publicação, consulta ou reinício: exige uma nova mensagem do responsável validada no histórico GPT Maker. Os registros v1/v2 não são apagados e seus eventos não disparam v3.

Antes de interpretar o retorno da Meta, os campos contacts e messages da resposta são cifrados e armazenados em meta_reminder_responses. O diagnóstico privado expõe somente comprimentos dos números, indicação de correspondência do wa_id e estados iniciais conhecidos, sem IDs nem telefones completos. Compara também os campos recipient e whatsappPhone do chat canônico com o destino do teste. held_for_quality_assessment não é tratado como confirmação de entrega. Os campos não guardados em tentativas antigas não podem ser recuperados retroativamente; a ausência de registros antigos é mostrada como ausência, não como prova de sucesso ou falha.

81 testes passaram. A mudança melhora o diagnóstico de um possível novo envio, não confirma a causa das falhas nem a solução. Não altera o destinatário, o modelo, a conta remetente ou os webhooks Meta/GPT Maker, e não habilita envio geral ou temporizado.


## Operação geral — versão 24/10/2026

Após o responsável confirmar a entrega do teste direto v3, ele autorizou concluir e deixar o sistema operativo. operation.py implementa o despacho persistente, inicialmente pausado até conferência e ativação pelo controle privado. A saudação e o lembrete usam texto fixo e modelo aprovado, respectivamente. Não ativa o agente de IA.

As rotas privadas /monitor/operation, /monitor/operation/probe e /monitor/control permitem consultar, conferir, pausar e retomar. Alterações exigem Basic Auth e token HMAC de ação. O endpoint /monitor/operation/connect substitui somente onNewMessage pela entrada direta do servidor, preservando outros eventos e gravando a configuração anterior cifrada em operation_webhook_backup. A URL direta exige chave secreta no caminho e todos os eventos são reconstruídos a partir do histórico autenticado do GPT Maker antes de serem persistidos. A URL não é exposta nos diagnósticos. O fluxo anterior do n8n deixa de ser necessário para este monitor.

A cada cinco segundos, um despachante lê novos eventos posteriores à ativação. A cada minuto, consulta os chats recentes do canal para recuperar notificações perdidas. A cada cinco minutos, confere o modelo Meta, o canal e o histórico GPT Maker. Chats são paginados em até três workspaces e vinte páginas de cem por workspace, com cache de trinta segundos. O histórico de mensagens retornado pelo provedor limita a recuperação disponível; não é uma importação integral nem uma garantia de captura ilimitada. O canal aceito é o channelId da conversa de referência do responsável já validada, e cada destinatário/contexto é novamente conferido na API. Grupos, bloqueados e pedidos explícitos de parar não recebem as mensagens.

O atendimento tem uma saudação, sem repetir enquanto estiver sem resposta. Após resposta humana, nova saudação requer retorno depois de 24h sem movimentação ou atendimento encerrado no GPT Maker. A conferência do histórico antes do envio evita saudar por cima de uma resposta humana já registrada. Mensagens com erro, excluídas ou aguardando aprovação não comprovam resposta humana. Resultados incertos não são reenviados. Contextos e números das sessões ficam cifrados. Claims no banco e trava de coordenação PostgreSQL evitam duplicatas entre reinícios, processos e publicações simultâneas.

O lembrete é executado no servidor 24 horas corridas após uma saudação aceita, depois de nova leitura canônica para cancelar em caso de resposta humana ou pedido para parar. É registrado antes do POST à Meta, uma vez por saudação, e permanece independente do aviso de uma hora útil e do alarme crítico. O status aceito continua significando aceitação da API, não confirmação de entrega. A rotina não envia saudações retroativas ao ativar ou retomar. operation_sessions, operation_attempts e operation_events mantêm o estado persistente. O guia de uso está em docs/operacao-fiorella.md.

97 testes locais passaram, incluindo produção com contato diferente do responsável, cancelamento por humano, não repetição, resultado incerto, pausa/retomada, restrição ao canal, recuperação de notificação perdida, autenticação das rotas e preservação/cópia cifrada da configuração anterior. A implantação é seguida de preflight real, conexão direta e ativação autorizada, sem campanha sobre o histórico anterior.
