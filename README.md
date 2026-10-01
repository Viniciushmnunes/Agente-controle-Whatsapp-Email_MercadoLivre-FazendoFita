# Agente de Controle — Fazendo Fita

Projeto de monitoramento comercial e operacional da Fazendo Fita.

## Objetivo

Monitorar WhatsApp Business, e-mail, Mercado Livre e Bling para:

- evitar perda de vendas;
- identificar oportunidades comerciais;
- controlar mensagens sem resposta;
- acompanhar vendas e prazos do Mercado Livre;
- identificar problemas de pedidos, pagamentos, envio e operação;
- gerar alertas e resumos para acompanhamento humano.

## Princípio

O agente deve interpretar o contexto das conversas, e não classificar mensagens isoladamente.

Na fase inicial, o agente será **somente leitura/análise**: não responderá clientes automaticamente nem executará ações destrutivas.

## Fontes

1. WhatsApp Business da Fazendo Fita
2. E-mail da empresa
3. Mercado Livre
4. Bling (ERP)

## Arquitetura prevista

```
WhatsApp Business
      ↓
WhatsApp Business Platform / Cloud API
      ↓
Webhook
      ↓
Agente de interpretação e contexto
      ↓
Classificação + prioridade + oportunidades + pendências
      ↓
Alertas / resumos
      ↓
WhatsApp pessoal + e-mail + canal redundante
```

Também serão integrados posteriormente Mercado Livre e Bling.

## Status

O WhatsApp Business da Fazendo Fita já foi conectado à Plataforma do WhatsApp Business e continua funcionando normalmente para envio/recebimento de mensagens.

A próxima etapa é concluir o acesso de desenvolvimento/API e configurar o webhook, preservando a conexão atual.

Consulte `docs/estado-atual.md` para o histórico e as decisões já tomadas.
