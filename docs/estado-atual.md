# Estado atual do projeto

Atualizado em 24/10/2026.

O CRM Bling/Mercado Livre e os resumos internos por e-mail estão publicados. O WhatsApp comercial 3209 está conectado em coexistência pelo GPT Maker, com agente de IA inativo. Recebimento, resposta humana, saudação fixa, preservação do prazo, aviso de uma hora e entrega do modelo Meta foram validados em testes reais. A operação geral está ativa desde 24/10/2026 às 9h43 (São Paulo), após preflight real e verificação da conexão direta. O recebimento do número 3209 não depende mais do n8n. Consulte [Operação da Fiorella](operacao-fiorella.md) e [registro técnico](monitoramento-whatsapp.md).

## Histórico de 01/10/2026

Os registros abaixo documentam uma etapa anterior; não descrevem as integrações atuais.

## WhatsApp Business

- Empresa: Fazendo Fita
- Número comercial: conectado à Plataforma do WhatsApp Business.
- O aplicativo WhatsApp Business continua funcionando normalmente.
- Foi realizado teste enviando mensagem do telefone pessoal para o número comercial; a mensagem chegou normalmente.
- Ao conectar a plataforma, o compartilhamento do histórico de conversas foi recusado.
- O Instagram da Fazendo Fita foi adicionado ao perfil do WhatsApp.

**Importante:** não desconectar, migrar novamente ou apagar o número enquanto a integração estiver sendo investigada.

## Meta Business

No Meta Business Suite foi verificado:

- Conta do WhatsApp aprovada.
- Número aparece com o nome de exibição Fazendo Fita.
- O status anteriormente apareceu como offline e depois passou a aparecer como conectado.
- Em Pessoas e parceiros, o responsável recebeu acesso total.
- Em Configurações → Integrações → Apps conectados, tanto “Conectados” quanto “Removidos” não mostram integrações comerciais.

Isso não indica que o WhatsApp esteja desconectado; essa área é diferente da conexão da Plataforma do WhatsApp Business.

## Meta for Developers

Ao acessar a área de aplicativos da Meta, a conta apresentou:

“Nenhum aplicativo foi encontrado”.

A tentativa de entrar pela Central do Desenvolvedor do WhatsApp também retorna ao ambiente de aplicativos sem apresentar um botão claro para criar aplicativo.

Foi encontrada referência ao fluxo moderno de desenvolvimento/onboarding da Meta, mas o caminho exato aplicável à conta da Fazendo Fita ainda precisa ser confirmado antes de qualquer alteração.

### Decisão

Não continuar tentando menus aleatoriamente e não alterar a configuração atual do WhatsApp. Pesquisar/confirmar o fluxo oficial atual da Meta para uma conta que já possui o número conectado à Plataforma do WhatsApp Business.

## Hostinger Mail

A caixa da empresa está conectada ao ChatGPT:

- contato@fazendofita.com.br

A integração de e-mail foi testada em modo de leitura.

### Regras já calibradas

Não classificar e-mails apenas por palavras-chave como “Mercado Livre”, “Bling”, “pagamento” ou “Pix”.

Interpretar:

- quem pagou quem;
- o que foi pago;
- finalidade da comunicação;
- contexto anterior.

Exemplo real validado: uma confirmação de pagamento Pix ao próprio Bling não era uma venda da Fazendo Fita e não deve ser classificada como “venda recebida”.

## Classificação

### 🔴 URGENTE

- venda Mercado Livre;
- reclamação sem solução;
- cliente aguardando há muito tempo;
- problema de pagamento/pedido;
- cliente com necessidade imediata;
- oportunidade de venda em risco;
- falha crítica do Bling que possa bloquear faturamento, NF, expedição, integração ou outra operação essencial;
- evento de segurança que exija verificação, como alteração de usuário/dados ou criação de chave de acesso desconhecida.

### 🟠 ALTA — OPORTUNIDADE DE VENDA

