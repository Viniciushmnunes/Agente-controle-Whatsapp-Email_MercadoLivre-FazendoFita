# Integração Bling e CRM — ativação

Implementação preparada em 11/10/2026. Ainda requer cadastro do aplicativo, autorização do proprietário e validação dos dados reais.

## O que está implementado

- OAuth 2.0 com JWT e renovação do acesso, credenciais cifradas no PostgreSQL.
- Autorização vinculada ao navegador por cookie e estado de uso único.
- Painel privado `/crm`, usuário `admin`, senha definida em CRM_ADMIN_PASSWORD.
- Importação a cada cinco minutos após conexão e seleção da loja. Não altera dados no Bling.
- Consulta de pedidos e contatos via API v3. Importa somente contatos dos pedidos da loja configurada.
- Histórico desde BLING_START_DATE. Reconsulta o histórico para atualizar cancelamentos antigos.
- Identificação por documento normalizado com HMAC; CPF/CNPJ completo não é persistido no CRM. Sem documento utilizável, mantém o contato Bling separado. Nomes não são usados para unir clientes.
- Número da loja + canal como chave do pedido; ID Bling como alternativa.
- Contagem de compras válidas por situações explicitamente confirmadas; desconhecidas ficam a conferir. Total dos pedidos não significa receita líquida após taxas.
- Dados de itens, valor, primeira/última compra, perfil de recorrência e lista de pedidos pendentes.
- Último snapshot preservado quando a API falha; erro visível no painel sem conteúdo da resposta ou credenciais.

## Criar o aplicativo

No Bling, Central de Extensões > Área do Integrador > Criar aplicativo. Escolher privado para uso interno, conforme opções disponibilizadas na sua conta.

Nome: Fazendo Fita - CRM e Monitoramento.
Descrição: Consulta de clientes e pedidos para histórico de compras, recorrência e acompanhamento interno de pendências.
Link de redirecionamento: https://fazendo-fita-webhook.onrender.com/bling/callback
Homepage: https://fazendo-fita-webhook.onrender.com/crm

Permissões necessárias: consulta de Pedidos de Venda, Contatos (Clientes e Fornecedores), Canais de Venda e Situações/Módulos. Selecionar somente consulta quando houver essa distinção. Conferir os nomes apresentados na conta, sem autorizar exclusões ou alterações desnecessárias.

## Configurar no Render

Adicionar todos os valores no Environment do serviço existente, não no GitHub ou no chat:

- BLING_CLIENT_ID e BLING_CLIENT_SECRET do aplicativo.
- BLING_ENCRYPTION_KEY: chave Fernet nova, mantida estável e guardada privadamente.
- CRM_ADMIN_PASSWORD: senha aleatória com pelo menos 20 caracteres.
- BLING_START_DATE: 2026-10-01 (ajustar para abranger vendas anteriores).

A chave e a senha devem ser geradas localmente e inseridas diretamente no Render. Não enviar capturas com valores visíveis. Não reutilizar o token do WhatsApp.

Depois da implantação, abrir `/crm`, entrar como admin e escolher Conectar ao Bling. Fazer login e conceder o acesso na página oficial. O retorno processa o código automaticamente; nunca copiar código ou tokens para o chat.

Após autorizar, consultar `/bling/stores`. A página privada mostra IDs, nomes e tipos de lojas; se houver mais de 100 lojas usar `?pagina=2`. Configurar BLING_STORE_ID com o ID do canal Mercado Livre correto. O sufixo “01” do nome da loja não é o ID da API.

Consultar `/bling/statuses` para os módulos, e `/bling/statuses?modulo=ID` para situações do módulo de pedidos de venda. Confirmar as situações da conta antes de definir:

- BLING_VALID_STATUS_IDS: IDs separados por vírgulas, compras consideradas válidas.
- BLING_EXCLUDED_STATUS_IDS: canceladas/devolvidas, fora da contagem.
- BLING_PENDING_STATUS_IDS: aguardando etiqueta/despacho, conferência operacional.

As situações pendentes podem também ser compras válidas se o pagamento estiver confirmado. Não há pressuposição de pagamento baseada só no nome da situação. Nenhuma classificação de recorrência é confirmada sem configuração das situações válidas.

## Conferência real

1. Aguardar a primeira importação, até cinco minutos após conexão/configuração.
2. Conferir o pedido 3 do Bling, número da loja 2000015447855219, com a tela original.
3. Conferir o outro pedido, produtos, valor, documento mascarado e situação.
4. Validar quantos pedidos contam como compras válidas e confirmar situações de cancelamento/devolução.
5. Só depois ativar distribuição dos avisos pelo canal escolhido.

## Limites atuais

- Não envia avisos por WhatsApp/email nem lembretes programados. O envio por WhatsApp depende da conexão pendente com o provedor; os pedidos/pendências ficam no painel.
- dataPrevista é uma previsão do Bling, não prazo confirmado de postagem. Lembretes de vencimento precisam de fonte validada do Mercado Livre para o prazo, além de confirmação de despacho.
- Reembolsos parciais e devoluções não refletidos em situação do pedido exigem integração adicional. Não inferir essas condições sem dados.
- Histórico é limitado aos pedidos disponíveis na API desde a data configurada. Lojas/canais diferentes precisam ser configurados e validados antes da consolidação.
- Importação completa é adequada ao baixo volume inicial. Conforme crescer, migrar para eventos e sincronização incremental com reconciliação.
- Manter uma instância web e um worker Gunicorn com quatro threads. A importação roda no mesmo serviço pago, sem criar serviço adicional. Locks PostgreSQL protegem imports e renovação durante implantações concorrentes.
- Testes automatizados usam SQLite e API simulada. PostgreSQL real, fluxo OAuth real e Docker/Render ainda precisam de validação após implantação. Nenhum dado real de comprador consta nos testes.
- O webhook Meta segue funcionando independentemente da configuração Bling. Sem BLING_CLIENT_ID, as rotas CRM não são ativadas.

Fontes oficiais consultadas em 11/10/2026:
- https://developer.bling.com.br/aplicativos
- https://developer.bling.com.br/migracao-jwt
- https://developer.bling.com.br/referencia
- https://developer.bling.com.br/perguntas-frequentes


## Atualização após conferência pelo proprietário — 11/10/2026

Regras específicas do canal 206307158 em crm_rules.json:
- Pedido Bling 1 é teste: excluído do painel, clientes, compras e pendências.
- Pedidos da loja 2000015410851473 e 2000015447855219 são compras reais, confirmadas individualmente. Não estende situação 6 a outros pedidos.
- Pedido 2 entregue conforme informação do responsável; sem pendência de despacho.
- Pedido 3 com despacho previsto para 13/10/2026 conforme responsável. A data não comprova despacho realizado; continuará a conferir até nova informação.
- Produtos e frete lidos dos campos totalProdutos e transporte.frete; total preservado separadamente. Soma por cliente usa produtos, não frete. Dados ausentes aguardam importação, nunca inferidos por subtração.
- Líquido do marketplace não é calculado por estimativa. Não confundir total com receita líquida nem lucro.
- Regras são confirmações manuais iniciais, não rastreamento logístico automático. Novos pedidos continuam a conferir; mudanças de situação para IDs de exclusão configurados prevalecem sobre confirmações anteriores.
- Avisos por WhatsApp/email e atualização automática da entrega continuam pendentes.
