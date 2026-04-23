"""
Escrow Monitor — Liqi Digital Assets
Busca emails do Itaú Escrow Advanced, parseia bloqueios/desbloqueios/transferências
e gera dashboard HTML estático.
"""

import os
import sys
import json
import re
import base64
from datetime import datetime
from pathlib import Path
from html.parser import HTMLParser

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SENDER = "bloqueiojudicialgarantias@itau-unibanco.com.br"
DATA_FILE = Path(__file__).parent / "data" / "events.json"
CONFIG_DIR = Path(__file__).parent / "config"


def get_gmail_service():
    """Autentica e retorna o serviço Gmail API."""
    creds = None
    token_path = CONFIG_DIR / "token.json"
    creds_path = CONFIG_DIR / "credentials.json"

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                print("ERRO: credentials.json não encontrado em config/")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def fetch_emails(service, after_date=None):
    """Busca todos os emails do remetente Escrow Advanced."""
    query = f"from:{SENDER}"
    if after_date:
        query += f" after:{after_date}"

    messages = []
    page_token = None

    while True:
        result = service.users().messages().list(
            userId="me", q=query, pageToken=page_token, maxResults=500
        ).execute()

        if "messages" in result:
            messages.extend(result["messages"])

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    print(f"Encontrados {len(messages)} emails de {SENDER}")
    return messages


def extract_text_from_html(html_content):
    """Extrai texto limpo do HTML do email."""
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.texts = []
            self.skip = False

        def handle_starttag(self, tag, attrs):
            if tag in ("style", "script"):
                self.skip = True

        def handle_endtag(self, tag):
            if tag in ("style", "script"):
                self.skip = False

        def handle_data(self, data):
            if not self.skip:
                self.texts.append(data)

    extractor = TextExtractor()
    extractor.feed(html_content)
    return " ".join(extractor.texts)


def get_email_body(service, msg_id):
    """Obtém o corpo HTML do email."""
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

    payload = msg.get("payload", {})

    def find_html(part):
        """Busca recursivamente a parte HTML do email."""
        mime = part.get("mimeType", "")
        if mime == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for sub in part.get("parts", []):
            result = find_html(sub)
            if result:
                return result
        return None

    html = find_html(payload)
    if not html:
        body_data = payload.get("body", {}).get("data", "")
        if body_data:
            html = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")

    return html


def parse_email(service, msg_id):
    """Parseia um email e extrai os dados estruturados."""
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    subject = headers.get("Subject", "")
    date_str = headers.get("Date", "")

    # Determina o tipo
    tipo = None
    if "BLOQUEIO JUDICIAL" in subject and "DESBLOQUEIO" not in subject:
        tipo = "BLOQUEIO"
    elif "DESBLOQUEIO JUDICIAL" in subject:
        tipo = "DESBLOQUEIO"
    elif "TRANSFERÊNCIA JUDICIAL" in subject or "TRANSFERENCIA JUDICIAL" in subject:
        tipo = "TRANSFERÊNCIA"

    if not tipo:
        return None

    html = get_email_body(service, msg_id)
    if not html:
        print(f"  Sem corpo HTML para {msg_id} ({subject})")
        return None

    text = extract_text_from_html(html)

    # Extrai campos do texto
    def extract_field(pattern, text, default=""):
        match = re.search(pattern, text)
        return match.group(1).strip() if match else default

    processo = extract_field(
        r"N[úu]mero\s+do\s+Processo\s+Judicial[:\s]+(\d+)", text
    )
    vara = extract_field(
        r"N[úu]mero\s+da\s+Vara\s+Civil[:\s]+(\d+)", text
    )
    ag_conta = extract_field(
        r"Ag\.?\s*Conta[:\s]+([\d/\-]+)", text
    )
    valor_str = extract_field(
        r"Valor\s+Efetivado\s+da\s+Ordem[:\s]+R\$\s*([\d.,]+)", text
    )
    data_efetivacao = extract_field(
        r"Data\s+da\s+efetiva[çc][ãa]o\s+da\s+Ordem[:\s]+([\d/]+)", text
    )
    contrato = extract_field(
        r"Contrato\s+de\s+Cust[óo]dia\s+de\s+Recursos\s+Financeiros\s+([\w\$\s]+?)(?:,|\s+informamos)",
        text
    )

    # Converte valor para float
    valor = 0.0
    if valor_str:
        valor = float(valor_str.replace(".", "").replace(",", "."))

    # Converte data
    data_iso = ""
    if data_efetivacao:
        try:
            dt = datetime.strptime(data_efetivacao, "%d/%m/%Y")
            data_iso = dt.strftime("%Y-%m-%d")
        except ValueError:
            data_iso = data_efetivacao

    return {
        "id": msg_id,
        "tipo": tipo,
        "processo": processo,
        "vara": vara,
        "ag_conta": ag_conta,
        "valor": valor,
        "valor_display": f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "data_efetivacao": data_iso,
        "data_email": date_str,
        "contrato": contrato.strip() if contrato else "",
        "subject": subject,
    }


