# Mercado Livre — acompanhamento de envios

Implementado em 11/10/2026. Ainda requer autorização real da conta via `/meli/connect` e validação dos pedidos conhecidos após retorno ao CRM.

Configuração no aplicativo: Authorization Code e Refresh Token; PKCE desabilitado nesta versão; Mercado Livre; Vendas e envios em Leitura; demais permissões opcionais sem acesso. Usuários é permissão padrão do portal.

Redirect: https://fazendo-fita-webhook.onrender.com/meli/callback
Notificações: https://fazendo-fita-webhook.onrender.com/meli/notifications
Tópicos: orders_v2 e shipments.

Variáveis: MELI_CLIENT_ID e MELI_CLIENT_SECRET, já adicionadas pelo proprietário ao Render. Usa a chave Fernet do CRM para armazenamento cifrado dos tokens; não alterar a chave existente.

- Autorização privada com cookie e estado de uso único. Renovação de token persistida e proteção de concorrência PostgreSQL.
- Consulta somente pedidos numéricos já importados do Bling; tenta identificar pack e suas orders, ou order direta quando pack retorna 404.
- Exige que todas as orders e o envio pertençam à conta autorizada. Packs multivendedor são recusados, sem acesso ou consolidação indevida.
- Consulta status/substatus em `/shipments/ID` e prazo em `/shipments/ID/sla` (`expected_date`). Ausência de prazo nunca é inferida pela previsão Bling nem por campo depreciado lead_time.
- Consulta a cada cinco minutos e após sinais de notificação. Notificações só sinalizam atualização, não são prova de status e não fornecem URLs a consultar. Validam application_id, user_id, tópico e formato limitado; dados canônicos são buscados na API com autorização.
- Não guarda endereço, documento, nome ou payload completo da API Mercado Livre. Mantém apenas IDs, status, prazo, data de consulta e erro seguro.
- Status e pagamento frescos prevalecem sobre confirmações manuais iniciais. Só status de order paid conta como compra válida automaticamente; outros estados requerem revisão. Dados com mais de quinze minutos são sinalizados como desatualizados.
- Não envia mensagens, emails ou alertas externos. Prazo aparece no painel; distribuição de lembretes e resumos continua pendente.
- Não atualiza os clientes ou os produtos do Bling, nem interfere na integração ML–Bling.
- Relações não suportadas, packs divididos ou respostas novas ficam a conferir; não inferir entrega nem despacho quando consulta falha.
- Testes com API simulada; validação real de OAuth, SLA e notificações deve ser feita após autorização do usuário.

Fontes oficiais consultadas:
https://developers.mercadolivre.com.br/devcenter/desenvolvimento-in-house
https://developers.mercadolivre.com.br/pt_br/gerenciamento-de-vendas/gestao-packs
https://developers.mercadolivre.com.br/pt_br/registre-o-seu-aplicativo/gerenciamento-de-envios
https://developers.mercadolivre.com.br/pt_br/lojas-oficiais/produto-receba-notificacoes
