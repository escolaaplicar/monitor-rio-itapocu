"""Radar meteorológico SC (mosaico C-MAX da Defesa Civil SC / CIRAM) -> chuva na bacia.

- Baixa os quadros de 10 em 10 min (PNG com paleta indexada de 14 cores).
- Cor -> dBZ (classes da paleta, ordem conferida contra o pluviômetro CEMADEN de Corupá em 06/10/2026).
- dBZ -> mm/h pela relação de Marshall-Palmer Z = 200 R^1.6.
- Média na bacia (Rio Novo + Humboldt a montante da confluência).
- Fator de viés radar/pluviômetro (correção do viés médio do campo) calculado com as horas recentes.
- Previsão imediata (nowcasting): estima o deslocamento das células por correlação
  entre quadros consecutivos e desloca o último campo para frente (até 3 h).
- Gera uma animação GIF (observado + previsão) para o painel.
"""
import datetime as dt
import glob
import math
import os

import requests
import urllib3
from PIL import Image, ImageDraw, ImageFont

urllib3.disable_warnings()  # o servidor da Defesa Civil tem cadeia de certificado incompleta

BASE = "https://sifap.defesacivil.sc.gov.br/radarsc/rest/radar/"
AQUI = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(AQUI, "cache_radar")
GIF = os.path.join(AQUI, "radar_animacao.gif")
QUADROS = os.path.join(AQUI, "radar_quadros.png")  # todos os quadros empilhados (o painel mostra um por vez)
EXT = (-58.0651279, -33.8163446, -46.4999942, -24.7653703)  # lon0, lat0, lon1, lat1 (mosaico COMP)
W, H = 920, 719

# cor RGB -> dBZ representativo da classe
COR_DBZ = {
    (165, 255, 255): 10, (110, 200, 255): 15, (55, 145, 255): 18, (0, 90, 255): 22,
    (170, 255, 0): 26, (128, 206, 0): 30, (85, 156, 0): 35, (43, 107, 0): 40, (0, 57, 0): 45,
    (255, 255, 0): 50, (255, 192, 0): 55, (255, 128, 0): 60, (255, 255, 255): 65,
}

# bacia a montante da confluência (aprox.): Rio Novo (oeste) + Humboldt (norte)
BACIA = [(-26.47, -49.47), (-26.36, -49.50), (-26.24, -49.42), (-26.22, -49.30),
         (-26.27, -49.17), (-26.40, -49.17), (-26.45, -49.22)]
# recorte da região exibida na animação
REC = (-49.75, -26.75, -48.85, -26.05)
# região usada nos cálculos (maior, para enxergar a chuva que vai chegar em até 3 h)
REC_CALC = (-50.45, -27.25, -48.25, -25.55)
CIDADES = {"Corupá": (-26.425, -49.243), "Jaraguá do Sul": (-26.486, -49.067),
           "São Bento do Sul": (-26.250, -49.379), "Schroeder": (-26.412, -49.073)}


def px(lat, lon):
    return ((lon - EXT[0]) / (EXT[2] - EXT[0]) * W, (EXT[3] - lat) / (EXT[3] - EXT[1]) * H)


def _get(url, **params):
    r = requests.get(BASE + url, params=params, timeout=40, verify=False)
    r.raise_for_status()
    return r


def baixar(horas=6):
    """Garante no cache os quadros das últimas `horas`. Devolve lista [(t_local, caminho)]."""
    os.makedirs(CACHE, exist_ok=True)
    agora_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    nomes = set(_get("getUltimasImagens", prod=4, radar="COMP", data="").json())
    t = agora_utc - dt.timedelta(hours=horas)
    while t < agora_utc - dt.timedelta(minutes=50):
        try:
            nomes.update(_get("getUltimasImagens", prod=4, radar="COMP", data=t.strftime("%Y-%m-%d%H:%M:%S")).json())
        except Exception:  # noqa: BLE001
            pass
        t += dt.timedelta(minutes=60)
    out = []
    for nm in sorted(nomes):
        tu = dt.datetime.strptime(nm[:12], "%Y%m%d%H%M")
        if tu < agora_utc - dt.timedelta(hours=horas, minutes=10):
            continue
        cam = os.path.join(CACHE, nm)
        if not os.path.exists(cam):
            try:
                open(cam, "wb").write(_get("getImagem", prod=4, radar="COMP", file=nm).content)
            except Exception:  # noqa: BLE001
                continue
        out.append((tu - dt.timedelta(hours=3), cam))
    # limpa cache com mais de 2 dias
    for c in glob.glob(os.path.join(CACHE, "*.png")):
        if os.path.getmtime(c) < (dt.datetime.now() - dt.timedelta(days=2)).timestamp():
            os.remove(c)
    return out