def load_existing_data():
    """Carrega dados existentes do JSON."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"events": [], "last_update": "", "summary": {}}


def save_data(data):
    """Salva dados no JSON."""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def compute_summary(events):
    """Calcula resumo dos dados: saldo bloqueado, totais, processos ativos."""
    total_bloqueado = 0.0
    total_desbloqueado = 0.0
    total_transferido = 0.0

    processos = {}

    for ev in sorted(events, key=lambda x: x["data_efetivacao"]):
        proc = ev["processo"]
        if not proc:
            continue

        if proc not in processos:
            processos[proc] = {
                "processo": proc,
                "vara": ev["vara"],
                "bloqueios": [],
                "desbloqueios": [],
                "transferencias": [],
                "saldo_bloqueado": 0.0,
            }

        p = processos[proc]

        if ev["tipo"] == "BLOQUEIO":
            total_bloqueado += ev["valor"]
            p["saldo_bloqueado"] += ev["valor"]
            p["bloqueios"].append({
                "valor": ev["valor"],
                "data": ev["data_efetivacao"],
                "id": ev["id"],
            })
        elif ev["tipo"] == "DESBLOQUEIO":
            total_desbloqueado += ev["valor"]
            p["saldo_bloqueado"] -= ev["valor"]
            p["desbloqueios"].append({
                "valor": ev["valor"],
                "data": ev["data_efetivacao"],
                "id": ev["id"],
            })
        elif ev["tipo"] == "TRANSFERÊNCIA":
            total_transferido += ev["valor"]
            p["saldo_bloqueado"] -= ev["valor"]
            p["transferencias"].append({
                "valor": ev["valor"],
                "data": ev["data_efetivacao"],
                "id": ev["id"],
            })

    saldo_bloqueado_atual = total_bloqueado - total_desbloqueado - total_transferido

    processos_list = []
    for proc_id, p in processos.items():
        status = "BLOQUEADO"
        if p["saldo_bloqueado"] <= 0:
            if p["transferencias"]:
                status = "TRANSFERIDO"
            elif p["desbloqueios"]:
                status = "DESBLOQUEADO"
        processos_list.append({
            "processo": proc_id,
            "vara": p["vara"],
            "status": status,
            "saldo_bloqueado": round(p["saldo_bloqueado"], 2),
            "total_bloqueado": round(sum(b["valor"] for b in p["bloqueios"]), 2),
            "total_desbloqueado": round(sum(d["valor"] for d in p["desbloqueios"]), 2),
            "total_transferido": round(sum(t["valor"] for t in p["transferencias"]), 2),
            "bloqueios": p["bloqueios"],
            "desbloqueios": p["desbloqueios"],
            "transferencias": p["transferencias"],
        })

    processos_list.sort(key=lambda x: x["saldo_bloqueado"], reverse=True)

    return {
        "total_bloqueado": round(total_bloqueado, 2),
        "total_desbloqueado": round(total_desbloqueado, 2),
        "total_transferido": round(total_transferido, 2),
        "saldo_bloqueado_atual": round(saldo_bloqueado_atual, 2),
        "total_eventos": len(events),
        "total_processos": len(processos),
        "processos_ativos": len([p for p in processos_list if p["status"] == "BLOQUEADO"]),
        "processos": processos_list,
    }


def generate_html(data):
    """Gera o dashboard HTML estático."""
    summary = data["summary"]
    events = sorted(data["events"], key=lambda x: x["data_efetivacao"], reverse=True)
    last_update = data["last_update"]

    def fmt_brl(val):
        """Formata valor como BRL."""
        return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    # Tabela de processos
    processos_rows = ""
    for p in summary.get("processos", []):
        status_class = {
            "BLOQUEADO": "status-bloqueado",
            "DESBLOQUEADO": "status-desbloqueado",
            "TRANSFERIDO": "status-transferido",
        }.get(p["status"], "")

        processos_rows += f"""
        <tr>
          <td class="font-mono">{p['processo']}</td>
          <td>{p['vara']}</td>
          <td><span class="status-badge {status_class}">{p['status']}</span></td>
          <td class="text-right">{fmt_brl(p['total_bloqueado'])}</td>
          <td class="text-right">{fmt_brl(p['total_desbloqueado'])}</td>
          <td class="text-right">{fmt_brl(p['total_transferido'])}</td>
          <td class="text-right font-bold">{fmt_brl(p['saldo_bloqueado'])}</td>
        </tr>"""

    # Timeline de eventos
    timeline_rows = ""
    for ev in events:
        tipo_class = {
            "BLOQUEIO": "tipo-bloqueio",
            "DESBLOQUEIO": "tipo-desbloqueio",
            "TRANSFERÊNCIA": "tipo-transferencia",
        }.get(ev["tipo"], "")

        timeline_rows += f"""
        <tr>
          <td>{ev['data_efetivacao']}</td>
          <td><span class="tipo-badge {tipo_class}">{ev['tipo']}</span></td>
          <td class="font-mono">{ev['processo']}</td>
          <td>{ev['vara']}</td>
          <td class="text-right">{ev['valor_display']}</td>
        </tr>"""

    # Histórico de transferências
    transferencias = [ev for ev in events if ev["tipo"] == "TRANSFERÊNCIA"]
    transferencias_rows = ""
    for ev in transferencias:
        transferencias_rows += f"""
        <tr>
          <td>{ev['data_efetivacao']}</td>
          <td class="font-mono">{ev['processo']}</td>
          <td>{ev['vara']}</td>
          <td class="text-right">{ev['valor_display']}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Escrow Monitor — Liqi</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700&family=Inter:wght@400;500;600&family=Work+Sans:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {{
      --preto: #212121;
      --azul: #0247fe;
      --rosa: #f556e9;
      --azul-claro: #5cd5ee;
      --bg-light: #f2eee7;
      --bg-card: #ffffff;
      --cinza-texto: #606d7f;
      --cinza-borda: #e8eaed;
      --verde: #22c55e;
      --vermelho: #ef4444;
      --amarelo: #f59e0b;
    }}

    * {{ margin: 0; padding: 0; box-sizing: border-box; }}

    body {{
      font-family: 'Inter', 'Work Sans', sans-serif;
      background: var(--bg-light);
      color: var(--preto);
      min-height: 100vh;
    }}

    /* Header */
    .header {{
      background: var(--preto);
      padding: 1.25rem 2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .header-left {{
      display: flex;
      align-items: center;
      gap: 1.5rem;
    }}
    .header h1 {{
      font-family: 'Poppins', sans-serif;
      font-size: 1.25rem;
      font-weight: 600;
      color: #ffffff;
    }}
    .header .subtitle {{
      font-family: 'Work Sans', sans-serif;
      font-size: 0.85rem;
      color: #999;
      letter-spacing: -0.025em;
    }}
    .last-update {{
      font-size: 0.8rem;
      color: #888;
    }}

    /* Container */
    .container {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 2rem;
    }}

    /* Cards resumo */
    .cards {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 1rem;
      margin-bottom: 2rem;
    }}
    .card {{
      background: var(--bg-card);
      border-radius: 12px;
      padding: 1rem 1.25rem;
      border: 1px solid var(--cinza-borda);
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
      display: flex;
      flex-direction: column;
    }}
    .card-label {{
      font-size: 0.75rem;
      font-weight: 500;
      color: var(--cinza-texto);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      min-height: 2.5rem;
      display: flex;
      align-items: flex-end;
      margin-bottom: 0.5rem;
    }}
    .card-value {{
      font-family: 'Poppins', sans-serif;
      font-size: 1.35rem;
      font-weight: 700;
    }}
    .card-value.bloqueado {{ color: var(--azul); }}
    .card-value.desbloqueado {{ color: var(--verde); }}
    .card-value.transferido {{ color: #7b4ef3; }}
    .card-value.saldo {{ color: var(--vermelho); }}
    .card-value.comprometido {{
      background: linear-gradient(45deg, var(--azul), var(--rosa));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .card-sub {{
      font-size: 0.8rem;
      color: var(--cinza-texto);
      margin-top: 0.25rem;
    }}

    /* Tabs */
    .tabs {{
      display: flex;
      gap: 0;
      margin-bottom: 1.5rem;
      border-bottom: 2px solid var(--cinza-borda);
    }}
    .tab {{
      padding: 0.75rem 1.5rem;
      font-family: 'Inter', sans-serif;
      font-size: 0.9rem;
      font-weight: 500;
      color: var(--cinza-texto);
      background: none;
      border: none;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      margin-bottom: -2px;
      transition: all 0.2s;
    }}
    .tab:hover {{ color: var(--preto); }}
    .tab.active {{
      color: var(--azul);
      border-bottom-color: var(--azul);
    }}

    /* Tab content */
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}

    /* Tables */
    .table-wrapper {{
      background: var(--bg-card);
      border-radius: 12px;
      border: 1px solid var(--cinza-borda);
      overflow: hidden;
      box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    thead th {{
      background: #f8f9fa;
      padding: 0.75rem 1rem;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--cinza-texto);
      text-align: left;
      border-bottom: 1px solid var(--cinza-borda);
    }}
    tbody td {{
      padding: 0.75rem 1rem;
      font-size: 0.875rem;
      border-bottom: 1px solid #f0f0f0;
    }}
    tbody tr:hover {{
      background: #fafafa;
    }}
    .text-right {{ text-align: right; }}
    .font-mono {{ font-family: 'SF Mono', 'Cascadia Code', monospace; font-size: 0.8rem; }}
    .font-bold {{ font-weight: 600; }}

    /* Status badges */
    .status-badge {{
      display: inline-block;
      padding: 0.2rem 0.6rem;
      border-radius: 20px;
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .status-bloqueado {{ background: #fef2f2; color: var(--vermelho); }}
    .status-desbloqueado {{ background: #f0fdf4; color: var(--verde); }}
    .status-transferido {{ background: #eff6ff; color: var(--azul); }}

    .tipo-badge {{
      display: inline-block;
      padding: 0.2rem 0.6rem;
      border-radius: 20px;
      font-size: 0.7rem;
      font-weight: 600;
      letter-spacing: 0.03em;
    }}
    .tipo-bloqueio {{ background: #fef2f2; color: var(--vermelho); }}
    .tipo-desbloqueio {{ background: #f0fdf4; color: var(--verde); }}
    .tipo-transferencia {{ background: #eff6ff; color: var(--azul); }}

    /* Search */
    .search-bar {{
      display: flex;
      gap: 1rem;
      margin-bottom: 1rem;
    }}
    .search-input {{
      flex: 1;
      padding: 0.6rem 1rem;
      border: 1px solid var(--cinza-borda);
      border-radius: 8px;
      font-family: 'Inter', sans-serif;
      font-size: 0.875rem;
      outline: none;
      transition: border-color 0.2s;
    }}
    .search-input:focus {{
      border-color: var(--azul);
    }}

    /* Footer */
    .footer {{
      text-align: center;
      padding: 2rem;
      font-size: 0.75rem;
      color: var(--cinza-texto);
    }}

    /* Responsive */
    @media (max-width: 768px) {{
      .container {{ padding: 1rem; }}
      .cards {{ grid-template-columns: repeat(2, 1fr); }}
      .card-value {{ font-size: 1.1rem; }}
      .table-wrapper {{ overflow-x: auto; }}
      table {{ min-width: 700px; }}
    }}
  </style>
</head>
<body>
  <header class="header">
    <div class="header-left">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="75 170 1780 745"
           width="100" role="img" aria-label="Liqi Digital Assets">
        <defs><style>.liqi{{fill:#ffffff;}}</style></defs>
        <path class="liqi" d="M86.72,279.97c-3.33,2.71-5.31,6.74-5.39,11.03l1.46,267.51c.28,67.37,54.97,121.84,122.34,121.84h1.3c25.89.02,46.91-20.92,46.98-46.81h0c0-25.83-20.86-46.8-46.69-46.94h0c-12.98,0-23.51-10.51-23.53-23.49l-.54-203.1c0-46.48-50.16-84.1-80.29-84.1-6.52.04-10.7,0-15.63,4.05Z"/>
        <path class="liqi" d="M344.71,679.81h0c-27.54,0-49.86-22.33-49.86-49.86h0v-230.89c0-12.14,9.84-21.99,21.99-21.99h0c42.96,0,77.79,34.83,77.79,77.79,0,.07,0,.14,0,.21v175.09c-.12,27.47-22.43,49.68-49.91,49.66Z"/>
        <path class="liqi" d="M862.75,679.81h0c-27.54,0-49.87-22.33-49.87-49.86h0v-230.89c0-12.14,9.84-21.99,21.99-21.99h0c42.96.05,77.75,34.91,77.7,77.87,0,.04,0,.08,0,.13v175.09c-.11,27.44-22.38,49.63-49.82,49.66Z"/>
        <path class="liqi" d="M760.74,522.86l-.13-2.17c0-85.65-69.42-155.09-155.07-155.11h-1.8c-86.62-1.79-158.28,66.98-160.07,153.59-1.75,84.66,64.02,155.43,148.58,159.89,15.25.19,30.47-1.5,45.31-5.06,8.68-1.87,18.66-4.94,23.11-6.37v42.27h0c0,4.02.53,7.9,1.42,11.64,5.85,37.13,37.88,65.56,76.66,65.61,12.14,0,21.99-9.84,21.99-21.99v-104.24c0-1.76-.1-3.5-.27-5.21.18-59.38.27-132.86.27-132.86ZM603.84,584.63h-.09c-34.3,0-62.11-27.81-62.11-62.11s27.81-62.11,62.11-62.11,62.06,27.76,62.11,62.03c.05,34.3-27.73,62.15-62.03,62.2Z"/>
        <path class="liqi" d="M1399.58,181.15l-253.81,146.53c-23.62,13.64-38.17,38.83-38.17,66.1v293.07c0,27.27,14.55,52.47,38.17,66.1l253.81,146.53c23.62,13.64,52.71,13.63,76.33,0l127.21-73.49c11.01-6.36,17.79-18.1,17.79-30.81v-109.65c0-15.97,8.52-30.74,22.36-38.72l106.83-61.63c11.01-6.36,17.79-18.1,17.79-30.81v-160.59c0-27.27-14.55-52.47-38.17-66.1l-253.8-146.53c-23.62-13.64-52.71-13.64-76.33,0ZM1436.77,570.06v186.83c0,22.23-24.06,36.12-43.31,25.01l-161.81-93.42c-16.09-9.29-26-26.45-26-45.03v-206.83c0-18.57,9.91-35.74,26-45.03l179.13-103.42c16.09-9.29,35.9-9.29,51.99,0l161.8,93.42c19.25,11.11,19.25,38.9,0,50.02l-161.8,93.42c-16.09,9.29-25.99,26.45-25.99,45.02Z"/>
        <path class="liqi" d="M1685.14,827.63v-108.58c0-17.11,9.13-32.92,23.95-41.48l98.21-56.7c14.28-8.24,32.13,2.06,32.13,18.55v113.41c0,17.11-9.13,32.92-23.95,41.48l-94.04,54.29c-16.14,9.32-36.3-2.33-36.3-20.96Z"/>
      </svg>
      <div>
        <h1>Escrow Monitor</h1>
        <span class="subtitle">Bloqueios Judiciais — CASAS BAHIA I</span>
      </div>
    </div>
    <span class="last-update">Atualizado até {last_update}</span>
  </header>

  <div class="container">
    <!-- Cards resumo -->
    <div class="cards">
      <div class="card">
        <div class="card-label">Valor Histórico Bloqueado</div>
        <div class="card-value bloqueado">{fmt_brl(summary.get('total_bloqueado', 0))}</div>
        <div class="card-sub">{summary.get('total_processos', 0)} processos</div>
      </div>
      <div class="card">
        <div class="card-label">Total Desbloqueado</div>
        <div class="card-value desbloqueado">{fmt_brl(summary.get('total_desbloqueado', 0))}</div>
      </div>
      <div class="card">
        <div class="card-label">Total Transferido</div>
        <div class="card-value transferido">{fmt_brl(summary.get('total_transferido', 0))}</div>
      </div>
      <div class="card">
        <div class="card-label">Saldo Bloqueado Atual</div>
        <div class="card-value saldo">{fmt_brl(summary.get('saldo_bloqueado_atual', 0))}</div>
        <div class="card-sub">{summary.get('processos_ativos', 0)} processos ativos</div>
      </div>
      <div class="card">
        <div class="card-label">Total Comprometido (Bloqueado + Transferido)</div>
        <div class="card-value comprometido">{fmt_brl(summary.get('saldo_bloqueado_atual', 0) + summary.get('total_transferido', 0))}</div>
      </div>
    </div>

    <!-- Tabs -->
    <div class="tabs">
      <button class="tab active" data-tab="processos">Processos</button>
      <button class="tab" data-tab="timeline">Timeline</button>
      <button class="tab" data-tab="transferencias">Transferências</button>
    </div>

    <!-- Processos -->
    <div id="processos" class="tab-content active">
      <div class="search-bar">
        <input type="text" class="search-input" id="search-processos"
               placeholder="Buscar por número do processo ou vara...">
      </div>
      <div class="table-wrapper">
        <table id="table-processos">
          <thead>
            <tr>
              <th>Processo</th>
              <th>Vara</th>
              <th>Status</th>
              <th class="text-right">Bloqueado</th>
              <th class="text-right">Desbloqueado</th>
              <th class="text-right">Transferido</th>
              <th class="text-right">Saldo</th>
            </tr>
          </thead>
          <tbody>{processos_rows}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Timeline -->
    <div id="timeline" class="tab-content">
      <div class="search-bar">
        <input type="text" class="search-input" id="search-timeline"
               placeholder="Buscar por processo, tipo ou data...">
      </div>
      <div class="table-wrapper">
        <table id="table-timeline">
          <thead>
            <tr>
              <th>Data</th>
              <th>Tipo</th>
              <th>Processo</th>
              <th>Vara</th>
              <th class="text-right">Valor</th>
            </tr>
          </thead>
          <tbody>{timeline_rows}
          </tbody>
        </table>
      </div>
    </div>

    <!-- Transferências -->
    <div id="transferencias" class="tab-content">
      <div class="search-bar">
        <input type="text" class="search-input" id="search-transferencias"
               placeholder="Buscar por processo ou data...">
      </div>
      <div class="table-wrapper">
        <table id="table-transferencias">
          <thead>
            <tr>
              <th>Data</th>
              <th>Processo</th>
              <th>Vara</th>
              <th class="text-right">Valor</th>
            </tr>
          </thead>
          <tbody>{transferencias_rows}
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <footer class="footer">
    Liqi Digital Assets — Tecnologia que conecta
  </footer>

  <script>
    // Tabs
    document.querySelectorAll('.tab').forEach(tab => {{
      tab.addEventListener('click', () => {{
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(tab.dataset.tab).classList.add('active');
      }});
    }});

    // Search
    function setupSearch(inputId, tableId) {{
      const input = document.getElementById(inputId);
      if (!input) return;
      input.addEventListener('input', () => {{
        const term = input.value.toLowerCase();
        const rows = document.querySelectorAll('#' + tableId + ' tbody tr');
        rows.forEach(row => {{
          row.style.display = row.textContent.toLowerCase().includes(term) ? '' : 'none';
        }});
      }});
    }}
    setupSearch('search-processos', 'table-processos');
    setupSearch('search-timeline', 'table-timeline');
    setupSearch('search-transferencias', 'table-transferencias');
  </script>
</body>
</html>"""
    return html


def main():
    print("=== Escrow Monitor — Liqi Digital Assets ===")
    print(f"Execução: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    service = get_gmail_service()

    # Carrega dados existentes
    data = load_existing_data()
    existing_ids = {ev["id"] for ev in data["events"]}

    # Busca emails
    messages = fetch_emails(service)

    # Parseia novos emails
    new_count = 0
    for msg in messages:
        msg_id = msg["id"]
        if msg_id in existing_ids:
            continue

        print(f"  Parseando {msg_id}...")
        event = parse_email(service, msg_id)
        if event:
            data["events"].append(event)
            new_count += 1

    print(f"Novos eventos: {new_count}")
    print(f"Total de eventos: {len(data['events'])}")

    # Computa resumo
    data["summary"] = compute_summary(data["events"])
    datas_efetivacao = [ev["data_efetivacao"] for ev in data["events"] if ev.get("data_efetivacao")]
    if datas_efetivacao:
        ultima = max(datas_efetivacao)
        data["last_update"] = datetime.strptime(ultima, "%Y-%m-%d").strftime("%d/%m/%Y")
    else:
        data["last_update"] = datetime.now().strftime("%d/%m/%Y")

    # Salva JSON
    save_data(data)
    print(f"Dados salvos em {DATA_FILE}")

    # Gera HTML
    html_path = Path(__file__).parent / "index.html"
    html = generate_html(data)
    html_path.write_text(html, encoding="utf-8")
    print(f"Dashboard gerado em {html_path}")

    # Imprime resumo
    s = data["summary"]
    print(f"\n--- RESUMO ---")
    print(f"Total bloqueado:       {fmt_brl(s['total_bloqueado'])}")
    print(f"Total desbloqueado:    {fmt_brl(s['total_desbloqueado'])}")
    print(f"Total transferido:     {fmt_brl(s['total_transferido'])}")
    print(f"Saldo bloqueado atual: {fmt_brl(s['saldo_bloqueado_atual'])}")
    print(f"Processos: {s['total_processos']} ({s['processos_ativos']} ativos)")


def fmt_brl(val):
    return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


if __name__ == "__main__":
    main()
