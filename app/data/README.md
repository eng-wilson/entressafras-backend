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

# Dados: ISRIC SoilGrids 2.0 (`soilgrids/`)

Respostas JSON brutas da [API REST do SoilGrids](https://rest.isric.org/soilgrids/v2.0/docs)
(`/properties/query`: sand, silt, clay, soc; 0–100 cm), salvas por
`scripts/download_soilgrids.py`. Nome: `soilgrids_v1_<lat>_<lon>.json` (4 casas decimais).

- Semente de Sorriso-MT: o centro da cidade e os pontos a 2 km ao norte e a leste são área urbana
  (valores `null`); o primeiro ponto com dados fica a 2 km ao sul (-12,568; -55,72).
- Unidades brutas: g/kg (÷10 → %) e soc em dg/kg (÷10 → g/kg).

Licença CC BY 4.0. Cite: Poggio, L. et al. (2021). SoilGrids 2.0: producing soil information for
the globe with quantified spatial uncertainty. SOIL 7:217–240. doi:10.5194/soil-7-217-2021.

# Dados: calendário agrícola GGCMI Phase 3 (`ggcmi/`)

`ggcmi_phase3_v1.01_rf.npz` (numpy, compactado) reúne, para 8 culturas de sequeiro (soy, mai,
sor, mil, cot, bea, sun, nut), a grade global de 0,5° do
[GGCMI Phase 3 crop calendar v1.01](https://doi.org/10.5281/zenodo.5062513). Gerado por
`uv run --with netCDF4 python -m scripts.build_ggcmi_calendar`.

- Arrays: `lat`, `lon` e, por cultura, `<crop>_planting_day` (dia do ano), `<crop>_season_length`
  (dias) e `<crop>_data_source` (1 MIRCA2000, 2 SAGE, … 8 Brazil CONAB); ausente = `-1`.
- Em Sorriso (célula -12,75; -55,75): soja 31/out, 100 dias; milho 14/fev, 116 dias (safrinha);
  ambos com dados da CONAB. O milheto vem do MIRCA2000 (180 dias), o que é pouco plausível para o
  milheto safrinha de MT.

Licença CC BY 4.0. Cite: Jägermeyr, J. et al. (2021). Climate impacts on global agriculture
emerge earlier in new generation of climate and crop models. Nature Food 2:873–885.
doi:10.1038/s43016-021-00400-y.

# Dados: preço, produtividade e faturamento de referência (`prices/`)

`crop_economics.json` traz, para as 9 culturas da rotação, o preço recebido pelo produtor, a
produtividade e o faturamento bruto de referência (produtividade/1000 × preço, USD/ha). Gerado por
`uv run --with openpyxl python -m scripts.build_crop_economics` (downloads em `.cache/crop_economics/`).

- **Preço ao produtor:** [FAOSTAT Producer Prices (PP)](https://www.fao.org/faostat/en/#data/PP),
  elemento "Producer Price (USD/tonne)", valor anual, Brasil.
- **Produtividade:** [FAOSTAT Crops and livestock products (QCL)](https://www.fao.org/faostat/en/#data/QCL),
  elemento "Yield" (kg/ha), Brasil.
- Ambos são a média dos 3 últimos anos com dado (hoje 2022–2024), em USD nominal.
- **Referência internacional** (só exibição): [World Bank Pink Sheet](https://www.worldbank.org/en/research/commodity-markets),
  último mês publicado. Soja, milho, algodão (pluma, Cotlook A, convertido de USD/kg) e amendoim
  (descascado). Sorgo foi descontinuado pelo Banco Mundial; girassol só tem óleo; feijão, caupi e
  milheto não têm série.
- **Proxies** (`method: "proxy"`): o FAOSTAT não tem milheto nem caupi no Brasil. Milheto usa o preço
  do sorgo (Brasil) e a produtividade de milheto da América do Sul; caupi usa preço e produtividade de
  "Beans, dry" (Brasil), que na estatística brasileira de feijão inclui o caupi.
- É uma média nacional, não previsão para a fazenda: não aplica o risco hídrico do local (a média já
  embute perdas típicas) e não desconta custos de produção.
- **Prioridade `revenue`** (opcional, desligada por padrão): ordena pela soma do faturamento de cada
  cultura × (1 − perda hídrica estimada no local). A nota 0–100 usa como teto o maior faturamento
  bruto, sem perda, entre as rotações viáveis no local (família, calendário e temperatura). O teto
  muda de local para local, e a melhor rotação só chega a 100 se não tiver perda hídrica. Não entra
  em `overall`.
- O link do Pink Sheet muda a cada ano (id do documento); pegue o atual em worldbank.org/commodities
  e passe em `--pinksheet-url`.

Licença CC BY 4.0 (FAOSTAT e World Bank). Cite: FAO. FAOSTAT: Producer Prices; Crops and livestock
products. Rome. https://www.fao.org/faostat/ — e World Bank. Commodity Price Data (The Pink Sheet).

# Cenários de clima futuro (`app/services/climate_scenario.py`)

Não há arquivo de dados: os cenários são deltas aplicados à série do NASA POWER pelo método delta
(*change factor*). Tmax e Tmin recebem +ΔT, a chuva é multiplicada por (1 + Δ%) e, se pedido, a chuva
de 15/ago a 31/dez é deslocada N dias para frente (atraso no início das chuvas). A ET0 é recalculada
com a mesma rotina FAO-56; ponto de orvalho e umidade relativa ficam iguais.

- **Valores atuais: ilustrativos** (`warmer` +1,0 °C; `warmer_drier` +1,5 °C, −10 % de chuva, 10 dias
  de atraso). Ainda precisam ser substituídos pela média multimodelo do NASA NEX-GDDP-CMIP6 (ou do
  IPCC WGI Interactive Atlas), SSP2-4.5, 2021–2040 em relação a 1995–2014, região Centro-Oeste.
- Referência do método: IPCC TGICA (2007), *General Guidelines on the Use of Scenario Data for Climate
  Impact and Adaptation Assessment*, v2, seção sobre o método delta.
- São cenários "e se", não uma previsão da próxima safra.
