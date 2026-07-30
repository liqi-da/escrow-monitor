# Escrow Monitor — Casas Bahia I

Dashboard de monitoramento de bloqueios, desbloqueios e transferências judiciais na conta escrow **Casas Bahia I** (Itaú Escrow Advanced), conciliado contra o extrato bancário.

## Dashboard

**https://liqi-da.github.io/escrow-monitor/**

## Duas fontes, papéis diferentes

| Fonte | Serve para | Não serve para |
|-------|-----------|----------------|
| **E-mails** do Itaú | número do processo, vara — informação que só existe aqui | saldo: alguns avisos nunca viram lançamento, o valor avisado às vezes difere do debitado |
| **Extrato bancário** (.xlsx) | saldo e valores efetivamente lançados — fonte oficial | identificar o processo: o extrato não traz o número |

O dashboard usa o extrato para o saldo bloqueado e os e-mails para atribuir cada movimento a um processo. A aba **Conciliação** mostra onde as duas bases divergem e a data do extrato usado.

## As três contas do contrato

O Contrato de Custódia de Recursos Financeiros T2 (ID 1034480, de 31/03/2026) abre três Contas Vinculadas na agência 8541:

| Conta | Nome no Contrato de Custódia | Conta Vinculada de qual garantia | Cobertura |
|-------|------------------------------|----------------------------------|-----------|
| **83571-9** | Conta Fluxos Casas Bahia | Conta Vinculada – Casas Bahia (Alienação e Cessão Fiduciária de Ações) | e-mails + extrato |
| 83534-7 | Conta Controlada Casas Bahia | Conta Vinculada (Cessão Fiduciária de Direitos Creditórios) | tripwire |
| 83563-6 | Conta Controlada Lake | Conta Vinculada – Lake (Alienação e Cessão Fiduciária de Ações) | tripwire |

As duas Contas de Liberação Controlada estão zeradas desde o início da operação. Elas entram no dashboard como **tripwire**: aparecem no mapa da aba Garantia e, se algum dia surgir evento nelas, sobe uma faixa vermelha no topo. O mesmo vale para conta que não esteja nas três — o contrato não prevê nenhuma outra.

Atenção ao cruzamento, porque os nomes do Contrato de Custódia não seguem os dos contratos de garantia:

- A **83534-7** é a Conta Vinculada do **Contrato de Cessão Fiduciária de Direitos Creditórios** — é lá que a cl. 6.2/6.3 manda manter o Saldo Mínimo Retido de R$ 30 mi e a cl. 6.1(ii) manda depositar os recebíveis de antecipação da FIC. Está zerada.
- A **83571-9** e a **83563-6** são as Contas Vinculadas do **Contrato de Alienação e Cessão Fiduciária de Ações**, cuja cl. 6.1 manda varrer os recursos para a **Conta Centralizadora da Liqi** (ag. 2419, cc 96933-8) em até 5 dias úteis.
- O colchão está de fato na **83571-9**, e o Anexo I do Contrato de Custódia varre o excedente na direção oposta — para a conta livre da Casas Bahia (0018/51133-7) no dia útil seguinte.

## Saldo Mínimo Retido — aba Garantia

A cl. 6.3 do Contrato de Cessão Fiduciária (espelhada na cl. 2.1 do Anexo I do Contrato de Custódia) exige **R$ 30.000.000** retidos na conta. A cl. 2.9 do mesmo anexo determina que **valores bloqueados por ordem judicial não compõem esse saldo** — ou seja, cada bloqueio é um furo direto no colchão, e a reposição cabe à Casas Bahia.

A aba **Garantia** apura:

- **Colchão efetivo** = saldo livre em conta (o bloqueado não conta), com % de cobertura e déficit
- **Ponte do colchão**: créditos recebidos + rendimentos − transferido judicialmente − bloqueado
- **Prova de caixa**: créditos + rendimentos − transferências deve igualar saldo livre + bloqueado. Resíduo perto de zero significa que não há recursos aplicados fora da conta corrente; resíduo relevante indica aplicação financeira, e aí o saldo aplicado precisa ser conferido no Itaú na Internet
- **Mapa das Contas Vinculadas** com o papel contratual de cada uma

## Como funciona

1. `escrow_monitor.py` conecta na Gmail API e busca e-mails de `bloqueiojudicialgarantias@itau-unibanco.com.br`
2. Parseia o HTML de cada e-mail e extrai tipo, número do processo, vara, valor e data
3. Salva os eventos em `data/events.json` (histórico incremental)
4. Concilia contra `data/extrato.json` (`conciliacao.py`)
5. Gera o dashboard HTML estático (`index.html`)

### Dados extraídos por evento

| Campo | Exemplo |
|-------|---------|
| Tipo | BLOQUEIO / DESBLOQUEIO / TRANSFERÊNCIA |
| Processo Judicial | 10010853720255020231 |
| Vara Civil | 000017849 |
| Ag. Conta | 8541/83571-9 |
| Valor | R$ 258,30 |
| Data Efetivação | 2026-04-15 |

## Atualizando o extrato bancário

Baixe o **Extrato de Lançamentos** (.xlsx) no Itaú Escrow Advanced e rode:

```bash
python importar_extrato.py "caminho/Extrato_Lançamentos_8541_835719_30-07-2026.xlsx"
python escrow_monitor.py --offline
git add data/ index.html && git commit -m "Extrato conciliado até 30/07/2026" && git push
```

O dashboard passa a exibir a data de geração e de importação do extrato no topo. Enquanto um extrato novo não for importado, o painel continua sendo atualizado pelos e-mails, mas o saldo conciliado permanece na data do último extrato.

### Detalhe do parsing do extrato

Toda `TRANSF JUDICIAL` vem precedida de um `DESBLOQUEIO JUDICIAL` de mesmo valor no mesmo dia — é o estorno contábil que antecede a transferência, não uma liberação de recurso. O `importar_extrato.py` neutraliza esses pares; sem isso o total de desbloqueios fica inflado pelo valor de todas as transferências.

## O que a conciliação aponta

- **Ponte de saldo** entre extrato e e-mails, item a item
- **Processos ainda marcados como bloqueados que o extrato já baixou** — amarrados pelo valor exato do saldo somado a um lançamento posterior ao bloqueio. É indício para conferência manual, não baixa automática
- **Eventos lançados com valor diferente do avisado** — o e-mail informa um valor, o banco debita outro
- **Avisados por e-mail, sem lançamento no extrato**
- **Lançados no extrato, sem e-mail correspondente** — lotes de aviso perdidos; nos últimos dias costuma ser só defasagem, o e-mail do Itaú chega de 1 a 4 dias após o lançamento

## Execução local

```bash
pip install -r requirements.txt
python escrow_monitor.py              # busca e-mails novos e regenera o dashboard
python escrow_monitor.py --offline    # só reprocessa e regenera, sem tocar no Gmail
```

Requer `config/credentials.json` e `config/token.json` (Google OAuth2 — Gmail API), exceto no modo `--offline`.

## Atualização automática

GitHub Actions roda diariamente às **08:00 BRT** (11:00 UTC): busca novos e-mails, atualiza `data/events.json`, refaz a conciliação, regenera `index.html` e publica no GitHub Pages.

| Secret | Conteúdo |
|--------|----------|
| `GMAIL_CREDENTIALS` | JSON do OAuth2 client (credentials.json) |
| `GMAIL_TOKEN` | JSON do token do usuário (token.json) |

---

Liqi Digital Assets — Tecnologia que conecta
