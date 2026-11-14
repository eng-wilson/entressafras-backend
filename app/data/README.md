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
