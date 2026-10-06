"""Gera o site estático para publicação (pasta site/): index.html, dados.json e radar.gif.

Roda no GitHub Actions a cada 10 min. O site é somente leitura: não há formulário nem API de escrita.
"""
import html
import json
import os
import shutil
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(RAIZ, "rio_itapocu")
SITE = os.path.join(RAIZ, "site")
sys.path.insert(0, APP)

import motor  # noqa: E402


def escapar(obj):
    """Escapa todo texto vindo das fontes, para que nenhum conteúdo externo vire código na página."""
    if isinstance(obj, str):
        return html.escape(obj, quote=True)
    if isinstance(obj, list):
        return [escapar(x) for x in obj]
    if isinstance(obj, tuple):
        return [escapar(x) for x in obj]
    if isinstance(obj, dict):
        return {escapar(k) if isinstance(k, str) else k: escapar(v) for k, v in obj.items()}
    return obj


def pagina():
    p = open(os.path.join(APP, "painel.html"), encoding="utf-8").read()
    trocas = [
        ("fetch('/api/dados',{cache:'no-store'})", "fetch('dados.json?t='+Date.now(),{cache:'no-store'})"),
        ("'/radar.gif?t='", "'radar.gif?t='"),
        ('  <button class="sec" id="btnAtual">Atualizar agora</button>\n', ""),
        ("idade>P.config.atualizacao_min+5", "idade>Math.max(30,P.config.atualizacao_min*3)"),
        ("<title>Monitor Rio Itapocu</title>",
         "<title>Nível do Rio Itapocu em Corupá</title>\n"
         '<meta name="description" content="Nível do Rio Itapocu na Ponte Corupá, chuva e previsão experimental. '
         'Dados públicos de SAMAE/Defesa Civil, CEMADEN, ANA, radar SC e Open-Meteo.">'),
        ('<div class="sub" id="sub">carregando…</div>',
         '<div class="sub" id="sub">carregando…</div>\n'
         '<div class="aviso" style="margin-top:6px;font-size:13px"><b>Previsão experimental, não oficial.</b> '
         'Em emergência, siga a <b>Defesa Civil (199)</b>. Fontes no final da página.</div>'),
    ]
    for a, b in trocas:
        if a not in p:
            raise SystemExit(f"trecho não encontrado no painel: {a[:60]}")
        p = p.replace(a, b)
    i = p.index("$('btnAtual').onclick=")
    p = p[:i] + p[p.index("\n", i) + 1:]
    return p


def main():
    pk = motor.rodar()
    if os.path.exists(SITE):
        shutil.rmtree(SITE)
    os.makedirs(SITE)
    with open(os.path.join(SITE, "dados.json"), "w", encoding="utf-8") as f:
        json.dump({"pacote": escapar(pk), "erro": None, "rodando": False}, f, ensure_ascii=False, separators=(",", ":"))
    gif = os.path.join(APP, "radar_animacao.gif")
    if os.path.exists(gif):
        shutil.copy(gif, os.path.join(SITE, "radar.gif"))
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(pagina())
    open(os.path.join(SITE, ".nojekyll"), "w").close()
    a = pk["alerta"]
    print(f"site gerado {pk['gerado_em']} | status {a['status']} | régua {a['regua_atual']} m | erros: {pk['erros']}")


if __name__ == "__main__":
    main()
