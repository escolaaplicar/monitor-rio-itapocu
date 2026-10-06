"""Leitura da estação Epagri/CIRAM (AgroConnect): reimplementa a decodificação feita pela própria página do AgroConnect."""
import base64
import datetime as dt
import time
import urllib.parse

import requests

from fontes import UA, agora_local


# --------------------------------------------------------------------------- CIRAM
CIRAM = "https://ciram.epagri.sc.gov.br/agroconnect/"
_LPTYA = "CbkYTPgEQbNLja"
_CHK = "1A853d23"


def _ciram_marker(keyy):
    s, k = "", 0
    for i in range(0, len(_LPTYA), 2):
        s += keyy[k] + _LPTYA[i:i + 2]
        k += 1
    return s + str(k)


def _ciram_decode(txt, keyy):
    """Reimplementa a rotina ack3uk() do AgroConnect (base64 + XOR espelhado)."""
    txt = txt.replace("\r", "").replace("\n", "").strip()
    idx = txt.rfind(_ciram_marker(keyy))
    if idx >= 0:
        txt = txt[:idx]
    s = list(base64.b64decode(txt).decode("utf-8"))
    key = _CHK + keyy
    n, p = len(s), 0
    for i in range(n // 2):
        if p >= len(key):
            p = 0
        a = chr(ord(s[i]) ^ ord(key[p]))
        b = chr(ord(s[n - 1 - i]) ^ ord(key[p]))
        s[i], s[n - 1 - i] = b, a
        p += 1
    return "".join(s)


def ciram_mapa(var=271, grupo=4, nhoras=24):
    """Valor atual de todas as estações (var 271 = precipitação). nhoras=1 ou 24."""
    d = agora_local().strftime("%d-%m-%Y")
    dd, mm, yy = [float(x) for x in d.split("-")]
    tipo = "horario"
    h1 = int(nhoras ** 3 * yy * (mm * 12) * (30 * dd))
    h2 = 0
    now = int(time.time() * 1000)
    keyy = str(now)[:-5]
    q = (f"cd_estacao=0&cd_cultura=0&produto={tipo}&cd_variavel={var}&grupo={grupo}"
         f"&data={d}&nhoras={nhoras}&estado_=0&tipoEstacao_=todas&dt={now}&date={h1}{h2}"
         f"&idestacao=: 0&ka={keyy}")
    r = requests.post(CIRAM + "busca.jsp?" + urllib.parse.quote(q, safe="=&:/"),
                      headers={**UA, "Referer": CIRAM}, timeout=40)
    r.raise_for_status()
    out = []
    for row in _ciram_decode(r.text, keyy).split(";;"):
        c = row.split(",")
        if len(c) >= 9:
            try:
                out.append({"codigo": c[0], "nome": c[1], "lon": float(c[2]), "lat": float(c[3]),
                            "valor": float(c[4]), "municipio": c[5], "data": c[6],
                            "altitude": c[7], "dono": c[8]})
            except ValueError:
                pass
    return out


def ciram_horario(cd_estacao, inicio, fim, var=271):
    """Série horária de uma estação CIRAM. O valor rotulado HH:00 é o acumulado da hora que termina em HH:00."""
    url = (f"{CIRAM}grafico-horario.jsp?cd_estacao={cd_estacao}&cd_cultura=0&cd_variavel={var}"
           f"&data_inicio={inicio:%d/%m/%Y}&data_fim={fim:%d/%m/%Y}")
    r = requests.post(url, headers={**UA, "Referer": CIRAM}, timeout=40)
    r.raise_for_status()
    serie = {}
    for p in r.json():
        if p.get("y") is None:
            continue
        # x = hora local codificada como epoch UTC
        t = dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=p["x"])
        serie[t - dt.timedelta(hours=1)] = float(p["y"])
    return serie


