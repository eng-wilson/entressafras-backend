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
