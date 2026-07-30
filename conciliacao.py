"""
Conciliação e-mails x extrato bancário — Escrow Monitor / Liqi Digital Assets

Os e-mails do Itaú informam o número do processo e da vara, mas não são
confiáveis para saldo: alguns avisos nunca viram lançamento, alguns lançamentos
nunca geram aviso e o valor avisado às vezes difere do valor debitado.
O extrato é a fonte oficial de valores; o e-mail é a única fonte de processo.

Este módulo casa as duas bases e devolve as pendências.
"""

from datetime import date, datetime

# Casamento em estágios, do mais restrito ao mais tolerante.
# A data de efetivação informada no e-mail antecede o lançamento em 1 a 4 dias,
# por isso nenhum estágio exige data idêntica.
ESTAGIOS = [
    ("exato", 0, 7),            # mesmo valor, até 7 dias de defasagem
    ("arredondamento", 1.00, 15),
    ("atualizacao", -0.005, 20),  # negativo = tolerância percentual sobre o valor
]

# Pendências além destes limites deixam de ser "mesmo evento com valor
# divergente" e passam a contar como falta de um lado ou do outro.
PAR_TOLERANCIA_PCT = 0.10
PAR_TOLERANCIA_MIN = 100.00
PAR_TOLERANCIA_DIAS = 10

TIPO_EMAIL_PARA_EXTRATO = {
    "BLOQUEIO": "BLOQUEIO",
    "DESBLOQUEIO": "DESBLOQUEIO",
    "TRANSFERÊNCIA": "TRANSFERENCIA",
    "TRANSFERENCIA": "TRANSFERENCIA",
}


def _dia(iso):
    return datetime.strptime(iso, "%Y-%m-%d").date()


def _cent(valor):
    return int(round(float(valor) * 100))


def _defasagem(a, b):
    return abs((a - b).days)


def _preparar_eventos(events):
    """Normaliza os eventos dos e-mails, descartando os sem data utilizável."""
    prontos = []
    for ev in events:
        tipo = TIPO_EMAIL_PARA_EXTRATO.get(ev.get("tipo"))
        data_iso = ev.get("data_efetivacao") or ""
        if not tipo or len(data_iso) != 10:
            continue
        try:
            dia = _dia(data_iso)
        except ValueError:
            continue
        prontos.append({
            "data": dia,
            "tipo": tipo,
            "cent": _cent(ev.get("valor", 0)),
            "processo": ev.get("processo", ""),
            "vara": ev.get("vara", ""),
            "id": ev.get("id", ""),
        })
    return prontos


def _preparar_lancamentos(extrato):
    return [{"data": _dia(l["data"]), "tipo": l["tipo"], "cent": _cent(l["valor"])}
            for l in extrato.get("lancamentos", [])]


def _casar(eventos, lancamentos):
    """Casa eventos com lançamentos em estágios; devolve os órfãos de cada lado.

    Casamentos com valor diferente saem também em `divergentes`: se ficassem
    escondidos aqui, a ponte de saldo não fecharia.
    """
    usados, divergentes = set(), []
    pendentes = list(range(len(eventos)))
    exatos = 0

    for _, tol_valor, tol_dias in ESTAGIOS:
        restantes = []
        for i in pendentes:
            ev = eventos[i]
            limite = (int(ev["cent"] * abs(tol_valor)) if tol_valor < 0
                      else int(tol_valor * 100))
            candidatos = [
                j for j, l in enumerate(lancamentos)
                if j not in usados
                and l["tipo"] == ev["tipo"]
                and abs(l["cent"] - ev["cent"]) <= limite
                and _defasagem(l["data"], ev["data"]) <= tol_dias
            ]
            if candidatos:
                melhor = min(candidatos, key=lambda j: (
                    abs(lancamentos[j]["cent"] - ev["cent"]),
                    _defasagem(lancamentos[j]["data"], ev["data"]),
                ))
                usados.add(melhor)
                if lancamentos[melhor]["cent"] == ev["cent"]:
                    exatos += 1
                else:
                    divergentes.append((ev, lancamentos[melhor]))
            else:
                restantes.append(i)
        pendentes = restantes

    so_email = [eventos[i] for i in pendentes]
    so_extrato = [lancamentos[j] for j in range(len(lancamentos)) if j not in usados]
    return exatos, divergentes, so_email, so_extrato


def _parear_divergentes(so_email, so_extrato):
    """Identifica pares que são o mesmo evento lançado com valor diferente."""
    usados, pares = set(), []
    for ev in sorted(so_email, key=lambda e: -e["cent"]):
        candidatos = [
            j for j, l in enumerate(so_extrato)
            if j not in usados
            and l["tipo"] == ev["tipo"]
            and _defasagem(l["data"], ev["data"]) <= PAR_TOLERANCIA_DIAS
        ]
        if not candidatos:
            continue
        melhor = min(candidatos, key=lambda j: abs(so_extrato[j]["cent"] - ev["cent"]))
        limite = max(ev["cent"] * PAR_TOLERANCIA_PCT, PAR_TOLERANCIA_MIN * 100)
        if abs(so_extrato[melhor]["cent"] - ev["cent"]) <= limite:
            usados.add(melhor)
            pares.append((ev, so_extrato[melhor]))

    pareados = {id(p[0]) for p in pares}
    email_restante = [e for e in so_email if id(e) not in pareados]
    extrato_restante = [so_extrato[j] for j in range(len(so_extrato)) if j not in usados]
    return pares, email_restante, extrato_restante