def _dentro(lat, lon, poli):
    dentro = False
    for i in range(len(poli)):
        (y1, x1), (y2, x2) = poli[i], poli[i - 1]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            dentro = not dentro
    return dentro


_MASCARA = None


def mascara():
    global _MASCARA
    if _MASCARA is None:
        x0, y1 = px(-26.50, -49.55)
        x1, y0 = px(-26.18, -49.12)
        _MASCARA = []
        for y in range(int(y0), int(y1) + 1):
            for x in range(int(x0), int(x1) + 1):
                lon = EXT[0] + (x + 0.5) / W * (EXT[2] - EXT[0])
                lat = EXT[3] - (y + 0.5) / H * (EXT[3] - EXT[1])
                if _dentro(lat, lon, BACIA):
                    _MASCARA.append((x, y))
    return _MASCARA


def dbz_para_mm(dbz):
    if dbz is None or dbz < 12:
        return 0.0
    return (10 ** (dbz / 10) / 200) ** (1 / 1.6)


def campo_mm(caminho):
    """Grade (dict (x,y)->mm/h) da região do recorte."""
    im = Image.open(caminho)
    pal = im.getpalette()
    x0, y0 = px(REC_CALC[3], REC_CALC[0])
    x1, y1 = px(REC_CALC[1], REC_CALC[2])
    g = {}
    for y in range(int(y0), int(y1) + 1):
        for x in range(int(x0), int(x1) + 1):
            i = im.getpixel((x, y))
            g[(x, y)] = dbz_para_mm(COR_DBZ.get(tuple(pal[3 * i:3 * i + 3])))
    return g


def media_bacia(g):
    m = mascara()
    return sum(g.get(p, 0.0) for p in m) / len(m)


def deslocamento(g_a, g_b, raio=6):
    """Vetor (dx, dy) em pixels que melhor leva o campo g_a em g_b (correlação máxima)."""
    chaves = [k for k, v in g_a.items() if v > 0.3]
    if len(chaves) < 30:
        return 0, 0
    melhor, best = (0, 0), -1
    for dx in range(-raio, raio + 1):
        for dy in range(-raio, raio + 1):
            s = sum(min(g_a[k], g_b.get((k[0] + dx, k[1] + dy), 0.0)) for k in chaves)
            if s > best:
                best, melhor = s, (dx, dy)
    return melhor


def processar(horas=6, horas_prev=3, chuva_pluvio=None):
    """Retorna dict com série de 10 min e horária (observado), previsão horária e caminho do GIF.

    chuva_pluvio: {t_hora_inicio: mm} média dos pluviômetros para calcular o fator de viés.
    """
    quadros = baixar(horas)
    if len(quadros) < 2:
        raise RuntimeError("radar sem quadros")
    campos = [(t, campo_mm(c), c) for t, c in quadros]
    serie10 = [(t, media_bacia(g)) for t, g, _ in campos]

    # horário: média das taxas dos quadros de cada hora
    acc = {}
    for t, v in serie10:
        k = t.replace(minute=0, second=0, microsecond=0)
        acc.setdefault(k, []).append(v)
    hor = {k: sum(v) / len(v) for k, v in acc.items()}

    # fator de viés radar x pluviômetro (horas completas com chuva)
    fator = 1.0
    if chuva_pluvio:
        pares = [(hor[k], chuva_pluvio[k]) for k in hor if k in chuva_pluvio and len(acc[k]) >= 4 and hor[k] > 0.3]
        if len(pares) >= 3 and sum(r for r, _ in pares) > 0:
            fator = min(max(sum(p for _, p in pares) / sum(r for r, _ in pares), 0.3), 3.0)

    # deslocamento médio dos últimos quadros (pixels por 10 min)
    # deslocamento entre quadros com 20 min de intervalo (mais estável); descarta vetores no limite da busca
    raio = 8
    vs = []
    for i in range(max(0, len(campos) - 7), len(campos) - 2):
        dx, dy = deslocamento(campos[i][1], campos[i + 2][1], raio)
        if abs(dx) < raio and abs(dy) < raio:
            vs.append((dx / 2, dy / 2))
    if vs:
        vx = sorted(v[0] for v in vs)[len(vs) // 2]
        vy = sorted(v[1] for v in vs)[len(vs) // 2]
    else:
        vx = vy = 0.0  # sem movimento confiável: persistência
    t_ult, g_ult, _ = campos[-1]

    def avancar(passos):
        dx, dy = round(vx * passos), round(vy * passos)
        return {(x + dx, y + dy): v for (x, y), v in g_ult.items()}

    prev10 = []
    passos_tot = horas_prev * 6
    for p in range(1, passos_tot + 1):
        g = avancar(p)
        decai = math.exp(-p / 18)  # persistência perde confiança: meia-vida ~2 h
        prev10.append((t_ult + dt.timedelta(minutes=10 * p), media_bacia(g) * decai))
    prev_hor = {}
    for t, v in prev10:
        k = t.replace(minute=0, second=0, microsecond=0)
        prev_hor.setdefault(k, []).append(v)
    prev_hor = {k: fator * sum(v) / 6 for k, v in prev_hor.items()}  # mm acumulados em cada hora

    quadros = _gif(campos[-12:], (vx, vy), t_ult)
    # reserva visual: RainViewer quando a Defesa Civil está parada há mais de 40 min (não entra no cálculo)
    reserva = None
    try:
        parado = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=3) - t_ult) > dt.timedelta(minutes=40)
        reserva = reserva_rainviewer({k: v for k, v in (chuva_pluvio or {}).items()}, mostrar=parado)
        if parado and reserva.get("quadros"):
            quadros = reserva.pop("quadros")
    except Exception as ex:  # noqa: BLE001
        reserva = {"erro": str(ex)[:200]}
    # legenda: cada cor do radar convertida em mm/h (Marshall-Palmer) e ajustada aos pluviômetros
    legenda = [{"cor": "#%02x%02x%02x" % c, "dbz": d, "mm_h": round(dbz_para_mm(d) * fator, 1)}
               for c, d in sorted(COR_DBZ.items(), key=lambda x: x[1]) if dbz_para_mm(d) > 0]
    km_h = math.hypot(vx * 1.26, vy * 1.40) * 6
    direcao = (math.degrees(math.atan2(vx, -vy)) + 360) % 360  # para onde vai (0 = norte)
    return {
        "ultimo_quadro": t_ult.strftime("%Y-%m-%dT%H:%M"),
        "fator_vies": round(fator, 2),
        "serie10": [(t.strftime("%Y-%m-%dT%H:%M"), round(v * fator, 2)) for t, v in serie10],
        "horario": {k: round(v * fator, 2) for k, v in hor.items()},
        "previsao_horaria": {k: round(v, 2) for k, v in prev_hor.items()},
        "previsao10": [(t.strftime("%Y-%m-%dT%H:%M"), round(v * fator, 2)) for t, v in prev10],
        "movimento": {"km_h": round(km_h, 1), "para_graus": round(direcao)},
        "taxa_atual_mm_h": round(serie10[-1][1] * fator, 1),
        "quadros": quadros,
        "rainviewer": reserva,
        "legenda": legenda,
    }


