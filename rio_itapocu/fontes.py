"""Coleta de dados em tempo real.

Todas as séries são devolvidas como dict {datetime_local_inicio_da_hora: valor},
em hora local de Brasília (UTC-3, sem horário de verão).

Fontes:
  - Epagri/CIRAM AgroConnect  (estação 2399 Corupá-Guarajuva e outras) - chuva horária
  - CEMADEN                   (pluviômetro 6925 João Tozini, na confluência) - chuva horária, 96 h
  - ANA Telemetria            (82336000 Rio Humboldt, 82338000 Rio Ano Bom) - nível e vazão a cada ~30 min
  - Open-Meteo                (ECMWF IFS, GFS, ICON) - previsão de chuva horária
"""
import base64
import datetime as dt
import json
import os
import re
import time
import urllib.parse

import requests

UA = {"User-Agent": "Mozilla/5.0 (monitor-rio-itapocu)"}
TZ_OFFSET = dt.timedelta(hours=-3)


def agora_local():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + TZ_OFFSET


def hora_cheia(t):
    return t.replace(minute=0, second=0, microsecond=0)


# --------------------------------------------------------------------------- Epagri/CIRAM (opcional)
# A leitura da estação da Epagri fica em fontes_ciram.py.
try:
    from fontes_ciram import ciram_horario, ciram_mapa  # noqa: F401
    CIRAM_DISPONIVEL = True
except ImportError:
    CIRAM_DISPONIVEL = False


# --------------------------------------------------------------------------- CEMADEN
CEMADEN = "https://mapservices.cemaden.gov.br/MapaInterativoWS/resources/horario/{id}/{h}"


def cemaden_horario(idestacao, horas=96):
    """Chuva horária CEMADEN (rótulos em UTC, hora de início). Máx ~96 h."""
    r = requests.get(CEMADEN.format(id=idestacao, h=horas - 1), headers=UA, timeout=40)
    r.raise_for_status()
    d = r.json()
    horarios = [int(h.rstrip("h")) for h in d["horarios"]]
    serie = {}
    for data, valores in zip(d["datas"], d["acumulados"]):
        base = dt.datetime.strptime(data, "%d/%m/%Y")
        for h, v in zip(horarios, valores):
            if v is not None:
                # rótulo = fim do intervalo (conferido contra o radar em 06/10/2026)
                serie[base + dt.timedelta(hours=h - 1) + TZ_OFFSET] = round(float(v), 2)
    return serie, d.get("estacao", {})


# --------------------------------------------------------------------------- ANA
ANA = ("https://telemetriaws1.ana.gov.br/ServiceANA.asmx/DadosHidrometeorologicos"
       "?codEstacao={c}&dataInicio={i:%d/%m/%Y}&dataFim={f:%d/%m/%Y}")


CACHE_ANA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_ana")


_ANA_PRAZO = {"fim": 0.0}


def ana_prazo(segundos):
    """Define quanto tempo total a ANA pode consumir nesta atualização (o serviço às vezes trava)."""
    _ANA_PRAZO["fim"] = time.time() + segundos


def ana_telemetria(codigo, inicio, fim):
    """Lista de leituras [{t, nivel_m, vazao, chuva}] em ordem cronológica (hora local).

    O serviço da ANA dá erro 500 em períodos longos e às vezes para de responder, então:
    - busca um dia por vez, com tempo limite curto;
    - dias encerrados ficam em cache definitivo; hoje e ontem ficam em cache "parcial", usado se a ANA não responder;
    - respeita o prazo total definido por ana_prazo().
    """
    os.makedirs(CACHE_ANA, exist_ok=True)
    hoje = agora_local().date()
    dia, out, falhas = inicio.date(), [], []
    while dia <= fim.date():
        arq = os.path.join(CACHE_ANA, f"{codigo}_{dia:%Y%m%d}.json")
        parcial = os.path.join(CACHE_ANA, f"{codigo}_{dia:%Y%m%d}_parcial.json")
        recente = dia >= hoje - dt.timedelta(days=1)
        if not recente and os.path.exists(arq):
            with open(arq, encoding="utf-8") as f:
                out += [dict(x, t=dt.datetime.fromisoformat(x["t"])) for x in json.load(f)]
        else:
            lst, erro = None, "prazo da ANA esgotado nesta atualização"
            for tentativa in range(2):
                if _ANA_PRAZO["fim"] and time.time() > _ANA_PRAZO["fim"]:
                    break
                try:
                    lst = _ana_um_periodo(codigo, dia, dia)
                    break
                except Exception as ex:  # noqa: BLE001
                    erro = ex
                    time.sleep(1)
            if lst is None:
                falhas.append(f"{dia:%d/%m}: {erro}")
                if os.path.exists(parcial):  # último dado recebido
                    with open(parcial, encoding="utf-8") as f:
                        out += [dict(x, t=dt.datetime.fromisoformat(x["t"])) for x in json.load(f)]
            else:
                out += lst
                if lst:
                    with open(arq if not recente else parcial, "w", encoding="utf-8") as f:
                        json.dump([dict(x, t=x["t"].isoformat()) for x in lst], f)
        dia += dt.timedelta(days=1)
    if not out and falhas:
        raise RuntimeError("ANA sem resposta: " + "; ".join(falhas[-3:]))
    out = sorted({x["t"]: x for x in out}.values(), key=lambda x: x["t"])
    return out


