# Dados: FAO EcoCrop

`EcoCrop_DB.csv` é a base completa do [FAO EcoCrop](https://gaez.fao.org/pages/ecocrop),
copiada do repositório [OpenCLIM/ecocrop](https://github.com/OpenCLIM/ecocrop)
(arquivo `EcoCrop_DB.csv`, último commit `ea43ecd`, 2022-03-01).

- 2.568 culturas, 53 colunas. Significado das siglas: `Ecocrop_variable_lookup.csv` no repo de origem.
- **Encoding: latin-1** (não é UTF-8). Valores ausentes: `NA` ou vazio.
- Temperaturas em °C, chuva em mm/ano, altitude em m, ciclo em dias.

Para atualizar: `uv run python scripts/download_ecocrop.py [ref]`.

## Licença e citação

Distribuído sob a [Open Government Licence v3](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Cite o repositório OpenCLIM/ecocrop (DOI [10.5281/zenodo.10843625](https://zenodo.org/doi/10.5281/zenodo.10843625))
e a fonte FAO EcoCrop.

# Dados: NASA POWER (`power/`)

Respostas JSON brutas da API diária por ponto do [NASA POWER](https://power.larc.nasa.gov/)
(`/api/temporal/daily/point`, comunidade AG), salvas por `scripts/baixar_power.py`. Nome:
`power_v1_<lat>_<lon>_<inicio>_<fim>.json` (centro da célula MERRA-2 e a janela de safras).

- Parâmetros: T2M_MAX, T2M_MIN, T2MDEW (°C), RH2M (%), WS2M (m/s), ALLSKY_SFC_SW_DWN (MJ/m²/dia),
  PRECTOTCORR (mm/dia), GWETTOP e GWETROOT (fração 0–1). Valor ausente: `-999`.
- Semente atual: Sorriso-MT (célula -12,5; -55,625), safras 2016/17 a 2025/26.

Os dados são públicos. Cite: "These data were obtained from the NASA Langley Research Center (LaRC)
POWER Project funded through the NASA Earth Science/Applied Science Program."
