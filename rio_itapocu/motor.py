"""Junta as fontes, roda o modelo e produz o pacote de dados do painel."""
import datetime as dt
import json
import math
import os
import traceback

import fontes
import modelo

AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(AQUI, "config.json")
LEITURAS = os.path.join(AQUI, "leituras.json")
H = dt.timedelta(hours=1)


CONFIG_LOCAL = os.path.join(AQUI, "config.local.json")
CALIB_AUTO = os.path.join(AQUI, "calibracao_auto.json")
PREV_1H = os.path.join(AQUI, "previsoes_1h.csv")


def _mesclar(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _mesclar(base[k], v)
        else:
            base[k] = v
    return base


def carregar_config():
    """config.json (público, no repositório) + config.local.json (só no PC, fora do git), se existir."""
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    if os.path.exists(CONFIG_LOCAL):
        with open(CONFIG_LOCAL, encoding="utf-8") as f:
            _mesclar(cfg, json.load(f))
    if os.path.exists(CALIB_AUTO):
        with open(CALIB_AUTO, encoding="utf-8") as f:
            ca = json.load(f)
        if ca.get("aceita"):
            cfg["hbv"] = dict(ca["hbv"], _calibracao=ca.get("descricao", ""))
            cfg["curva_chave"] = dict(cfg.get("curva_chave", {}), **ca["curva"])
    return cfg


def salvar_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def carregar_leituras():
    if not os.path.exists(LEITURAS):
        return []
    with open(LEITURAS, encoding="utf-8") as f:
        return json.load(f)


def salvar_leituras(lst):
    with open(LEITURAS, "w", encoding="utf-8") as f:
        json.dump(lst, f, ensure_ascii=False, indent=2)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M")


def _horario_medio(leituras, campo):
    """Leituras ANA (~30 min) -> média por hora cheia {t_hora: valor}."""
    acc = {}
    for l in leituras:
        v = l.get(campo)
        if v is None:
            continue
        k = fontes.hora_cheia(l["t"])
        acc.setdefault(k, []).append(v)
    return {k: sum(v) / len(v) for k, v in acc.items()}


def rodar(registrar_previsao=True):
    cfg = carregar_config()
    agora = fontes.agora_local()
    h_atual = fontes.hora_cheia(agora)
    dias = cfg["hbv"]["dias_aquecimento"]
    t0 = h_atual - dt.timedelta(days=dias)
    horas_fut = 96
    tempos = [t0 + i * H for i in range(int((h_atual - t0) / H) + horas_fut)]
    i_agora = tempos.index(h_atual)
    erros, fontes_ok = [], {}

    # ---------------------------------------------------------------- telemetria ANA (vazão, nível e chuva)
    fontes.ana_prazo(cfg.get("ana_prazo_s", 90))
    cal = cfg["calibracao"]
    obs_ana = {}
    for cod in [cal["estacao_ana"]] + [e["codigo"] for e in cal.get("estacoes_info", [])]:
        try:
            lst = fontes.ana_telemetria(cod, t0, agora)
            obs_ana[cod] = lst
            fontes_ok[f"ANA {cod}"] = {"ok": bool(lst), "ultima": _iso(lst[-1]["t"]) if lst else None}
        except Exception as ex:  # noqa: BLE001
            obs_ana[cod] = []
            erros.append(f"ANA {cod}: {ex}")
            fontes_ok[f"ANA {cod}"] = {"ok": False, "erro": str(ex)[:200]}

    # ---------------------------------------------------------------- chuva observada
    chuva_est = {}
    for e in cfg["chuva"]["estacoes"]:
        try:
            if e["fonte"] == "ciram":
                if not fontes.CIRAM_DISPONIVEL:
                    continue
                s = fontes.ciram_horario(e["id"], t0, agora)
            elif e["fonte"] == "ana":
                s, n_leit = {}, {}
                for l in obs_ana.get(str(e["id"])) or fontes.ana_telemetria(str(e["id"]), t0, agora):
                    c = l["chuva"]
                    if c is not None and 0 <= c <= 40:  # descarta leituras de contador corrompidas
                        k = fontes.hora_cheia(l["t"])
                        s[k] = round(s.get(k, 0) + c, 2)
                        n_leit[k] = n_leit.get(k, 0) + 1
                # a ANA lê a cada 30 min: hora com uma leitura só ainda está incompleta e subestimaria a chuva
                s = {k: v for k, v in s.items() if n_leit.get(k, 0) >= 2}
            else:
                s, _ = fontes.cemaden_horario(e["id"], 96)
            chuva_est[e["nome"]] = s
            ult = max(s) if s else None
            fontes_ok[e["nome"]] = {"ok": True, "ultima": _iso(ult + H) if ult else None}
        except Exception as ex:  # noqa: BLE001
            erros.append(f"{e['nome']}: {ex}")
            fontes_ok[e["nome"]] = {"ok": False, "erro": str(ex)[:200]}

    pesos = {e["nome"]: e["peso"] for e in cfg["chuva"]["estacoes"]}
    chuva_obs = []
    for t in tempos[:i_agora + 1]:
        num = den = 0.0
        for nome, s in chuva_est.items():
            if t in s:
                num += s[t] * pesos[nome]
                den += pesos[nome]
        chuva_obs.append(num / den if den else None)

    # ----- hora corrente: CEMADEN em tempo real (as séries horárias só fecham a hora no fim dela)
    chuva_tr = None
    id_cem = next((e["id"] for e in cfg["chuva"]["estacoes"] if e["fonte"] == "cemaden"), None)
    if id_cem:
        try:
            rt = fontes.cemaden_tempo_real(id_cem)
            if rt and agora - rt[0] <= dt.timedelta(minutes=25):
                chuva_tr = {"t": _iso(rt[0]), "mm_ultima_hora": rt[1]}
                # o acumulado cobre os 60 min anteriores à leitura: vai para a hora que contém o meio desse intervalo
                k_tr = fontes.hora_cheia(rt[0] - dt.timedelta(minutes=30))
                if k_tr in tempos:
                    j_tr = tempos.index(k_tr)
                    if chuva_obs[j_tr] is None:  # se o total horário oficial já saiu, ele prevalece
                        chuva_obs[j_tr] = rt[1]
                    chuva_tr["hora"] = _iso(k_tr)
                if chuva_obs[i_agora] is None:
                    chuva_obs[i_agora] = rt[1]  # hora atual ainda sem medição: a última hora serve de estimativa
        except Exception as ex:  # noqa: BLE001
            erros.append(f"CEMADEN tempo real: {ex}")

    # ---------------------------------------------------------------- previsão
    fp = cfg["chuva"].get("fator_previsao", 1.0)
    cache_prev = os.path.join(AQUI, "cache_previsao.json")
    prev, erro_prev = {}, None
    for tentativa in range(2):
        try:
            prev = fontes.openmeteo_previsao(cfg["chuva"]["pontos_previsao"], cfg["chuva"]["modelos_previsao"])
            break
        except Exception as ex:  # noqa: BLE001
            erro_prev = ex
    if prev:
        fontes_ok["Open-Meteo"] = {"ok": True, "modelos": list(prev)}
        with open(cache_prev, "w", encoding="utf-8") as f:
            json.dump({"t": _iso(agora), "prev": {m: {_iso(t): v for t, v in s_.items()} for m, s_ in prev.items()}}, f)
    else:
        erros.append(f"Open-Meteo: {erro_prev}")
        fontes_ok["Open-Meteo"] = {"ok": False, "erro": str(erro_prev)[:200]}
        if os.path.exists(cache_prev):  # usa a última previsão recebida, se tiver menos de 6 h
            with open(cache_prev, encoding="utf-8") as f:
                c_ = json.load(f)
            if agora - dt.datetime.fromisoformat(c_["t"]) <= dt.timedelta(hours=6):
                prev = {m: {dt.datetime.fromisoformat(t): v for t, v in s_.items()} for m, s_ in c_["prev"].items()}
                fontes_ok["Open-Meteo"]["erro"] = f"sem resposta; usando a previsão recebida às {c_['t'][11:16]}"
    modelos = list(prev)
    vies_prev = {}
    if cfg["chuva"].get("corrigir_vies_previsao", True):
        # compara o que cada modelo "previu" nas últimas horas com a média dos pluviômetros
        nh = cfg["chuva"].get("horas_vies_previsao", 12)
        tau_v = cfg["chuva"].get("tau_vies_previsao_h", 18)
        janela_v = [t for t in tempos[max(0, i_agora - nh):i_agora]]
        obs_v = sum(chuva_obs[tempos.index(t)] or 0 for t in janela_v)
        for m in modelos:
            mod_v = sum(prev[m].get(t, 0) for t in janela_v)
            if obs_v >= 5 and mod_v > 0.5:
                fv = min(max(obs_v / mod_v, 0.5), 3.0)
            else:
                fv = 1.0
            vies_prev[m] = round(fv, 2)
            for t in list(prev[m]):
                if t >= h_atual:
                    k = (t - h_atual) / H
                    prev[m][t] *= 1 + (fv - 1) * math.exp(-k / tau_v)
    prev_media = {}
    for t in tempos:
        vals = [prev[m][t] for m in modelos if t in prev[m]]
        if vals:
            prev_media[t] = sum(vals) / len(vals)

    def serie_chuva(fonte_prev):
        out = []
        for i, t in enumerate(tempos):
            if i < i_agora and chuva_obs[i] is not None:
                out.append(chuva_obs[i])
            elif i == i_agora and chuva_obs[i] is not None:
                # hora corrente incompleta: usa o maior entre o observado até agora e a previsão
                out.append(max(chuva_obs[i], fonte_prev.get(t, 0) * fp))
            else:
                out.append(fonte_prev.get(t, 0.0) * fp)
        return out

    # cenário pessimista: em cada hora, o maior valor entre os modelos, +25%
    fpess = cfg["chuva"].get("fator_pessimista", 1.25)
    prev_pess = {}
    for t in tempos:
        vals = [prev[m][t] for m in modelos if t in prev[m]]
        if vals:
            prev_pess[t] = max(vals) * fpess

    # radar: chuva observada nos últimos quadros e previsão imediata (nowcasting) para 0-3 h
    radar_info = None
    try:
        import radar
        pluv = {t: v for t, v in zip(tempos, chuva_obs) if v is not None}
        radar_info = radar.processar(horas=6, horas_prev=3, chuva_pluvio=pluv)
        fontes_ok["Radar Defesa Civil SC (mosaico C-MAX)"] = {"ok": True, "ultima": radar_info["ultimo_quadro"]}
        peso_radar = cfg["chuva"].get("peso_radar_horas", [1.0, 0.7, 0.4])
        for k, w in enumerate(peso_radar):
            t = h_atual + k * H
            rv = radar_info["previsao_horaria"].get(t)
            if k == 0:  # hora corrente: o que o radar já viu + o que vem pelo deslocamento
                vistos = [v for tt, v in radar_info["serie10"] if fontes.hora_cheia(dt.datetime.fromisoformat(tt)) == t]
                rv = (sum(vistos) / 6 if vistos else 0) + (rv or 0)
            if rv is None:
                continue
            for fonte_prev in [prev_media, prev_pess] + [prev[m] for m in modelos]:
                fonte_prev[t] = w * rv + (1 - w) * fonte_prev.get(t, rv)
    except Exception as ex:  # noqa: BLE001
        erros.append(f"Radar: {ex}")
        fontes_ok["Radar Defesa Civil SC (mosaico C-MAX)"] = {"ok": False, "erro": str(ex)[:200]}

    # Persistência: média da chuva medida nas últimas 3 horas (e radar agora), decaindo com o tempo.
    # O cenário pessimista nunca assume menos chuva que isso; sem nenhuma previsão, a média também usa.
    recentes = [chuva_obs[k] for k in range(max(0, i_agora - 3), i_agora) if chuva_obs[k] is not None]
    taxa_rec = sum(recentes) / len(recentes) if recentes else 0.0
    if radar_info:
        taxa_rec = max(taxa_rec, radar_info.get("taxa_atual_mm_h") or 0.0)
    if chuva_tr:
        taxa_rec = max(taxa_rec, chuva_tr["mm_ultima_hora"])
    persistencia = {}
    for k in range(0, 12):
        t = h_atual + k * H
        persistencia[t] = taxa_rec * math.exp(-k / 4)
        prev_pess[t] = max(prev_pess.get(t, 0.0), persistencia[t])
        if not modelos:
            prev_media[t] = max(prev_media.get(t, 0.0), taxa_rec * math.exp(-k / 2.5))

    cenarios = {"media": serie_chuva(prev_media)}
    for m in modelos:
        cenarios[m] = serie_chuva(prev[m])
    cenarios["pessimista"] = serie_chuva(prev_pess)

    # ---------------------------------------------------------------- vazões observadas (ANA)
    q_ref_h = _horario_medio(obs_ana.get(cal["estacao_ana"], []), "vazao")
    area, area_ref = cfg["ponto"]["area_km2"], cal["area_ana_km2"]

    # ---------------------------------------------------------------- modelo
    p = cfg["hbv"]
    q_ref_mod = modelo.vazao(cenarios["media"], p, area_ref)
    obs_alinhado = [q_ref_h.get(t) for t in tempos]
    ini_cal = max(0, i_agora - cal["horas_calibracao"])
    escala = modelo.calibrar_escala(q_ref_mod[ini_cal:i_agora + 1], obs_alinhado[ini_cal:i_agora + 1], (0.5, 2.0))

    idx_obs = max((i for i, t in enumerate(tempos) if t in q_ref_h), default=None)
    proxy = [q_ref_h[t] * area / area_ref if t in q_ref_h else None for t in tempos]

    q_cen, q_bruto = {}, {}
    for nome, ch in cenarios.items():
        q = [v * escala for v in modelo.vazao(ch, p, area)]
        q_bruto[nome] = q
        if idx_obs is not None:
            q = modelo.assimilar(q, idx_obs, proxy[idx_obs], cal["tau_assimilacao_h"])
        q_cen[nome] = q
    q_modelo_puro = [v * escala for v in modelo.vazao(cenarios["media"], p, area)]

    # melhor estimativa: observado escalado no passado, modelo assimilado no futuro
    q_melhor = [proxy[i] if (proxy[i] is not None and i <= (idx_obs or -1)) else q_cen["media"][i]
                for i in range(len(tempos))]

    # ---------------------------------------------------------------- régua real (SAMAE) e cota
    al = cfg["alertas"]
    reg0 = al["regua_normal_m"]
    cc = cfg.get("curva_chave")
    g = dict(cfg["canal"])
    q_normal = g["vazao_normal_m3s"]

    def q_de_regua(hr):
        return cc["K"] * max(hr - cc["H0"], 0.0) ** cc["n"]

    def regua_de_q(q):
        return cc["H0"] + (max(q, 0.0) / cc["K"]) ** (1 / cc["n"])

    chave_r = cfg.get("regua", {}).get("chave", "corupa")
    reguas, err_r = {}, []
    try:
        reguas, err_r = fontes.reguas_com_historico()
        ult_r = reguas.get(chave_r, [])[-1] if reguas.get(chave_r) else None
        fontes_ok["Régua SAMAE Ponte Corupá"] = {"ok": bool(ult_r), "ultima": _iso(ult_r[0]) if ult_r else None}
    except Exception as ex:  # noqa: BLE001
        err_r = [str(ex)]
        fontes_ok["Régua SAMAE Ponte Corupá"] = {"ok": False, "erro": str(ex)[:200]}
    erros += [f"Réguas SAMAE: {e}" for e in err_r]
    reg_hora = {}
    for t, n in reguas.get(chave_r, []):
        reg_hora.setdefault(fontes.hora_cheia(t), []).append(n)
    reg_hora = {k: sum(v) / len(v) for k, v in reg_hora.items()}
    serie_regua = [reg_hora.get(t) for t in tempos]

    ancora = None
    leituras = carregar_leituras()
    if cc:
        # passado: vazão tirada da régua real; a previsão parte da última hora com régua
        idx_reg = max((i for i, t in enumerate(tempos) if t in reg_hora and i <= i_agora), default=None)
        fonte_anc, j_anc = "régua SAMAE", idx_reg
        h_anc = reg_hora[tempos[idx_reg]] if idx_reg is not None else None
        # usa a leitura mais recente (minuto) como valor da hora corrente, se tiver menos de 20 min
        serie_min = reguas.get(chave_r, [])
        if serie_min and (agora - serie_min[-1][0]) <= dt.timedelta(minutes=20):
            fonte_anc, j_anc, h_anc = "régua SAMAE", i_agora, serie_min[-1][1]
        for l in leituras:  # leitura manual mais nova que a régua prevalece
            t = fontes.hora_cheia(dt.datetime.fromisoformat(l["t"]))
            if t in tempos and tempos.index(t) <= i_agora and (j_anc is None or tempos.index(t) > j_anc):
                j_anc, h_anc, fonte_anc = tempos.index(t), l["elevacao_m"], "leitura manual"
        for i, t in enumerate(tempos):
            if t in reg_hora and i <= i_agora:
                q_melhor[i] = q_de_regua(reg_hora[t])
        if j_anc is not None:
            q_anc = q_de_regua(h_anc)
            for nome in q_cen:
                q_cen[nome] = modelo.assimilar(q_bruto[nome], j_anc, q_anc, cal["tau_assimilacao_h"])
                q_cen[nome][j_anc] = q_anc
            for i in range(j_anc, len(tempos)):
                q_melhor[i] = q_cen["media"][i]
            ancora = {"t": _iso(tempos[j_anc]), "regua_m": round(h_anc, 2), "q_m3s": round(q_anc, 1), "fonte": fonte_anc}
        for l in leituras:
            l["q_est"] = q_de_regua(l["elevacao_m"])

        def elev(serie):  # metros acima do normal
            return [round(regua_de_q(q) - reg0, 3) for q in serie]
    else:
        for l in leituras:
            t = fontes.hora_cheia(dt.datetime.fromisoformat(l["t"]))
            l["q_est"] = q_melhor[tempos.index(t)] if t in tempos else None
        if [l for l in leituras if l.get("q_est")]:
            g["fator_conducao"] = modelo.calibrar_conducao([l for l in leituras if l.get("q_est")], g, q_normal)

        def elev(serie):
            return [round(modelo.elevacao(q, g, q_normal), 3) for q in serie]

    elev_melhor = elev(q_melhor)
    elev_cen = {k: elev(v) for k, v in q_cen.items()}

    # Correção de tendência: se nas últimas 2 h a régua subiu (ou desceu) mais que o modelo,
    # projeta essa diferença de inclinação nas próximas horas, perdendo força (pico do efeito em ~2 h).
    tendencia = None
    if cc and ancora and ancora["fonte"] == "régua SAMAE":
        serie_min = reguas.get(chave_r, [])
        t_fim = serie_min[-1][0]
        antes = [n for t, n in serie_min if abs((t - (t_fim - 2 * H)).total_seconds()) <= 300]
        if antes:
            j = tempos.index(fontes.hora_cheia(t_fim))
            sobe_obs = (serie_min[-1][1] - sum(antes) / len(antes)) / 2  # m/h
            sobe_mod = (regua_de_q(q_bruto["media"][j]) - regua_de_q(q_bruto["media"][max(j - 2, 0)])) / 2
            e = max(min(sobe_obs - sobe_mod, 0.5), -0.5)
            corr = [0.0] * len(tempos)
            for i in range(j + 1, len(tempos)):
                k = i - j
                corr[i] = e * k * math.exp(-k / 2.5)
            for nome in elev_cen:
                elev_cen[nome] = [round(v + c, 3) for v, c in zip(elev_cen[nome], corr)]
                q_cen[nome] = [q_de_regua(v + reg0) if c else q for q, v, c in zip(q_cen[nome], elev_cen[nome], corr)]
            for i in range(j + 1, len(tempos)):
                elev_melhor[i] = elev_cen["media"][i]
                q_melhor[i] = q_cen["media"][i]
            tendencia = {"regua_sobe_m_h": round(sobe_obs, 3), "modelo_sobe_m_h": round(sobe_mod, 3),
                         "correcao_m_h": round(e, 3), "efeito_max_m": round(max(corr, key=abs), 2)}

    # ---------------------------------------------------------------- tempo de resposta da bacia
    # pulso de 10 mm na hora atual e na próxima: a diferença na vazão mostra quando essa chuva chega ao ponto
    resposta = {}
    base_q = modelo.vazao(cenarios["media"], p, area)
    for rot, k0 in (("agora", 0), ("em_1h", 1)):
        ch = list(cenarios["media"])
        if i_agora + k0 >= len(ch):
            continue
        ch[i_agora + k0] += 10.0
        dq = [max(a_ - b_, 0.0) * escala for a_, b_ in zip(modelo.vazao(ch, p, area), base_q)][i_agora + k0:]
        tot = sum(dq) or 1.0
        acum, t10, t50, t90 = 0.0, None, None, None
        for h_, v in enumerate(dq):
            acum += v
            if t10 is None and acum >= 0.1 * tot:
                t10 = h_
            if t50 is None and acum >= 0.5 * tot:
                t50 = h_
            if t90 is None and acum >= 0.9 * tot:
                t90 = h_
        pk_h = max(range(len(dq)), key=lambda h_: dq[h_])
        resposta[rot] = {"chuva_em": _iso(tempos[i_agora + k0]), "inicio_h": t10, "pico_h": pk_h, "metade_h": t50,
                         "noventa_h": t90, "pico_em": _iso(tempos[min(i_agora + k0 + pk_h, len(tempos) - 1)]),
                         "curva": [round(v / 10.0, 2) for v in dq[:24]]}  # m³/s no ponto por mm de chuva
    # defasagem observada hoje: pico da chuva x pico da régua
    defasagem_obs = None
    hoje = [t for t in tempos[:i_agora + 1] if t in reg_hora and t >= h_atual - dt.timedelta(hours=20)]
    if hoje:
        t_pico_r = max(hoje, key=lambda t: reg_hora[t])
        cand = [t for t in tempos[:i_agora + 1] if t_pico_r - dt.timedelta(hours=8) <= t <= t_pico_r]
        if cand:
            t_pico_c = max(cand, key=lambda t: chuva_obs[tempos.index(t)] or 0)
            defasagem_obs = {"pico_chuva": _iso(t_pico_c), "pico_regua": _iso(t_pico_r),
                             "horas": round((t_pico_r - t_pico_c) / H, 1), "regua_pico_m": round(reg_hora[t_pico_r], 2)}

    # ---------------------------------------------------------------- limiar de chuva que faz o rio subir
    # Ajuste estatístico com o histórico: variação da régua nas 2 h seguintes (m/h)
    #   = a·(chuva média das últimas 2 h) + b·(nível da régua) + c
    # Equilíbrio (rio estável) quando a variação = 0  ->  chuva = -(b·nível + c)/a
    # Subida forte (> 30 cm/h)                        ->  chuva = (0,30 - b·nível - c)/a
    limiar = None
    pts = []
    for h in range(1, i_agora - 2):
        if None in (chuva_obs[h], chuva_obs[h - 1], serie_regua[h], serie_regua[h + 1], serie_regua[h + 2]):
            continue
        pts.append(((chuva_obs[h] + chuva_obs[h - 1]) / 2, serie_regua[h], (serie_regua[h + 2] - serie_regua[h]) / 2))
    reg_agora_l = None
    if serie_regua[i_agora] is not None:
        reg_agora_l = serie_regua[i_agora]
    elif ancora:
        reg_agora_l = ancora["regua_m"]
    if len(pts) >= 12:
        # mínimos quadrados (3 parâmetros)
        X = [(r, hh, 1.0) for r, hh, _ in pts]
        Y = [d for _, _, d in pts]
        M = [[sum(x[p_] * x[q_] for x in X) for q_ in range(3)] + [sum(x[p_] * y for x, y in zip(X, Y))] for p_ in range(3)]
        for col in range(3):
            piv = max(range(col, 3), key=lambda r_: abs(M[r_][col]))
            M[col], M[piv] = M[piv], M[col]
            for r_ in range(3):
                if r_ != col and M[col][col]:
                    f_ = M[r_][col] / M[col][col]
                    M[r_] = [u - f_ * v for u, v in zip(M[r_], M[col])]
        if all(M[k][k] for k in range(3)):
            ca, cb, cc_ = (M[k][3] / M[k][k] for k in range(3))
            my = sum(Y) / len(Y)
            ss = sum((y - my) ** 2 for y in Y) or 1e-9
            r2 = 1 - sum((y - (ca * x[0] + cb * x[1] + cc_)) ** 2 for x, y in zip(X, Y)) / ss
            if ca > 0.005:
                def eq(nv):
                    return max(0.0, -(cb * nv + cc_) / ca)

                def forte(nv):
                    return max(0.0, (0.30 - cb * nv - cc_) / ca)
                nv = reg_agora_l if reg_agora_l is not None else 1.0
                limiar = {"fonte": "ajuste", "n": len(pts), "r2": round(r2, 2),
                          "coef": [round(ca, 4), round(cb, 4), round(cc_, 4)],
                          "nivel": round(nv, 2), "equilibrio_mm_h": round(eq(nv), 1), "forte_mm_h": round(forte(nv), 1),
                          "tabela": [{"nivel": x / 2, "eq": round(eq(x / 2), 1), "forte": round(forte(x / 2), 1)} for x in range(1, 11)]}
    if limiar is None:  # pouco histórico: usa a estimativa inicial da config
        li = cfg.get("limiar_chuva", {"equilibrio_mm_h": 6.0, "forte_mm_h": 10.0})
        limiar = {"fonte": "estimativa inicial", "n": len(pts), "r2": None, "nivel": reg_agora_l,
                  "equilibrio_mm_h": li["equilibrio_mm_h"], "forte_mm_h": li["forte_mm_h"], "tabela": []}
    limiar["pontos"] = [[round(r, 2), round(hh, 2), round(100 * d, 1)] for r, hh, d in pts]
    limiar["chuva_agora_mm_h"] = round(chuva_obs[i_agora], 1) if chuva_obs[i_agora] is not None else None

    # ---------------------------------------------------------------- recorte para o painel
    ini = max(0, i_agora - 7 * 24)
    sl = slice(ini, len(tempos))
    nomes_mod = [m for m in modelos] + ["pessimista"]
    fut = range(i_agora, len(tempos))
    faixa_min = [min(elev_cen[m][i] for m in nomes_mod) if nomes_mod else elev_cen["media"][i] for i in range(len(tempos))]
    faixa_max = [max(elev_cen[m][i] for m in nomes_mod) if nomes_mod else elev_cen["media"][i] for i in range(len(tempos))]
    qmin = [min(q_cen[m][i] for m in nomes_mod) if nomes_mod else q_cen["media"][i] for i in range(len(tempos))]
    qmax = [max(q_cen[m][i] for m in nomes_mod) if nomes_mod else q_cen["media"][i] for i in range(len(tempos))]

    def r(lst, nd=2):
        return [None if v is None else round(v, nd) for v in lst[sl]]

    # alerta (pior caso entre melhor estimativa atual e previsão média no horizonte)
    hz = al["horizonte_alerta_h"]
    def nivel_alerta(reg):
        if reg >= al["transbordamento"]:
            return "transbordamento"
        if reg >= al["alerta"]:
            return "alerta"
        if reg >= al["atencao"]:
            return "atencao"
        return "normal"
    fr = agora.minute / 60
    reg_atual = elev_melhor[i_agora] * (1 - fr) + elev_cen["media"][min(i_agora + 1, len(tempos) - 1)] * fr + reg0
    ult_leit = (reguas.get(chave_r) or [None])[-1] if cc else None
    if ult_leit and agora - ult_leit[0] <= dt.timedelta(minutes=20):
        reg_atual = ult_leit[1]
    janela = range(i_agora, min(i_agora + hz + 1, len(tempos)))
    pico_i = max(janela, key=lambda i: elev_cen["media"][i])
    reg_pico = elev_cen["media"][pico_i] + reg0
    pior_i = max(janela, key=lambda i: faixa_max[i])
    reg_pior = faixa_max[pior_i] + reg0
    # pior caso exatamente daqui 1 h (interpolado entre as horas cheias): é o que define o status e os alertas
    def interp(serie, pos):
        k = int(pos)
        if k + 1 >= len(serie):
            return serie[-1]
        return serie[k] * (1 - (pos - k)) + serie[k + 1] * (pos - k)
    reg_pior_1h = interp(faixa_max, i_agora + fr + 1) + reg0
    reg_media_1h = interp(elev_cen["media"], i_agora + fr + 1) + reg0
    # ----- verificação: o que foi previsto para 1 h x o que a régua mostrou
    vcfg = cfg.get("verificacao", {})
    serie_r = reguas.get(chave_r, []) if cc else []
    reg_min = {t.strftime("%Y-%m-%dT%H:%M"): n for t, n in serie_r}

    def regua_em(t):
        for d in range(0, 6):
            for sinal in (1, -1):
                k = (t + sinal * dt.timedelta(minutes=d)).strftime("%Y-%m-%dT%H:%M")
                if k in reg_min:
                    return reg_min[k]
        return None

    verif = []
    if os.path.exists(PREV_1H):
        with open(PREV_1H, encoding="utf-8") as f:
            for linha in f.read().splitlines()[1:]:
                try:
                    emit, alvo, base_r, med, pio = linha.split(",")
                except ValueError:
                    continue
                ta = dt.datetime.fromisoformat(alvo)
                if ta > agora or ta < agora - dt.timedelta(hours=vcfg.get("janela_h", 72)):
                    continue
                obs = regua_em(ta)
                if obs is not None:
                    verif.append({"alvo": alvo, "obs": obs, "media": float(med), "pior": float(pio), "erro": obs - float(med)})
    pior_modelo_1h = reg_pior_1h
    fonte_pior = "pior cenário dos modelos de chuva"
    if len(verif) >= vcfg.get("min_verificacoes", 12):
        erros_v = sorted(v["erro"] for v in verif)
        q = erros_v[min(len(erros_v) - 1, int(vcfg.get("quantil_pior_caso", 0.9) * len(erros_v)))]
        reg_pior_1h = reg_media_1h + max(q, vcfg.get("folga_min_m", 0.05))
        fonte_pior = f"previsão + erro observado em {len(verif)} previsões anteriores (90% dos casos)"
    acerto = None
    if verif:
        acerto = {"n": len(verif), "erro_medio_cm": round(100 * sum(abs(v["erro"]) for v in verif) / len(verif), 1),
                  "vies_cm": round(100 * sum(v["erro"] for v in verif) / len(verif), 1),
                  "pior_cobriu_pct": round(100 * sum(v["obs"] <= v["pior"] for v in verif) / len(verif)),
                  "ultimas": verif[-24:]}
    # registra a previsão desta rodada para conferir daqui 1 h
    if cc and ancora and registrar_previsao:
        novo = not os.path.exists(PREV_1H)
        with open(PREV_1H, "a", encoding="utf-8") as f:
            if novo:
                f.write("emitida,alvo,regua_base,media_1h,pior_1h\n")
            f.write(f"{_iso(agora)},{_iso(agora + H)},{reg_atual:.3f},{reg_media_1h:.3f},{reg_pior_1h:.3f}\n")
    nivel_status = nivel_alerta(max(reg_atual, reg_pior_1h))
    # primeira hora prevista (pior cenário) em que cada cota é atingida
    cruzamentos = {}
    for nome in ("atencao", "alerta", "transbordamento"):
        for i in janela:
            if faixa_max[i] + reg0 >= al[nome]:
                cruzamentos[nome] = _iso(tempos[i])
                break

    pacote = {
        "gerado_em": _iso(agora),
        "agora": _iso(h_atual),
        "i_agora": i_agora - ini,
        "tempos": [_iso(t) for t in tempos[sl]],
        "chuva_estacoes": {n: [s.get(t) for t in tempos[sl]] for n, s in chuva_est.items()},
        "chuva_obs": r(chuva_obs + [None] * (len(tempos) - len(chuva_obs))),
        "chuva_prev": {m: r([prev[m].get(t) for t in tempos]) for m in modelos},
        "chuva_prev_media": r([prev_media.get(t) for t in tempos]),
        "chuva_prev_pessimista": r([prev_pess.get(t) for t in tempos]),
        "chuva_persistencia_mm_h": round(taxa_rec, 2),
        "chuva_tempo_real": chuva_tr,
        "limiar_chuva": limiar,
        "chuva_radar": r([radar_info["horario"].get(t) if radar_info else None for t in tempos]),
        "chuva_radar_prev": r([radar_info["previsao_horaria"].get(t) if radar_info else None for t in tempos]),
        "radar": None if not radar_info else {
            "ultimo_quadro": radar_info["ultimo_quadro"], "fator_vies": radar_info["fator_vies"],
            "atraso_min": int((agora - dt.datetime.fromisoformat(radar_info["ultimo_quadro"])).total_seconds() // 60),
            "movimento": radar_info["movimento"], "taxa_atual_mm_h": radar_info["taxa_atual_mm_h"],
            "serie10": radar_info["serie10"], "previsao10": radar_info["previsao10"],
            "quadros": radar_info.get("quadros"), "legenda": radar_info.get("legenda")},
        "chuva_modelo": r(cenarios["media"]),
        "q_melhor": r(q_melhor, 1),
        "q_prev": r(q_cen["media"], 1),
        "q_prev_min": r(qmin, 1),
        "q_prev_max": r(qmax, 1),
        "q_modelo_sem_assimilacao": r(q_modelo_puro, 1),
        "q_proxy_ana": r(proxy, 1),
        "elev_melhor": r(elev_melhor),
        "regua_obs": r(serie_regua),
        "reguas": {k: [(_iso(t), n) for t, n in v if t >= agora - dt.timedelta(hours=48)][::10] for k, v in reguas.items()},
        "reguas_nomes": {k: v["nome"] for k, v in fontes.REGUAS.items()},
        "tempo_resposta": {"pulsos": resposta, "observado_hoje": defasagem_obs},
        "elev_prev": r(elev_cen["media"]),
        "elev_prev_min": r(faixa_min),
        "elev_prev_max": r(faixa_max),
        "ana": {cod: [{"t": _iso(x["t"]), "nivel_m": x["nivel_m"], "vazao": x["vazao"]} for x in lst]
                for cod, lst in obs_ana.items()},
        "leituras": [{"t": l["t"], "elevacao_m": l["elevacao_m"], "nota": l.get("nota", ""),
                      "q_est": round(l["q_est"], 1) if l.get("q_est") else None} for l in leituras],
        "calibracao": {"escala_vazao": round(escala, 3), "fator_conducao": round(g.get("fator_conducao", 1), 3),
                       "curva_chave": cc,
                       "ultima_obs_ana": _iso(tempos[idx_obs]) if idx_obs is not None else None,
                       "ancora_leitura": ancora,
                       "tendencia": tendencia,
                       "auto": (json.load(open(CALIB_AUTO, encoding="utf-8")).get("resumo") if os.path.exists(CALIB_AUTO) else None),
                       "vies_previsao_chuva": vies_prev,
                       "ultima_obs_ana_min": (obs_ana.get(cal["estacao_ana"]) or [{}])[-1].get("t").strftime("%H:%M") if obs_ana.get(cal["estacao_ana"]) else None},
        "verificacao_1h": acerto,
        "alerta": {"status": nivel_status, "regua_pior_1h": round(reg_pior_1h, 2), "regua_media_1h": round(reg_media_1h, 2),
                   "pior_1h_fonte": fonte_pior, "pior_1h_modelos": round(pior_modelo_1h, 2),
                   "hora_1h": _iso(agora + H),
                   "nivel_atual": nivel_alerta(reg_atual), "regua_atual": round(reg_atual, 2),
                   "nivel_previsto": nivel_alerta(reg_pico), "regua_pico": round(reg_pico, 2),
                   "hora_pico": _iso(tempos[pico_i]),
                   "nivel_pior": nivel_alerta(reg_pior), "regua_pior": round(reg_pior, 2),
                   "hora_pior": _iso(tempos[pior_i]), "cruzamentos": cruzamentos},
        "config": cfg,
        "fontes": fontes_ok,
        "erros": erros,
    }
    return pacote


if __name__ == "__main__":
    try:
        pk = rodar()
        print(json.dumps({k: pk[k] for k in ("gerado_em", "calibracao", "alerta", "fontes", "erros")},
                         ensure_ascii=False, indent=1))
        i = pk["i_agora"]
        for j in range(i - 12, i + 25, 2):
            print(pk["tempos"][j], "chuva", pk["chuva_modelo"][j], "Q", pk["q_melhor"][j], "elev", pk["elev_melhor"][j],
                  "Qprev", pk["q_prev"][j], f"[{pk['q_prev_min'][j]}-{pk['q_prev_max'][j]}]", "proxy", pk["q_proxy_ana"][j])
    except Exception:
        traceback.print_exc()