- novo cliente interessado;
- pedido de orçamento;
- pergunta de preço;
- disponibilidade;
- compra em quantidade;
- negociação;
- “quero comprar” e equivalentes, considerando o contexto.

### 🟡 NORMAL

- dúvidas comuns;
- acompanhamento de pedido;
- dúvidas de entrega;
- solicitações administrativas;
- problemas operacionais normais do Bling.

### 🟢 BAIXA

- informações gerais;
- comunicações sem ação necessária;
- marketing e conteúdo de baixa relevância.

## Regra de intenção de compra

Nunca considerar toda pergunta como venda.

Avaliar conjuntamente:

produto + quantidade + preço + prazo + contexto + intenção.

## Contexto de conversa

Exemplo:

Cliente: “Vocês têm laço vermelho?”
Empresa: “Temos sim.”
Cliente: “Quanto custa 20 unidades?”

Resultado: oportunidade comercial 🟠 aguardando resposta da empresa.

Se uma oportunidade de alta intenção permanecer sem resposta, poderá evoluir para alerta de venda em risco.

## Ciclo de oportunidade

- NOVO INTERESSE
- ORÇAMENTO SOLICITADO
- AGUARDANDO EMPRESA
- ORÇAMENTO ENVIADO
- AGUARDANDO CLIENTE
- NEGOCIAÇÃO
- COMPRA CONFIRMADA
- PEDIDO REGISTRADO
- EM PRODUÇÃO/SEPARAÇÃO
- ENVIADO
- FINALIZADO
- PERDIDO / SEM RETORNO

## Mercado Livre

Toda comunicação que indique venda realizada é 🔴 urgente.

Uma mesma venda recebida por WhatsApp e e-mail deve ser consolidada em uma ocorrência única, usando número do pedido, produto, horário, cliente e outros dados de correlação.

Registrar:

- número do pedido;
- produto;
- quantidade;
- cliente, quando disponível;
- data/hora da venda;
- data-limite para postagem;
- horário-limite;
- tempo restante;
- status da postagem;
- última atualização;
- divergências.

Monitoramento previsto: 12:00 e 16:30, até postagem confirmada ou prazo finalizado.

Se surgir risco antes do próximo horário, alertar imediatamente.

Alertas:

- “PRAZO DE POSTAGEM SE APROXIMANDO”
- “PRAZO DE POSTAGEM EXPIRADO”

Nunca assumir prazo padrão: usar a informação específica da venda.

## Bling

O Bling é o ERP interno da Fazendo Fita.

Fluxo esperado:

Mercado Livre → Venda → Bling → NF/separação/expedição → Postagem → Finalizado

O agente deve correlacionar, quando possível:

- pedido;
- faturamento/NF;
- estoque;
- separação;
- expedição;
- postagem;
- finalização.

Divergências entre Mercado Livre e Bling devem ser sinalizadas.

## Notificações

- 12:00 — resumo comercial.
- 16:30 — resumo + pendências + oportunidades.
- Alertas 🔴 não devem esperar o relatório periódico.
- Futuramente, alertas urgentes poderão ter confirmação de leitura e escalonamento.

## Próximas etapas

1. Confirmar o fluxo atual da Meta para criar/associar o aplicativo de desenvolvimento.
2. Obter acesso à API/Cloud API sem alterar a conexão atual do número.
3. Configurar webhook somente para recebimento/leitura inicialmente.
4. Testar recebimento de eventos do WhatsApp.
5. Criar armazenamento de conversas e estado.
6. Implementar classificação contextual.
7. Integrar e-mail.
8. Integrar Mercado Livre.
9. Integrar Bling.
10. Implementar alertas e resumos.
11. Depois de validar todos os processos, atualizar e consolidar a documentação e o código no GitHub.

## Segurança

Nunca armazenar no repositório:

- access tokens;
- app secrets;
- senhas;
- códigos de verificação;
- chaves privadas;
- credenciais de APIs.

Usar variáveis de ambiente/secrets quando a implementação começar.
