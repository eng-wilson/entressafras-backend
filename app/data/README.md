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

# Dados: SMAP L4 (`smap/`)

Umidade do solo diária do [SMAP L4 v8](https://developers.google.com/earth-engine/datasets/catalog/NASA_SMAP_SPL4SMGP_008)
(`NASA/SMAP/SPL4SMGP/008`, Google Earth Engine), salva por `scripts/baixar_smap.py`. Nome:
`smap_v1_<lat>_<lon>_<inicio>_<fim>.json` (centro da célula de 0,1° e a janela de safras).

- Formato: `{"pixel": {lat, lon}, "bandas": [...], "dias": {"AAAA-MM-DD": [valores]}}`.
- Bandas: `sm_surface` (0–5 cm) e `sm_rootzone` (0–100 cm) em m³/m³, `sm_rootzone_wetness` (fração
  0–1) e `sm_rootzone_pctl` (percentil 0–100). `null` = mascarado.
- Média das 8 amostras de 3 h no fuso America/Cuiaba; dias com menos de 7 amostras são descartados.
- É produto de modelo (GEOS Catchment) com assimilação do satélite SMAP, não observação direta.

Domínio público. Cite: Reichle, R. et al. (2025). SMAP L4 Global 3-hourly 9 km EASE-Grid Surface
and Root Zone Soil Moisture Geophysical Data, Version 8. NSIDC DAAC. doi:10.5067/T5RUATAQREF8.

# Dados: CropWat / FAO-56 (`cropwat_crop_params.tsv`)

`crop_params.tsv` do pacote R [INRAE CropWat](https://inrae.r-universe.dev/CropWat) 0.3.0
(`inst/extdata/`), sem alterações. Salvo por `scripts/download_cropwat.py [versao]`.

- 55 perfis × 21 colunas, UTF-8, separado por tab; ausente = `NA`. Só os 37 `FAO-*` são usados
  (padrões do CropWat 8 / FAO-56); `KURN-*` (Índia) e `SB2023-*` (França, sem Ky) ficam de fora.
- Colunas: `Kini/Kmax/Kend` (Kc), `Lini/Ldev/Lmid/Lend` (fases, dias), `Zini/Zend` (raiz, m),
  `p_ini/p_mid/p_end` (fração de depleção), `Yini…Ytot` (Ky), `h` (altura, m), `sowing_date`.
- São **valores de referência**, não calibrados para cultivares ou regiões brasileiras.
- A ligação com o EcoCrop (`eco_port_code` → perfil) é curada em `app/services/cropwat.py`.

## Licença e citação

O pacote CropWat é distribuído sob **AGPL-3.0 ou posterior** (David Dorchies, INRAE); confira as
condições antes de usar em produto fechado/comercial. Os valores vêm das tabelas do FAO-56. Cite:
Allen, R. G. et al. (1998). Crop evapotranspiration: guidelines for computing crop water
requirements. FAO Irrigation and Drainage Paper 56. Roma: FAO.
