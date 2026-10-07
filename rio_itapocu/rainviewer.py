"""RainViewer (rede internacional de radares) como RESERVA VISUAL do radar da Defesa Civil de SC.

- Usado só para mostrar imagens quando o radar da Defesa Civil estiver parado há mais de 40 min.
- NÃO entra no cálculo da previsão (a menos que `chuva.rainviewer_no_calculo` seja ligado na config,
  o que só deve acontecer depois de a comparação com os pluviômetros mostrar que ele é confiável).
- A API gratuita devolve sempre o esquema de cores "Universal Blue"; a cor é convertida em dBZ pela
  tabela oficial do RainViewer e depois em mm/h pela mesma relação de Marshall-Palmer do radar principal.
Fonte: https://www.rainviewer.com/api.html (uso gratuito com atribuição).
"""
import datetime as dt
import io
import json
import math
import os

import requests
from PIL import Image

import radar

AQUI = os.path.dirname(os.path.abspath(__file__))
CACHE_CORES = os.path.join(AQUI, "cache_rainviewer_cores.json")
COMPARACAO = os.path.join(AQUI, "rainviewer_vs_pluvio.csv")
TABELA = "https://www.rainviewer.com/files/rainviewer_api_colors_table.csv"
MAPAS = "https://api.rainviewer.com/public/weather-maps.json"
UA = {"User-Agent": "Mozilla/5.0 (monitor-rio-itapocu)"}
Z = 7  # nível de zoom máximo da API gratuita (~1,2 km por pixel na região)


def _cores():
    """{(r,g,b): dBZ} do esquema Universal Blue, com cache em disco."""
    if os.path.exists(CACHE_CORES):
        with open(CACHE_CORES, encoding="utf-8") as f:
            return {tuple(json.loads(k)): v for k, v in json.load(f).items()}
    linhas = requests.get(TABELA, headers=UA, timeout=30).text.splitlines()
    col = linhas[0].split(",").index("Universal Blue")
    mapa = {}
    for ln in linhas[1:]:
        c = ln.split(",")
        h = c[col].strip().lstrip("#")
        if len(h) == 8 and h[6:] != "00":
            mapa[tuple(int(h[k:k + 2], 16) for k in (0, 2, 4))] = int(c[0])
    with open(CACHE_CORES, "w", encoding="utf-8") as f:
        json.dump({json.dumps(list(k)): v for k, v in mapa.items()}, f)
    return mapa


def _tile_xy(lat, lon):
    n = 2 ** Z
    la = math.radians(lat)
    return (lon + 180) / 360 * n, (1 - math.log(math.tan(la) + 1 / math.cos(la)) / math.pi) / 2 * n


def _cor_para_classe(dbz):
    """dBZ -> cor da paleta do radar da Defesa Civil (classe mais próxima por baixo)."""
    melhor = None
    for cor, d in sorted(radar.COR_DBZ.items(), key=lambda x: x[1]):
        if dbz >= d - 2:
            melhor = cor
    return melhor


def fator_visual(min_horas=2, faixa=(0.5, 10.0)):
    """Fator que leva a chuva do RainViewer à escala dos pluviômetros (só para a imagem na tela)."""
    pares = []
    if os.path.exists(COMPARACAO):
        with open(COMPARACAO, encoding="utf-8") as f:
            for ln in f.read().splitlines()[1:]:
                c = ln.split(",")
                if len(c) == 3 and c[1] and c[2] and (float(c[1]) > 0.2 or float(c[2]) > 0.2):
                    pares.append((float(c[1]), float(c[2])))
    pares = pares[-48:]  # últimas 48 h com chuva
    if len(pares) < min_horas or sum(a for a, _ in pares) <= 0:
        return 1.0, len(pares)
    return min(max(sum(b for _, b in pares) / sum(a for a, _ in pares), faixa[0]), faixa[1]), len(pares)


def _mm_para_dbz(mm):
    return 10 * math.log10(200 * mm ** 1.6) if mm > 0 else -99


