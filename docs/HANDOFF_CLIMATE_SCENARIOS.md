# Handoff: cenários de clima futuro (`climate_scenario`)

Para o frontend. O contrato completo está no Swagger (`/docs`), em `app/schemas/rotation.py`
(`RotationContext.climate_scenario`) e em `app/schemas/clima.py` (`ClimateScenarioInfo`).

## Resumo para quem implementa

- Hoje o risco vem dos **últimos 30 anos de clima**. Agora o usuário também pode ver o risco num
  **clima futuro** (mais quente, ou mais quente e seco).
- O usuário escolhe **um** cenário. **Todas** as recomendações e simulações passam a usar o risco
  daquele cenário. Não há comparação lado a lado.
- Com `historical`, que é o padrão, a tela fica **idêntica** à de hoje.
- O campo é o mesmo em `POST /api/rotations/recommendations` e `POST /api/rotations/simulate`.

## Fluxo do usuário

1. Na tela de recomendações há um seletor **Clima**. Ele começa em "Clima atual (30 anos)".
2. O usuário escolhe "2030: mais quente e seco".
3. O front refaz `POST /api/rotations/recommendations` com `"climate_scenario": "warmer_drier"` e
   **substitui** o resultado inteiro (rotações, riscos, notas, ordem), como já acontece ao mudar as
   prioridades.
4. Uma faixa abaixo do seletor mostra o que o cenário muda e a fonte (`description` + `source`).
5. Enquanto o cenário estiver selecionado, toda chamada a `/simulate`, inclusive ao arrastar datas,
   envia o mesmo `climate_scenario`.

## Listar os cenários

```
GET /api/clima/scenarios
```

```json
[
  {"id": "historical", "label": "Current climate (30 seasons)", "delta_temp_c": 0.0,
   "rain_change_pct": 0.0, "onset_delay_days": 0,
   "description": "Observed climate, no adjustment", "source": "NASA POWER"},
  {"id": "warmer", "label": "2030: warmer", "delta_temp_c": 1.0, "rain_change_pct": 0.0,
   "onset_delay_days": 0, "description": "+1.0 °C", "source": "Illustrative values, ..."},
  {"id": "warmer_drier", "label": "2030: warmer and drier", "delta_temp_c": 1.5,
   "rain_change_pct": -10.0, "onset_delay_days": 10,
   "description": "+1.5 °C, −10% rainfall, rainy season starts 10 days later",
   "source": "Illustrative values, ..."}
]
```

- Monte o seletor com esta lista, na ordem em que ela vem (`historical` é sempre o primeiro).
- **Não fixe os números no front.** Os deltas vão mudar quando entrarem os valores oficiais do
  NEX-GDDP-CMIP6.
- `label` e `description` vêm em inglês. Se a interface for em português, traduza pelo `id` e monte
  o texto a partir de `delta_temp_c`, `rain_change_pct` e `onset_delay_days`. Por exemplo:
  "+1,5 °C, −10 % de chuva, chuvas começam 10 dias depois".

## Request

```json
POST /api/rotations/recommendations
{
  "location": { "lat": -12.55, "lon": -55.72 },
  "agricultural_year": 2027,
  "climate_scenario": "warmer_drier"
}
```

| Campo | Padrão | Valores |
|---|---|---|
| `climate_scenario` | `"historical"` | `historical`, `warmer`, `warmer_drier` (os `id` de `/clima/scenarios`) |

Um valor desconhecido devolve **422**.

## Response

As duas rotas devolvem o cenário usado:

```json
"climate_scenario": {
  "id": "warmer_drier", "label": "2030: warmer and drier", "delta_temp_c": 1.5,
  "rain_change_pct": -10.0, "onset_delay_days": 10, "description": "...", "source": "..."
}
```

O resto da resposta não muda de formato. Riscos (`crops[].crop.risk.*`), notas (`scores`), ordem e
varreduras de plantio já vêm calculados com o clima do cenário. Use `climate_scenario` da resposta
para sincronizar o seletor.

## Desempenho

A primeira chamada de cada cenário, para um local e solo, simula tudo de novo (~1,5 s). As seguintes
vêm do cache. Mostre um loading ao trocar o cenário.

## Texto sugerido

- Tooltip do seletor: *"Cenários de clima futuro: usamos os 30 anos reais ajustados pelas projeções
  climáticas (+temperatura, −chuva). Não é uma previsão do próximo ano."*
- Enquanto `source` disser "Illustrative values", mostre uma etiqueta discreta **"valores
  ilustrativos"** na faixa do cenário.

## Critérios de aceite

- O seletor é preenchido por `GET /api/clima/scenarios`, com `historical` selecionado por padrão.
- Com `historical`, a tela fica idêntica à de hoje.
- Trocar o cenário atualiza todas as recomendações com o risco do novo cenário.
- `/simulate` usa o mesmo cenário selecionado.
- A faixa com a descrição e a fonte aparece sempre que o cenário não for `historical`.
