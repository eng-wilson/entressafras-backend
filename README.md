# entressafras-backend

Backend em Python com FastAPI + Uvicorn, gerenciado com [uv](https://docs.astral.sh/uv/).

## Setup

```bash
uv sync
cp .env.example .env
```

## Rodar

```bash
uv run uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Docs (Swagger): http://localhost:8000/docs
- Health check: http://localhost:8000/api/health

## Culturas (FAO EcoCrop)

Dados de requisitos agroclimáticos de 2.568 culturas, lidos de `app/data/EcoCrop_DB.csv`
(origem e licença em `app/data/README.md`).

- `GET /api/crops/{eco_port_code}`: detalhe da cultura (ex.: `/api/crops/2114` = trigo).
  Traz temperatura (°C), chuva (mm/ano), pH, latitude, altitude, luz, solo, fotoperíodo,
  zonas climáticas e ciclo (dias). Valores ausentes vêm como `null`.
- `GET /api/crops?q=trigo`: busca por nome científico ou popular, sem diferenciar
  maiúsculas nem acentos (ordem: exato > prefixo > contém). Filtros: `category`,
  `life_span`, `life_form`. Paginação: `limit` (1–100, padrão 20) e `offset`.

Os valores de texto ficam em inglês, como na FAO. Para atualizar o CSV:
`uv run python scripts/download_ecocrop.py`.

## Clima (NASA POWER, por decêndio)

Série diária do [NASA POWER](https://power.larc.nasa.gov/) (comunidade AG) das **10 últimas safras
completas** (setembro a agosto, encerradas há pelo menos 30 dias). A ET0 é calculada aqui por
Penman-Monteith FAO-56, com a pressão de vapor pelo ponto de orvalho, a umidade relativa como
reserva e Hargreaves quando faltam radiação ou vento.

- `GET /api/clima/decendios?lat=-12.55&lon=-55.72`: 36 decêndios (0 = 1–10/set, 35 = 21–31/ago).
  Traz chuva média e P20 (superada em 8 de cada 10 anos), ET0, Tmin/Tmax (média, média dos
  extremos de cada safra e extremo absoluto) e umidade do solo (`GWETTOP`/`GWETROOT`, 0–1).
  `null` = "Sem dado" (menos de 5 safras válidas).
- `GET /api/clima/diario?lat&lon[&inicio=AAAA-MM-DD&fim=AAAA-MM-DD]`: série diária bruta com
  a ET0, recortada da mesma janela (fora dela, 422).

**Resolução:** o ponto é encaixado na célula MERRA-2 (0,5° × 0,625°, ~50 km). O resultado vale
para a região, não para o talhão, e talhões vizinhos devolvem os mesmos números.

**Cache (obrigatório: a NASA bloqueia consultas repetidas):** uma consulta por célula, lida
primeiro de `app/data/power/` (semente versionada, demo sem rede), depois de `.cache/power/`
(local, fora do git) e só então da rede. Para pré-baixar um ponto:
`uv run python -m scripts.baixar_power [lat lon]` (padrão: Sorriso-MT).

## Testes e lint

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

## Estrutura

```
app/
  main.py            # instância FastAPI
  core/config.py     # configurações (pydantic-settings, lê .env)
  api/router.py      # agrega os routers
  api/routes/        # endpoints
  schemas/           # modelos Pydantic de resposta
  services/          # acesso a dados (EcoCrop)
  data/              # CSV do FAO EcoCrop
scripts/             # utilitários (download do CSV)
tests/
```
