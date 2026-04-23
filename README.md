# Escrow Monitor — Casas Bahia I

Dashboard de monitoramento de bloqueios, desbloqueios e transferências judiciais na conta escrow **Casas Bahia I** (Itaú Escrow Advanced).

## Dashboard

**https://liqi-da.github.io/escrow-monitor/**

## Como funciona

1. O script `escrow_monitor.py` conecta na Gmail API e busca emails de `bloqueiojudicialgarantias@itau-unibanco.com.br`
2. Parseia o HTML de cada email e extrai: tipo (bloqueio/desbloqueio/transferência), número do processo, vara civil, valor e data
3. Salva os eventos em `data/events.json` (histórico incremental)
4. Gera o dashboard HTML estático (`index.html`)

## Dados extraídos por evento

| Campo | Exemplo |
|-------|---------|
| Tipo | BLOQUEIO / DESBLOQUEIO / TRANSFERÊNCIA |
| Processo Judicial | 10010853720255020231 |
| Vara Civil | 000017849 |
| Ag. Conta | 8541/83571-9 |
| Valor | R$ 258,30 |
| Data Efetivação | 2026-04-15 |

## Atualização automática

GitHub Actions roda diariamente às **08:00 BRT** (11:00 UTC):
- Busca novos emails
- Atualiza `data/events.json`
- Regenera `index.html`
- Deploy via GitHub Pages

## Execução local

```bash
pip install -r requirements.txt
python escrow_monitor.py
```

Requer `config/credentials.json` e `config/token.json` (Google OAuth2 — Gmail API).

## Secrets do GitHub Actions

| Secret | Conteúdo |
|--------|----------|
| `GMAIL_CREDENTIALS` | JSON do OAuth2 client (credentials.json) |
| `GMAIL_TOKEN` | JSON do token do usuário (token.json) |

---

Liqi Digital Assets — Tecnologia que conecta
