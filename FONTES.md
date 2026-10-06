# Fontes, créditos e referências: Monitor do Rio Itapocu (Corupá/SC)

**Previsão experimental, não oficial.** Este painel junta dados públicos de vários órgãos e aplica um modelo próprio. Os dados pertencem às instituições citadas abaixo. Em emergência, siga sempre a **Defesa Civil (199)**.

## Dados observados em tempo real

| Fonte | O que fornece | Atualização | Uso no painel |
|---|---|---|---|
| **SAMAE de Jaraguá do Sul / Defesa Civil de Jaraguá do Sul**: réguas do Rio Itapocu na Ponte Corupá (Rua Roberto Seidel), Ponte Nereu Ramos, SAMAE ETA Central e Ponte Via Verde. [Painel público “Níveis dos Rios”](https://sistemas.samaejs.com.br:3000/grafana/public-dashboards/964bfc3d627a4d3db5f760c4654b7b57) · [Defesa Civil de Jaraguá do Sul](https://www.jaraguadosul.sc.gov.br/defesa-civil) | Nível da régua (m) e cotas oficiais: atenção 2,4 m, alerta 3,5 m, alerta máximo 5,0 m | 1 min (o painel público mostra ~21 h; o monitor grava o histórico) | Nível observado, cotas de alerta, ponto de partida da previsão, calibração |
| **CEMADEN**: pluviômetro 6925 “João Tozini” (Corupá, junto à confluência). [Mapa Interativo](https://mapainterativo.cemaden.gov.br/) | Chuva por hora | 10 min / horária | Chuva observada (peso 0,35) |
| **Epagri/CIRAM**: estação 2399 “Corupá - Guarajuva” (vale do Rio Novo). [AgroConnect](https://ciram.epagri.sc.gov.br/agroconnect/) | Chuva por hora | Horária | Chuva observada (peso 0,35) |
| **ANA, Hidrotelemetria**: 82336000 “PCH Rabo do Macaco Jusante” (Rio Humboldt); 82338000 “CGH Ano Bom Jusante” (Rio Ano Bom). [SNIRH Hidrotelemetria](https://www.snirh.gov.br/hidrotelemetria/) | Chuva, nível e vazão | ~30 min | Chuva do Humboldt (peso 0,30); vazão para calibrar e corrigir o modelo |
| **Radares meteorológicos de SC**, Defesa Civil de SC e Epagri/CIRAM: mosaico C-MAX (Chapecó, Lontras, Araranguá). [Radar CIRAM](https://ciram.epagri.sc.gov.br/index.php/radar/) · [Defesa Civil SC](https://www.defesacivil.sc.gov.br/) | Refletividade (dBZ) | 10 min | Chuva atual, animação, previsão imediata (0–3 h) |

## Previsão do tempo

- **Open-Meteo** ([open-meteo.com](https://open-meteo.com/), licença [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)), com os modelos **ECMWF IFS 0,25°** (Centro Europeu de Previsões Meteorológicas de Médio Prazo), **GFS** (NOAA/NCEP, EUA) e **ICON** (Serviço Meteorológico Alemão, DWD). Fornece a chuva prevista hora a hora em 4 pontos da bacia. Usada depois das 3 primeiras horas, corrigida pelo erro de cada modelo nas últimas 12 h, nos cenários otimista, médio e pessimista.
- **Epagri/CIRAM**: [previsão de chuva por modelo](https://ciram.epagri.sc.gov.br/index.php/previsao-modelo-chuva/) e [chuva acumulada prevista](https://ciram.epagri.sc.gov.br/index.php/previsao-modelo-chuva-acumulada/). Só consulta e comparação.

## Estudos e dados usados para definir parâmetros

- **ANA, HidroWeb / Inventário**: áreas de drenagem (82320000 Rio Novo em Corupá: 182 km²; 82336000 Rio Humboldt: 130 km²; 82350000 Itapocu em Jaraguá do Sul: 794 km²) e séries históricas de vazão (vazão média do Rio Novo em Corupá: 6,56 m³/s). [snirh.gov.br/hidroweb](https://www.snirh.gov.br/hidroweb/)
- **Negri et al.**, hidrograma unitário (Nash e geomorfológico) do Rio Itapocu em Jaraguá do Sul. XXIV Simpósio Brasileiro de Recursos Hídricos, ABRHidro, 2022. [PDF](https://files.abrhidro.org.br/Eventos/Trabalhos/142/XXIV-SBRH0055-2-0-20221031-214239.pdf)
- Área da bacia do Itapocu (2.919,8 km², incluindo a bacia litorânea do Itajuba): [anais Even3](https://static.even3.com/anais/663620.pdf) (consulta).
- **CEMADEN**, estação hidrológica 6637 “Rio Novo” (Corupá), desativada desde 2024. Consultada no início; as cotas dela não são mais usadas.
- Coordenadas da confluência (26°26′S 49°14′W): [Wikipedia](https://en.wikipedia.org/wiki/Humboldt_River_(Brazil)) · relevo: [topographic-map.com](https://pt-br.topographic-map.com/map-r3q5cz/Itapocu/)
- Área no ponto (~400 km²), forma do canal e nível normal da régua (0,40 m): estimativas próprias a partir das fontes acima e de imagem de satélite.

## Métodos científicos

- **HBV**: Bergström, S. (1976). *Development and application of a conceptual runoff model for Scandinavian catchments*. SMHI, Reports RHO 7. Versão simplificada, passo horário.
- **Cascata de Nash**: Nash, J. E. (1957). *The form of the instantaneous unit hydrograph*. IAHS Publ. 45.
- **Relação Z-R (radar → chuva)**: Marshall, J. S.; Palmer, W. McK. (1948). *The distribution of raindrops with size*. Journal of Meteorology, 5, 165–166 (Z = 200·R^1,6). Com correção do viés médio do radar em relação aos pluviômetros.
- **Curva-chave** Q = K·(H − H₀)^n, ajustada à régua da Ponte Corupá. Não há medição de vazão no ponto, então é estimativa.
- **Previsão imediata por radar**: deslocamento do campo de chuva medido por correlação entre quadros; sem movimento definido, usa a chuva atual perdendo força aos poucos.

## Conferência com boletins (06/10/2026)

- [CBN Total: chuva supera 100 mm em 24 horas](https://cbntotal.com.br/cotidiano/chuva-supera-100-mm-em-24-horas-em-cidade-e-provoca-alagamentos-e-interdicoes-no-norte-de-sc/)
- [ND+: rios chegam a 3,5 metros](https://ndmais.com.br/tempo/rios-chegam-3-5-metros-chuva-continua-norte-sc/)
- [NSC Total: cota de alerta máximo em Jaraguá do Sul](https://www.nsctotal.com.br/tempo/chuva-faz-rio-atingir-cota-de-alerta-maximo-ocupar-area-alagavel-em-jaragua-do-sul-e-forcar-desvios-no-transito)

## Software

Gráficos: [Chart.js](https://www.chartjs.org/) (MIT). Coleta e modelo: Python, [requests](https://requests.readthedocs.io/) e [Pillow](https://python-pillow.org/).
