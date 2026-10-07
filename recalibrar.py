"""Recalibração automática do modelo (roda a cada 6 h no GitHub Actions).

Ajusta o modelo HBV e a curva-chave Q = K·(H − H0)^n contra o histórico da régua da Ponte Corupá
(até 7 dias), usando a chuva média da bacia. Travas de segurança:
  - só aceita os parâmetros novos se o erro na régua cair pelo menos 10% em relação aos atuais;
  - exige um mínimo de horas de régua e alguma variação de nível (senão não há o que aprender);
  - parâmetros ficam dentro de faixas físicas plausíveis.
O resultado vai para rio_itapocu/calibracao_auto.json, que o motor lê por cima da config.
"""
import datetime as dt
import json
import math
import os
import random
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(RAIZ, "rio_itapocu")
sys.path.insert(0, APP)
import modelo  # noqa: E402
import motor  # noqa: E402

SAIDA = os.path.join(APP, "calibracao_auto.json")
FAIXAS_HBV = {"fc": (40, 400), "beta": (0.5, 5), "lp": (0.2, 1), "k0": (0.05, 0.9), "k1": (0.005, 0.2),
              "k2": (0.0005, 0.02), "uzl": (2, 40), "perc": (0.01, 1.0), "nash_k": (0.3, 4),
              "lz_ini": (5, 200), "sm_ini": (0.3, 1.0)}
FAIXAS_CURVA = {"K": (3, 200), "H0": (-0.6, 0.35), "n": (1.3, 2.8)}


def main(iteracoes=4000, intervalo_h=6):
    # roda no máximo a cada 6 h, não importa quem disparou a atualização
    if os.path.exists(SAIDA):
        try:
            ultima = json.load(open(SAIDA, encoding="utf-8")).get("resumo", {}).get("quando")
            agora_l = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=3)
            if ultima and agora_l - dt.datetime.fromisoformat(ultima) < dt.timedelta(hours=intervalo_h):
                print(f"última recalibração às {ultima}; próxima só depois de {intervalo_h} h")
                return
        except (ValueError, OSError):
            pass
    pk = motor.rodar(registrar_previsao=False)
    cfg = motor.carregar_config()
    i = pk["i_agora"]
    P = [x or 0 for x in pk["chuva_obs"][:i + 1]]
    H = pk["regua_obs"][:i + 1]
    Qp = pk["q_proxy_ana"][:i + 1]
    A = cfg["ponto"]["area_km2"]
    idx = [k for k, v in enumerate(H) if v is not None]
    if len(idx) < 18 or max(H[k] for k in idx) - min(H[k] for k in idx) < 0.3:
        print(f"poucos dados para recalibrar ({len(idx)} h de régua); mantém os parâmetros atuais")
        return

    def h_de_q(q, c):
        return c["H0"] + (max(q, 0) / c["K"]) ** (1 / c["n"])

    def score(par, c):
        q = modelo.vazao(P, par, A)
        rmse = math.sqrt(sum((h_de_q(q[k], c) - H[k]) ** 2 for k in idx) / len(idx))
        pr = [(q[k], Qp[k]) for k in range(max(0, len(P) - 72), len(P)) if Qp[k]]
        eq = sum(abs(math.log(max(a, .1) / b)) for a, b in pr) / len(pr) if pr else 0
        return rmse + 0.25 * eq, rmse

    atual_hbv = {k: v for k, v in cfg["hbv"].items() if not k.startswith("_")}
    atual_c = {k: cfg["curva_chave"][k] for k in ("K", "H0", "n")}
    s_atual = score(atual_hbv, atual_c)
    best, bc, bs = dict(atual_hbv), dict(atual_c), s_atual
    random.seed(int(dt.datetime.now().timestamp()) // 3600)
    for it in range(iteracoes):
        par, c = dict(best), dict(bc)
        amplo = it < iteracoes // 4
        for k, (a, b) in FAIXAS_HBV.items():
            if amplo and random.random() < 0.3:
                par[k] = random.uniform(a, b)
            elif not amplo and random.random() < 0.3:
                par[k] = min(b, max(a, par[k] * math.exp(random.gauss(0, .15))))
        for k, (a, b) in FAIXAS_CURVA.items():
            if amplo and random.random() < 0.4:
                c[k] = random.uniform(a, b)
            elif not amplo and random.random() < 0.4:
                c[k] = min(b, max(a, c[k] + random.gauss(0, .04 * (b - a))))
        s = score(par, c)
        if s[0] < bs[0]:
            best, bc, bs = par, c, s
    aceita = bs[1] <= 0.9 * s_atual[1]
    agora = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)
    resumo = {"quando": agora.strftime("%Y-%m-%dT%H:%M"), "horas_regua": len(idx),
              "erro_atual_cm": round(100 * s_atual[1], 1), "erro_novo_cm": round(100 * bs[1], 1), "aceita": aceita}
    if aceita:
        json.dump({"aceita": True, "hbv": {k: round(v, 5) if isinstance(v, float) else v for k, v in best.items()},
                   "curva": {k: round(v, 4) for k, v in bc.items()}, "resumo": resumo,
                   "descricao": f"Recalibrado automaticamente em {resumo['quando']} com {len(idx)} h de régua da Ponte Corupá; "
                                f"erro médio na régua {resumo['erro_novo_cm']} cm (antes {resumo['erro_atual_cm']} cm)."},
                  open(SAIDA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    elif os.path.exists(SAIDA):
        ca = json.load(open(SAIDA, encoding="utf-8"))
        ca["resumo"] = dict(resumo, mantida_desde=ca.get("resumo", {}).get("quando"))
        json.dump(ca, open(SAIDA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(resumo, ensure_ascii=False))


if __name__ == "__main__":
    main()