def _desenhista():
    """Recorte do painel e função que desenha um quadro (bacia, cidades e rótulo)."""
    x0, y0 = px(REC[3], REC[0])
    x1, y1 = px(REC[1], REC[2])
    caixa = (int(x0), int(y0), int(x1) + 1, int(y1) + 1)
    esc = 8
    fundo = (238, 241, 244)
    try:
        fonte = ImageFont.truetype("arial.ttf", 15)
        fonte_b = ImageFont.truetype("arialbd.ttf", 16)
    except OSError:
        fonte = fonte_b = ImageFont.load_default()

    def base(img_rgba, rotulo, prev=False):
        im = Image.new("RGB", img_rgba.size, fundo)
        im.paste(img_rgba, mask=img_rgba.split()[3])
        im = im.resize((im.width * esc, im.height * esc), Image.NEAREST)
        d = ImageDraw.Draw(im)
        poli = [((px(la, lo)[0] - caixa[0]) * esc, (px(la, lo)[1] - caixa[1]) * esc) for la, lo in BACIA]
        d.line(poli + [poli[0]], fill=(20, 20, 20), width=4)
        for nome, (la, lo) in CIDADES.items():
            x, y = (px(la, lo)[0] - caixa[0]) * esc, (px(la, lo)[1] - caixa[1]) * esc
            d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(200, 0, 0), outline=(255, 255, 255))
            d.text((x + 8, y - 9), nome, fill=(0, 0, 0), font=fonte, stroke_width=2, stroke_fill=(255, 255, 255))
        d.rectangle((0, 0, im.width, 28), fill=(255, 230, 150) if prev else (255, 255, 255))
        d.text((8, 5), rotulo, fill=(0, 0, 0), font=fonte_b)
        return im

    return caixa, base