def _ana_um_periodo(codigo, inicio, fim):
    r = requests.get(ANA.format(c=codigo, i=inicio, f=fim), headers=UA, timeout=(8, 20))
    r.raise_for_status()
    out = []
    for bloco in re.findall(r"<DadosHidrometereologicos[^>]*>(.*?)</DadosHidrometereologicos>", r.text, re.S):
        g = lambda k: (re.search(rf"<{k}>\s*([^<]*)", bloco) or [None, None])[1]
        t, n, q, c = g("DataHora"), g("Nivel"), g("Vazao"), g("Chuva")
        if not t:
            continue
        try:
            out.append({"t": dt.datetime.strptime(t.strip()[:19], "%Y-%m-%d %H:%M:%S"),
                        "nivel_m": float(n) / 100 if n and n.strip() else None,
                        "vazao": float(q) if q and q.strip() else None,
                        "chuva": float(c) if c and c.strip() else None})
        except ValueError:
            pass
    out.sort(key=lambda x: x["t"])
    return out


# --------------------------------------------------------------------------- Open-Meteo
OPENMETEO = "https://api.open-meteo.com/v1/forecast"


def openmeteo_previsao(pontos, modelos, dias=4):
    """Média espacial da chuva horária prevista, por modelo. {modelo: {t_inicio: mm}}."""
    params = {
        "latitude": ",".join(str(p[0]) for p in pontos),
        "longitude": ",".join(str(p[1]) for p in pontos),
        "hourly": "precipitation",
        "models": ",".join(modelos),
        "past_days": 1,
        "forecast_days": dias,
        "timezone": "America/Sao_Paulo",
    }
    r = requests.get(OPENMETEO, params=params, headers=UA, timeout=40)
    r.raise_for_status()
    dados = r.json()
    if isinstance(dados, dict):
        dados = [dados]
    out = {}
    for m in modelos:
        soma, cont = {}, {}
        for d in dados:
            h = d["hourly"]
            vals = h.get(f"precipitation_{m}") or (h.get("precipitation") if len(modelos) == 1 else None)
            if not vals:
                continue
            for ts, v in zip(h["time"], vals):
                if v is None:
                    continue
                # Open-Meteo: soma da hora anterior ao rótulo
                t = dt.datetime.fromisoformat(ts) - dt.timedelta(hours=1)
                soma[t] = soma.get(t, 0) + v
                cont[t] = cont.get(t, 0) + 1
        if soma:
            out[m] = {t: soma[t] / cont[t] for t in soma}
    return out


# --------------------------------------------------------------------------- SAMAE Jaraguá do Sul (réguas)
SAMAE = ("https://sistemas.samaejs.com.br:3000/grafana/api/public/dashboards/"
         "964bfc3d627a4d3db5f760c4654b7b57/panels/{}/query")
REGUAS = {  # painel de série temporal no Grafana público do SAMAE
    "corupa": {"painel": 67, "nome": "Rio Itapocu - Ponte Corupá (Rua Roberto Seidel)"},
    "nereu": {"painel": 58, "nome": "Rio Itapocu - Ponte Nereu Ramos (Jaraguá do Sul)"},
    "eta": {"painel": 57, "nome": "Rio Itapocu - SAMAE ETA Central (Jaraguá do Sul)"},
    "via_verde": {"painel": 56, "nome": "Rio Itapocu - Ponte Via Verde (Jaraguá do Sul)"},
}
HIST_REGUAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "historico_reguas.csv")


def samae_regua(painel):
    """Leituras de ~1 em 1 min das últimas ~21 h: [(t_local, nivel_m)] e cotas do painel.

    O Grafana devolve a hora local codificada como se fosse UTC (conferido com os boletins de 06/10/2026).
    """
    import urllib3
    urllib3.disable_warnings()
    r = requests.post(SAMAE.format(painel), json={"intervalMs": 60000, "maxDataPoints": 2000,
                      "timeRange": {"from": "now-24h", "to": "now", "timezone": "browser"}},
                      headers=UA, timeout=40, verify=False)  # cadeia de certificado incompleta no servidor
    r.raise_for_status()
    fr = r.json()["results"]["A"]["frames"][0]
    campos = dict(zip([f["name"] for f in fr["schema"]["fields"]], fr["data"]["values"]))
    serie = [(dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=t), n)
             for t, n in zip(campos["Time"], campos["Nível rio"]) if n is not None]
    cotas = {k: (campos[k][0] if campos.get(k) else None) for k in ("Normalidade", "Atenção", "Alerta", "Alerta máximo")}
    return serie, cotas


def reguas_com_historico():
    """Busca todas as réguas, grava no CSV de histórico e devolve {chave: [(t, nivel)]} com o histórico completo."""
    hist = {}
    if os.path.exists(HIST_REGUAS):
        with open(HIST_REGUAS, encoding="utf-8") as f:
            for linha in f.read().splitlines()[1:]:
                k, t, n = linha.split(",")
                hist.setdefault(k, {})[t] = float(n)
    novos, erros = [], []
    for k, info in REGUAS.items():
        try:
            serie, _ = samae_regua(info["painel"])
        except Exception as ex:  # noqa: BLE001
            erros.append(f"{k}: {ex}")
            continue
        for t, n in serie:
            ts = t.strftime("%Y-%m-%dT%H:%M")
            if ts not in hist.setdefault(k, {}):
                hist[k][ts] = n
                novos.append(f"{k},{ts},{n}")
    if novos:
        existe = os.path.exists(HIST_REGUAS)
        with open(HIST_REGUAS, "a", encoding="utf-8") as f:
            if not existe:
                f.write("regua,t,nivel_m\n")
            f.write("\n".join(novos) + "\n")
    out = {k: sorted((dt.datetime.fromisoformat(t), n) for t, n in v.items()) for k, v in hist.items()}
    return out, erros
