# Monitor do Rio Itapocu em Corupá/SC

Painel público: **https://escolaaplicar.com.br/nivel-rio-corupa/**

Nível do Rio Itapocu na régua da Ponte Corupá (Rua Roberto Seidel), chuva na bacia (Rio Novo + Rio Humboldt), radar e previsão experimental para as próximas horas.

> **Previsão experimental, não oficial.** Em emergência, siga a Defesa Civil (199).

## Como funciona
- `.github/workflows/atualizar.yml`: a cada 10 min, coleta os dados, roda o modelo (`rio_itapocu/motor.py`) e publica o site estático no GitHub Pages. O histórico da régua fica no ramo `estado`.
- `worker/`: Worker da Cloudflare que entrega o site em `escolaaplicar.com.br/nivel-rio-corupa/`, somente leitura e com cabeçalhos de segurança.
- Fontes e referências: [FONTES.md](FONTES.md).

## Como alterar (somente por commit)
- **Cotas de alerta:** `rio_itapocu/config.json`, bloco `alertas` (`atencao`, `alerta`, `transbordamento` = alerta máximo, `regua_normal_m`).
- **Leituras manuais da régua:** `rio_itapocu/leituras.json`, por exemplo `{"t": "2026-10-06T20:30", "elevacao_m": 3.25, "nota": "conferido na ponte"}`. Usar o valor da régua, não "acima do normal".

Cada commit no `main` atualiza o site.
