"""Modelo chuva-vazão-cota para o Itapocu 250 m a jusante da confluência Rio Novo + Humboldt.

1. Chuva média na bacia (mm/h)                -> HBV simplificado, passo horário
2. Escoamento (mm/h)                          -> cascata de Nash (n reservatórios, k horas)
3. Vazão (m³/s) = mm/h * área / 3,6           -> fator de escala calibrado contra a ANA 82336000 (Humboldt)
4. Correção pelo último valor observado (assimilação com decaimento exponencial)
5. Cota: Manning em seção trapezoidal, com fator de condutância calibrado pelas leituras do usuário
"""
import math


# --------------------------------------------------------------------------- HBV
def hbv(chuva, p, estado=None):
    """chuva: lista mm/h. Devolve (escoamento mm/h, estado final)."""
    fc, beta, lp = p["fc"], p["beta"], p["lp"]
    pet = p["pet_mm_dia"] / 24.0
    k0, k1, k2, uzl, perc = p["k0"], p["k1"], p["k2"], p["uzl"], p["perc"]
    sm, uz, lz = estado or (p["sm_ini"] * fc, 0.0, p["lz_ini"])
    out = []
    for P in chuva:
        P = max(P or 0.0, 0.0)
        rec = P * (sm / fc) ** beta if sm > 0 else 0.0
        sm += P - rec
        et = pet * min(1.0, sm / (lp * fc))
        sm = max(sm - et, 0.0)
        if sm > fc:
            rec += sm - fc
            sm = fc
        uz += rec
        q0 = k0 * max(uz - uzl, 0.0)
        q1 = k1 * uz
        pc = min(perc, uz - q0 - q1) if uz - q0 - q1 > 0 else 0.0
        uz = max(uz - q0 - q1 - pc, 0.0)
        lz += pc
        q2 = k2 * lz
        lz -= q2
        out.append(q0 + q1 + q2)
    return out, (sm, uz, lz)


def nash(serie, n, k):
    """Propaga a série por n reservatórios lineares de constante k (h), passo 1 h."""
    if n <= 0 or k <= 0:
        return list(serie)
    a = math.exp(-1.0 / k)
    estados = [serie[0]] * int(round(n))
    out = []
    for x in serie:
        entrada = x
        for i in range(len(estados)):
            estados[i] = a * estados[i] + (1 - a) * entrada
            entrada = estados[i]
        out.append(entrada)
    return out


def vazao(chuva, p, area_km2):
    esc, _ = hbv(chuva, p)
    rot = nash(esc, p["nash_n"], p["nash_k"])
    return [r * area_km2 / 3.6 for r in rot]


# --------------------------------------------------------------------------- Manning
def manning_q(h, g):
    """Vazão (m³/s) para profundidade h (m) em seção trapezoidal."""
    if h <= 0:
        return 0.0
    b, z, n, s = g["largura_fundo_m"], g["talude_z"], g["manning_n"], g["declividade"]
    area = (b + z * h) * h
    per = b + 2 * h * math.sqrt(1 + z * z)
    return g.get("fator_conducao", 1.0) * area * (area / per) ** (2 / 3) * math.sqrt(s) / n


def manning_h(q, g):
    if q <= 0:
        return 0.0
    lo, hi = 0.0, 1.0
    while manning_q(hi, g) < q and hi < 60:
        hi *= 2
    for _ in range(60):
        mid = (lo + hi) / 2
        if manning_q(mid, g) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def elevacao(q, g, q_normal):
    """Metros acima do nível normal (nível na vazão de referência)."""
    return manning_h(q, g) - manning_h(q_normal, g)


def calibrar_conducao(leituras, g, q_normal):
    """Ajusta o fator de condutância para reproduzir as leituras do usuário (elevação x vazão estimada)."""
    pares = [(l["q_est"], l["elevacao_m"]) for l in leituras if l.get("q_est") and l.get("elevacao_m") is not None]
    if not pares:
        return g.get("fator_conducao", 1.0)
    melhor, erro_min = 1.0, float("inf")
    f = 0.05
    while f <= 5.0:
        gg = dict(g, fator_conducao=f)
        erro = sum((elevacao(q, gg, q_normal) - e) ** 2 for q, e in pares)
        if erro < erro_min:
            melhor, erro_min = f, erro
        f *= 1.02
    return melhor


# --------------------------------------------------------------------------- calibração de escala
def calibrar_escala(q_mod, q_obs, limites=(0.2, 5.0)):
    """Fator multiplicativo que minimiza o erro em log entre modelo e observado (pares alinhados)."""
    pares = [(m, o) for m, o in zip(q_mod, q_obs) if m and o and m > 0.05 and o > 0.05]
    if len(pares) < 6:
        return 1.0
    media = sum(math.log(o / m) for m, o in pares) / len(pares)
    return min(max(math.exp(media), limites[0]), limites[1])


def assimilar(q_mod, idx_ultimo_obs, q_obs_ultimo, tau_h):
    """Corrige a série a partir do último ponto observado, com a correção decaindo com tempo tau."""
    if idx_ultimo_obs is None or q_obs_ultimo is None or q_mod[idx_ultimo_obs] <= 0:
        return list(q_mod)
    razao = q_obs_ultimo / q_mod[idx_ultimo_obs]
    razao = min(max(razao, 0.05), 20.0)
    out = list(q_mod)
    for i in range(len(q_mod)):
        if i <= idx_ultimo_obs:
            continue
        w = math.exp(-(i - idx_ultimo_obs) / tau_h)
        out[i] = q_mod[i] * (1 + (razao - 1) * w)
    return out
