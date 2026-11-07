# Operação da Fiorella — Fazendo Fita

Operação geral ativada em 24/10/2026 às 9h43, horário de São Paulo, após a conferência das conexões no servidor.

## Uso diário

Abra https://fazendo-fita-webhook.onrender.com/monitor com o mesmo login do CRM. Confira as conversas aguardando resposta, o horário limite e os alarmes em vermelho. Responda pelo WhatsApp Business da Fazendo Fita no celular; a mensagem humana registrada encerra a espera. Atualize o painel para ver o resultado. Não é necessário abrir n8n ou deixar o computador ligado para a rotina do servidor funcionar.

A Fiorella envia somente dois textos fixos: saudação no início do atendimento e uma mensagem de desculpas, pelo modelo aprovado da Meta, 24 horas corridas depois da saudação se nenhum humano tiver respondido. Não responde dúvidas e não manda outro lembrete diário. A saudação e o lembrete não encerram a espera nem reiniciam a contagem. Não ative o agente de IA no GPT Maker: os textos são enviados pelas integrações, sem conversa gerada por IA.

Uma nova saudação é permitida depois de uma resposta humana, quando o cliente retornar após pelo menos 24 horas sem movimentação, ou quando o atendimento anterior estiver encerrado na plataforma. Enquanto a espera estiver aberta, novas mensagens do cliente não geram outra saudação.

## Prazos e avisos

O limite de resposta humana é de uma hora útil, segunda a sexta, 8h às 18h, em São Paulo. O tempo fora desse expediente vai para o próximo dia útil. O calendário semanal não exclui feriados. Ao vencer, há aviso interno por e-mail aos três responsáveis configurados, uma vez por período de espera.

O segundo nível é vermelho, 24 horas corridas depois da saudação, inclusive no fim de semana. Ele gera aviso interno e a tentativa do modelo de desculpas, sem depender de nova mensagem do cliente. Somente uma resposta humana encerra uma pendência de atendimento. O monitor agora separa cliente/lead, fornecedor/parceiro e mensagens de baixa prioridade; casos ambíguos e agradecimentos continuam exigindo revisão como possíveis clientes.

## Pausar ou retomar

No final do painel há Pausar ou retomar os envios. A pausa afeta as mensagens automáticas aos clientes; o recebimento, o histórico e os avisos internos continuam. A retomada confere as conexões antes de liberar envios. Não há campanha de saudação para mensagens antigas. Uma espera já em andamento pode continuar gerando seu único lembrete, desde que o histórico do provedor confirme que ninguém respondeu.

## Quando conferir manualmente

Aceito pela API não significa entrega comprovada no celular. O painel registra as tentativas. Resultado incerto ou tentativa iniciada sem conclusão precisa de conferência no chat; o sistema não repete automaticamente um envio incerto, para evitar duplicatas. Falhas de conexão aparecem no painel. O alarme de atendimento continua relevante mesmo se o envio ao cliente falhar.

## Serviços necessários

Render e PostgreSQL mantêm o histórico e executam as rotinas. GPT Maker mantém o canal e fornece o histórico de mensagens; a Meta envia o modelo aprovado. Hostinger envia os avisos internos. Esses serviços e suas credenciais precisam permanecer ativos. O monitoramento do número 3209 passa a receber os eventos diretamente do GPT Maker e faz uma consulta de recuperação aproximadamente a cada minuto; não depende do fluxo My workflow 2 nem do período de teste do n8n.

Os dados de conversas e destinatários usados pela rotina ficam cifrados no banco. Não troque a chave BLING_ENCRYPTION_KEY: ela também protege as outras integrações. Não compartilhe tokens ou capturas das credenciais. A URL de recebimento direto contém uma chave privada; os logs de acesso do servidor estão desativados.


## Classificação conservadora dos alertas

O painel mostra a categoria e o motivo (regra versão 1). Cliente/lead e mensagens ambíguas mantêm os prazos existentes: uma hora útil e alarme vermelho 24 horas corridas após a saudação aceita, sem resposta humana. Fornecedor/parceiro exige apresentação explícita, mantém a pendência para revisão e o aviso de uma hora útil, mas não recebe alarme vermelho de cliente.

Marcadores explícitos de mensagem automática, institucional ou promocional (por exemplo, “Claro: oferta exclusiva”) recebem baixa prioridade, sem abrir pendência nem gerar alertas internos de atendimento. O nome Claro sozinho não basta. Intenção de compra/atendimento e anexos prevalecem sobre esses marcadores. Sem evidência suficiente, o fallback é possível cliente, com alertas preservados. A classificação é uma heurística conservadora e deve ser revisada no painel.

Uma mensagem promocional posterior não apaga uma pendência anterior de possível cliente. Após resposta humana, a próxima mensagem inicia uma nova classificação. Nenhum evento é apagado: o histórico cifrado e os registros de envios permanecem intactos; a categoria é reconstruída por ordem cronológica com a regra versionada no código. A regra também se aplica ao histórico já registrado. Esta alteração afeta o painel e os alertas internos; os envios de saudação e lembrete da Fiorella permanecem com o funcionamento anterior.
