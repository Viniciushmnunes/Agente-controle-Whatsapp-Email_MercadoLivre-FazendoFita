# Resumos internos por e-mail

Destinatários fixos autorizados: contato@fazendofita.com.br, patyalves.seven@hotmail.com e vhm.nunes@gmail.com. Nunca são usados os endereços dos compradores.

Configurar no Render: SMTP_HOST=smtp.hostinger.com, SMTP_PORT=465, SMTP_USER=contato@fazendofita.com.br e SMTP_PASSWORD com a senha da caixa, diretamente no painel. O código usa SMTP com TLS e validação de certificado. Não registrar senha no GitHub nem em capturas.

Primeiro usar EMAIL_ENABLED=false e EMAIL_TEST_ID=20261011-1. Cada destinatário recebe uma tentativa de teste. A aceitação pelo servidor não confirma chegada à caixa: verificar também spam nos três endereços. Após a conferência, EMAIL_ENABLED=true habilita resumos diários às 12h e 16h30 em America/Sao_Paulo. Para um novo teste intencional, mudar EMAIL_TEST_ID. O mesmo identificador nunca é reenviado.

Os resumos contêm números dos pedidos, valores e ações do painel, sem nomes ou documentos dos compradores. Datas de importação e dados desatualizados são indicados. Os testes excluídos do CRM permanecem excluídos dos resumos. As consultas do Mercado Livre distinguem dados atuais, pendências e dados antigos; e-mails não confirmam um despacho por previsão do Bling.

Cada tentativa por horário e destinatário é registrada no banco antes de enviar. Isso evita duplicação em reinícios ou processos simultâneos. Falhas ou interrupções depois do registro podem deixar o resultado incerto; não são repetidas automaticamente, para evitar duplicatas. O painel mostra as tentativas e sua situação sem exibir erros do provedor ou credenciais. Aceito significa aceito pelo servidor SMTP, não comprovadamente recebido. Uma janela de 15 minutos permite pequeno atraso por reinício; horários perdidos não geram resumos antigos depois disso.

A rotina depende do serviço Render estar em execução. Ainda não inclui leitura de e-mails, classificação de conversas ou alertas por WhatsApp. Não usa automações do aplicativo Codex: roda no serviço da loja.