def quadros(n=12, fator=1.0):
    """Últimos n quadros: [(t_local, imagem RGBA, {(x,y): mm/h} na bacia, {(x,y): mm/h} no recorte todo)].

    `fator` só muda as cores da imagem (escala dos pluviômetros); os valores em mm/h devolvidos
    continuam brutos, para a comparação não se contaminar com o próprio ajuste."""
    cores = _cores()
    d = requests.get(MAPAS, headers=UA, timeout=20).json()
    x0, y0 = radar.px(radar.REC[3], radar.REC[0])
    x1, y1 = radar.px(radar.REC[1], radar.REC[2])
    caixa = (int(x0), int(y0), int(x1) + 1, int(y1) + 1)
    mascara = set(radar.mascara())
    saida = []
    for fr in d["radar"]["past"][-n:]:
        base = d["host"] + fr["path"]
        tiles = {}
        img = Image.new("RGBA", (caixa[2] - caixa[0], caixa[3] - caixa[1]), (0, 0, 0, 0))
        mm, campo = {}, {}
        for y in range(caixa[1], caixa[3]):
            for x in range(caixa[0], caixa[2]):
                lon = radar.EXT[0] + (x + 0.5) / radar.W * (radar.EXT[2] - radar.EXT[0])
                lat = radar.EXT[3] - (y + 0.5) / radar.H * (radar.EXT[3] - radar.EXT[1])
                fx, fy = _tile_xy(lat, lon)
                k = (int(fx), int(fy))
                if k not in tiles:
                    r = requests.get(f"{base}/256/{Z}/{k[0]}/{k[1]}/2/0_0.png", headers=UA, timeout=20)
                    tiles[k] = Image.open(io.BytesIO(r.content)).convert("RGBA")
                p = tiles[k].getpixel((min(255, int((fx - k[0]) * 256)), min(255, int((fy - k[1]) * 256))))
                dbz = cores.get(p[:3]) if p[3] > 0 else None
                if dbz is not None:
                    cor = _cor_para_classe(_mm_para_dbz(radar.dbz_para_mm(dbz) * fator) if fator != 1.0 else dbz)
                    if cor:
                        img.putpixel((x - caixa[0], y - caixa[1]), cor + (255,))
                v_mm = radar.dbz_para_mm(dbz)
                campo[(x, y)] = v_mm * fator  # campo inteiro (com fator), para medir o deslocamento
                if (x, y) in mascara:
                    mm[(x, y)] = v_mm
        t = dt.datetime.fromtimestamp(fr["time"], dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=3)
        saida.append((t, img, mm, campo))
    return saida, caixa


def media_bacia(mm):
    return sum(mm.values()) / len(mm) if mm else 0.0


def registrar_comparacao(horario_rv, chuva_pluvio):
    """Guarda, por hora, a chuva média na bacia segundo o RainViewer e segundo os pluviômetros."""
    linhas = {}
    if os.path.exists(COMPARACAO):
        with open(COMPARACAO, encoding="utf-8") as f:
            for ln in f.read().splitlines()[1:]:
                c = ln.split(",")
                if len(c) == 3:
                    linhas[c[0]] = c
    for t, v in horario_rv.items():
        k = t.strftime("%Y-%m-%dT%H:%M")
        p = chuva_pluvio.get(t)
        antigo = linhas.get(k)
        linhas[k] = [k, f"{v:.2f}", f"{p:.2f}" if p is not None else (antigo[2] if antigo else "")]
    with open(COMPARACAO, "w", encoding="utf-8") as f:
        f.write("hora,rainviewer_mm_h,pluviometros_mm_h\n")
        for k in sorted(linhas):
            f.write(",".join(linhas[k]) + "\n")


def avaliar_comparacao(min_horas=24):
    """Confiabilidade do RainViewer contra os pluviômetros (horas com chuva em algum dos dois)."""
    if not os.path.exists(COMPARACAO):
        return {"horas": 0, "confiavel": False}
    pares = []
    with open(COMPARACAO, encoding="utf-8") as f:
        for ln in f.read().splitlines()[1:]:
            c = ln.split(",")
            if len(c) == 3 and c[1] and c[2]:
                a, b = float(c[1]), float(c[2])
                if a > 0.2 or b > 0.2:
                    pares.append((a, b))
    if len(pares) < 3:
        return {"horas": len(pares), "confiavel": False}
    n = len(pares)
    ma = sum(a for a, _ in pares) / n
    mb = sum(b for _, b in pares) / n
    cov = sum((a - ma) * (b - mb) for a, b in pares)
    va = math.sqrt(sum((a - ma) ** 2 for a, _ in pares))
    vb = math.sqrt(sum((b - mb) ** 2 for _, b in pares))
    corr = cov / (va * vb) if va and vb else 0.0
    erro = sum(abs(a - b) for a, b in pares) / max(sum(b for _, b in pares), 1e-9)
    return {"horas": n, "correlacao": round(corr, 2), "erro_relativo": round(erro, 2),
            "razao_media": round(ma / mb, 2) if mb else None,
            "confiavel": n >= min_horas and corr >= 0.7 and erro <= 0.30}