def _liberados_sem_aviso(processos, so_extrato):
    """Processos que o dashboard ainda mostra bloqueados mas o extrato já liberou.

    O extrato não traz número de processo; a amarração é feita pelo valor exato
    do saldo bloqueado somado a um lançamento posterior ao bloqueio. É indício
    forte, não prova — por isso entra como alerta de conciliação, e não como
    baixa automática do evento.
    """
    disponiveis = {}
    for l in so_extrato:
        if l["tipo"] in ("DESBLOQUEIO", "TRANSFERENCIA"):
            disponiveis.setdefault(l["cent"], []).append(l)

    achados = []
    for p in sorted(processos, key=lambda x: -x["saldo_bloqueado"]):
        if p["status"] != "BLOQUEADO" or not p["bloqueios"]:
            continue
        alvo = _cent(p["saldo_bloqueado"])
        ultimo_bloqueio = max(b["data"] for b in p["bloqueios"])
        for cand in list(disponiveis.get(alvo, [])):
            if cand["data"].isoformat() > ultimo_bloqueio:
                disponiveis[alvo].remove(cand)
                achados.append({
                    "processo": p["processo"],
                    "vara": p["vara"],
                    "valor": round(alvo / 100, 2),
                    "data_bloqueio": ultimo_bloqueio,
                    "tipo_extrato": cand["tipo"],
                    "data_extrato": cand["data"].isoformat(),
                })
                break
    return achados


def _totais(itens):
    return {t: round(sum(i["cent"] for i in itens if i["tipo"] == t) / 100, 2)
            for t in ("BLOQUEIO", "DESBLOQUEIO", "TRANSFERENCIA")}


def _liquido(totais):
    return round(totais["BLOQUEIO"] - totais["DESBLOQUEIO"] - totais["TRANSFERENCIA"], 2)


def conciliar(events, extrato, summary):
    """Concilia os eventos dos e-mails contra o extrato e devolve o diagnóstico."""
    if not extrato or not extrato.get("lancamentos"):
        return None

    eventos = _preparar_eventos(events)
    lancamentos = _preparar_lancamentos(extrato)
    exatos, divergentes, so_email, so_extrato = _casar(eventos, lancamentos)
    pares, so_email, so_extrato = _parear_divergentes(so_email, so_extrato)
    pares = divergentes + pares
    casados = exatos + len(pares)

    tot_email, tot_extrato = _totais(so_email), _totais(so_extrato)
    delta_pares = round(sum(
        ((e["cent"] if e["tipo"] == "BLOQUEIO" else -e["cent"])
         - (l["cent"] if l["tipo"] == "BLOQUEIO" else -l["cent"]))
        for e, l in pares) / 100, 2)

    saldo_extrato = extrato["totais"]["SALDO_BLOQUEADO"]
    saldo_email = summary.get("saldo_bloqueado_atual", 0.0)

    # A ponte tem que fechar: se não fechar, algum resíduo está sendo engolido.
    ponte_soma = round(saldo_extrato + _liquido(tot_email)
                       - _liquido(tot_extrato) + delta_pares, 2)
    residuo = round(saldo_email - ponte_soma, 2)

    return {
        "ponte_residuo": residuo,
        "eventos_casados_exatos": exatos,
        "extrato": {
            "arquivo": extrato.get("arquivo", ""),
            "gerado_em": extrato.get("gerado_em", ""),
            "importado_em": extrato.get("importado_em", ""),
            "periodo": extrato.get("periodo", ""),
            "ultimo_lancamento": extrato.get("ultimo_lancamento", ""),
            "agencia": extrato.get("agencia", ""),
            "conta": extrato.get("conta", ""),
            "saldo_conta_corrente": extrato.get("saldo_conta_corrente", 0.0),
            "totais": extrato["totais"],
        },
        "eventos_conciliados": casados,
        "eventos_totais": len(eventos),
        "lancamentos_totais": len(lancamentos),
        "saldo_bloqueado_extrato": saldo_extrato,
        "saldo_bloqueado_emails": saldo_email,
        "divergencia_saldo": round(saldo_email - saldo_extrato, 2),
        "ponte": {
            "extrato": saldo_extrato,
            "so_email": _liquido(tot_email),
            "so_extrato": -_liquido(tot_extrato),
            "valores_divergentes": delta_pares,
            "emails": saldo_email,
        },
        "valores_divergentes": [{
            "tipo": e["tipo"],
            "processo": e["processo"],
            "data_email": e["data"].isoformat(),
            "valor_email": round(e["cent"] / 100, 2),
            "data_extrato": l["data"].isoformat(),
            "valor_extrato": round(l["cent"] / 100, 2),
            "diferenca": round((l["cent"] - e["cent"]) / 100, 2),
        } for e, l in sorted(pares, key=lambda p: -abs(p[1]["cent"] - p[0]["cent"]))],
        "so_no_email": [{
            "tipo": e["tipo"],
            "processo": e["processo"],
            "vara": e["vara"],
            "data": e["data"].isoformat(),
            "valor": round(e["cent"] / 100, 2),
        } for e in sorted(so_email, key=lambda e: (e["data"], -e["cent"]))],
        "so_no_extrato": [{
            "tipo": l["tipo"],
            "data": l["data"].isoformat(),
            "valor": round(l["cent"] / 100, 2),
        } for l in sorted(so_extrato, key=lambda l: (l["data"], -l["cent"]))],
        "totais_so_email": tot_email,
        "totais_so_extrato": tot_extrato,
        "liberados_sem_aviso": _liberados_sem_aviso(
            summary.get("processos", []), so_extrato),
    }