def _gif(campos, vel, t_ult):
    """Animação: últimos quadros observados + 1 h de previsão, recortada sobre a bacia."""
    caixa, base = _desenhista()
    fundo = (238, 241, 244)

    frames = []
    for t, _, cam in campos:
        img = Image.open(cam).convert("RGBA").crop(caixa)
        frames.append(base(img, f"OBSERVADO  {t:%d/%m %H:%M} (hora local)  - radar Defesa Civil SC"))
    ult = Image.open(campos[-1][2]).convert("RGBA").crop(caixa)
    for p in (1, 2, 3, 4, 5, 6):
        dx, dy = round(vel[0] * p), round(vel[1] * p)
        img = Image.new("RGBA", ult.size, (0, 0, 0, 0))
        img.paste(ult, (dx, dy))
        modo = "deslocamento" if (vel[0] or vel[1]) else "persistência"
        frames.append(base(img, f"PREVISÃO ({modo})  {t_ult + dt.timedelta(minutes=10 * p):%H:%M}", prev=True))
    dur = [400] * (len(frames) - 1) + [1500]
    frames[0].save(GIF, save_all=True, append_images=frames[1:], duration=dur, loop=0)
    # tira vertical com todos os quadros, em paleta reduzida (arquivo pequeno); o painel navega por ela
    w, h = frames[0].size
    tira = Image.new("RGB", (w, h * len(frames)), fundo)
    for k, f in enumerate(frames):
        tira.paste(f, (0, k * h))
    tira.quantize(colors=96, method=Image.Quantize.MEDIANCUT).save(QUADROS, optimize=True)
    rotulos = [f"{t:%H:%M}" for t, _, _ in campos] + [f"{t_ult + dt.timedelta(minutes=10 * p):%H:%M}" for p in (1, 2, 3, 4, 5, 6)]
    return {"n": len(frames), "observados": len(campos), "rotulos": rotulos, "largura": w, "altura": h, "fonte": "Defesa Civil SC"}


def _tira(frames, rotulos, observados, fonte):
    fundo = (238, 241, 244)
    w, h = frames[0].size
    tira = Image.new("RGB", (w, h * len(frames)), fundo)
    for k, f in enumerate(frames):
        tira.paste(f, (0, k * h))
    tira.quantize(colors=96, method=Image.Quantize.MEDIANCUT).save(QUADROS, optimize=True)
    return {"n": len(frames), "observados": observados, "rotulos": rotulos, "largura": w, "altura": h, "fonte": fonte}


def reserva_rainviewer(chuva_pluvio=None, mostrar=False):
    """Busca o RainViewer: grava a comparação com os pluviômetros e, se `mostrar`, troca os quadros da tela."""
    import rainviewer
    fator, horas_fator = rainviewer.fator_visual()
    qs, caixa = rainviewer.quadros(12, fator=fator)
    hor = {}
    for t, _, mm, _c in qs:
        hor.setdefault(t.replace(minute=0, second=0, microsecond=0), []).append(rainviewer.media_bacia(mm))
    hor = {k: sum(v) / len(v) for k, v in hor.items() if len(v) >= 4}
    rainviewer.registrar_comparacao(hor, chuva_pluvio or {})
    info = {"ultimo_quadro": qs[-1][0].strftime("%Y-%m-%dT%H:%M") if qs else None,
            "taxa_bacia_mm_h": round(rainviewer.media_bacia(qs[-1][2]), 1) if qs else None,
            "avaliacao": rainviewer.avaliar_comparacao(),
            "fator_visual": round(fator, 2), "horas_fator": horas_fator}
    if mostrar and qs:
        _, base = _desenhista()
        frames = [base(img, f"RAINVIEWER (reserva)  {t:%d/%m %H:%M} (hora local)") for t, img, _, _c in qs]
        rotulos = [f"{t:%H:%M}" for t, _, _, _c in qs]
        # projeção de 1 h: mede para onde as manchas andam (quadros com 20 min de intervalo) e desloca a última imagem
        vs = []
        for k in range(max(0, len(qs) - 7), len(qs) - 2):
            dx, dy = deslocamento(qs[k][3], qs[k + 2][3], 8)
            if abs(dx) < 8 and abs(dy) < 8:
                vs.append((dx / 2, dy / 2))
        vx = sorted(v[0] for v in vs)[len(vs) // 2] if vs else 0.0
        vy = sorted(v[1] for v in vs)[len(vs) // 2] if vs else 0.0
        t_ult, img_ult = qs[-1][0], qs[-1][1]
        modo = "deslocamento" if (vx or vy) else "persistência"
        for p in range(1, 7):
            img = Image.new("RGBA", img_ult.size, (0, 0, 0, 0))
            img.paste(img_ult, (round(vx * p), round(vy * p)))
            tp = t_ult + dt.timedelta(minutes=10 * p)
            frames.append(base(img, f"RAINVIEWER: PROJEÇÃO ({modo})  {tp:%H:%M}", prev=True))
            rotulos.append(f"{tp:%H:%M}")
        info["movimento"] = {"km_h": round(math.hypot(vx * 1.26, vy * 1.40) * 6, 1),
                             "para_graus": round((math.degrees(math.atan2(vx, -vy)) + 360) % 360)}
        info["quadros"] = _tira(frames, rotulos, len(qs), "RainViewer")
    return info
