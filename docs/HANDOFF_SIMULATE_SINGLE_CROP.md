# Handoff: simular 1 cultura em `POST /api/rotations/simulate`

Para o frontend. O backend aceita uma rotação personalizada de **1 ou 2 culturas** (desde o commit
`8a258f1`). Com 1 cultura, a safra é simulada como **cultura + pousio** até 31/ago. O contrato
completo está no Swagger (`/docs`) e em `app/schemas/rotation.py`.

## Resumo para quem implementa

- `rotation.crops` aceita 1 ou 2 itens (0 ou 3+ → 422).
- Com 1 cultura: `second_planting_sweep` vem `null`, todos os arrays `crops` têm 1 item e o índice
  de cobertura do solo (`soil_protection`) cai por causa do pousio.
- `rank`/`would_be_recommended` comparam a cultura única contra as rotações recomendadas (sempre
  de 2 culturas), com as mesmas notas. Por isso a cultura única quase sempre fica atrás.
- A mesma regra vale em `custom_rotations` de `POST /api/rotations/recommendations`.

## Request

Mínimo (datas pelo calendário regional):

```json
POST /api/rotations/simulate
{
  "location": { "lat": -12.55, "lon": -55.72 },
  "agricultural_year": 2027,
  "rotation": { "crops": [ { "code": 1150 } ] },
  "sweep": true
}
```

Com datas (ex.: o agricultor arrastou o card):

```json
{
  "location": { "lat": -12.55, "lon": -55.72 },
  "rotation": {
    "crops": [
      { "code": 1150, "planting_date": "2027-10-11", "harvest_date": "2028-02-10" }
    ]
  },
  "sweep": false
}
```

Campos opcionais iguais aos da rotação de 2 culturas: `soil_type`, `agricultural_year`,
`crop_history`, `priorities`.

Regras de entrada:

| Campo | Regra |
|---|---|
| `crops[0].code` | Cultura com perfil hídrico FAO-56. Liste com `GET /api/crops?simulable=true`. |
| `crops[0].planting_date` | Opcional. Padrão: data do calendário regional (ou 1º/out sem calendário). Precisa estar dentro do ano agrícola (1º/set a 31/ago). |
| `crops[0].harvest_date` | Opcional. Define a duração do ciclo, que precisa ficar entre 50% e 150% do ciclo regional. Precisa ser depois do plantio. |
| `sweep` | `true` (padrão, ~1 s) devolve a varredura de datas; use `false` durante o arraste e `true` ao soltar. |

## Response: o que muda com 1 cultura

Mesma forma de `RotationSimulationResponse`; só muda a cardinalidade e alguns valores:

| Campo | Com 1 cultura |
|---|---|
| `rotation.crops` | 1 item (`crop`, `planting_date`, `harvest_date`, `calendar`). |
| `rotation.id` | `custom-{code}-{planting_date}`, ex. `custom-1150-2027-10-31`. |
| `rotation.checks` | Nunca traz o alerta de mesma família botânica. Pode trazer plantio fora do calendário (>30 dias), temperatura fora dos limites EcoCrop e colheita depois de 31/ago. |
| `rotation.soil_protection` | `transitions: 0`, `family_changes: 0`, `family_change_fraction: 0` (sem `crop_history`), `cover_fraction` = dias de ciclo ÷ dias da safra. Score típico de 10–25. |
| `rotation.scores.productivity` / `water_security` | Da única cultura (`aggregation: "WORST_CROP"` com 1 item). |
| `rotation.rank`, `ranked_among`, `would_be_recommended` | Posição entre as recomendadas (de 2 culturas) pelas `priorities`. Com `soil_protection` ligado, a cultura única tende a ficar atrás. |
| `first_planting_sweep` | `moved_crop: "first"`; `points[].crops` com 1 item. Só entram decêndios cuja colheita cai até 31/ago. |
| `second_planting_sweep` | Sempre `null`. |
| `warnings` | Inclui o aviso de rotação personalizada, que menciona o pousio. |

### Erros

| Status | Quando | `detail` |
|---|---|---|
| 422 | `crops: []` ou 3+ itens | Lista de validação do FastAPI (`too_short` / `too_long`) |
| 422 | Cultura sem perfil FAO-56 | `"... has no FAO-56 water profile ... GET /crops?simulable=true"` (string) |
| 422 | Plantio fora do ano agrícola, colheita antes do plantio ou ciclo fora de 50–150% | String começando com `custom rotation:` |
| 503 | Sem clima do POWER nem cache (só com deploy quebrado) | String |

Colheita depois de 31/ago **não** é erro: retorna 200 com um aviso em `checks`.

Ao exibir, trate `detail` como string ou lista (422 de validação).

## Checklist de UI

- [ ] Permitir soltar só 1 card na rotação e chamar o `/simulate` com `crops` de 1 item.
- [ ] Com `second_planting_sweep === null`, esconder o slider/heatmap da 2ª cultura (não mostrar vazio).
- [ ] Ler `rotation.crops` e `points[].crops` por iteração, nunca assumindo `[0]` e `[1]`.
- [ ] Na linha do tempo e na cobertura do solo, desenhar o período após `harvest_date` até 31/ago como **pousio**.
- [ ] Destacar `best_decendio_idx` / `worst_decendio_idx` do `first_planting_sweep` normalmente.
- [ ] Mostrar `checks` (alertas agronômicos) e `warnings` (premissas) como hoje.
- [ ] Se exibir o rank, explicar que é contra rotações de 2 culturas (ex.: "11º de 14").
- [ ] Na tela de recomendações, aceitar `custom_rotations[].crops` com 1 item.
- [ ] Ao adicionar uma 2ª cultura, refazer o request com 2 itens; `second_planting_sweep` passa a vir preenchido.

## Exemplo real (truncado)

Soja (`1150`) em Sorriso-MT, `soil_type: "medium"`, safra 2027, `sweep: true`. `crop` vem
truncado (é o mesmo detalhe de `GET /api/crops/{code}` + `risk`) e só um ponto da varredura é
mostrado (são 27).

```json
{
  "agricultural_year": 2027,
  "soil_type": "medium",
  "soil": { "source": "REQUEST_TEXTURE_CLASS", "texture_class": "medium", "available_water_mm_per_m": 150.0, "...": "..." },
  "priorities": { "productivity": 1, "water_security": 1, "soil_protection": 1, "revenue": 0 },
  "rotation": {
    "id": "custom-1150-2027-10-31",
    "rank": 11,
    "ranked_among": 14,
    "would_be_recommended": false,
    "crops": [
      {
        "crop": {
          "eco_port_code": 1150,
          "scientific_name": "Glycine max",
          "family": "Magnoliopsida:Rosidae:Fabales:Leguminosae",
          "...": "...",
          "risk": {
            "risk_percentage": 0.0,
            "estimated_yield_loss_pct": 0.1,
            "loss_threshold_pct": 20.0,
            "critical_stage": "initial",
            "mean_isna": { "initial": 0.991, "development": 1.0, "mid_season": 0.999, "late_season": 0.998 },
            "mean_season_isna": 0.998,
            "valid_years": 30,
            "method": "FAO56_SIMPLIFIED_KY",
            "validated": false
          }
        },
        "planting_date": "2027-10-31",
        "harvest_date": "2028-02-07",
        "calendar": {
          "source": "GGCMI_PHASE3_V1_01",
          "cycle_days": 100,
          "reference_planting_date": "2027-10-31",
          "data_source": "Brazil CONAB",
          "grid_cell": { "lat": -12.75, "lon": -55.75 }
        }
      }
    ],
    "soil_protection": {
      "score": 19,
      "living_cover_days": 100,
      "season_days": 366,
      "cover_fraction": 0.273,
      "family_change_fraction": 0.0,
      "transitions": 0,
      "family_changes": 0,
      "botanical_families": ["Fabaceae"],
      "includes_history": false,
      "weights": { "cover": 70, "diversity": 30 },
      "method": "PLANNED_COVER_DIVERSITY_V1",
      "validated": false
    },
    "scores": {
      "productivity": 99.9,
      "water_security": 100.0,
      "soil_protection": 19.1,
      "overall": 73.0,
      "aggregation": "WORST_CROP"
    },
    "checks": []
  },
  "first_planting_sweep": {
    "moved_crop": "first",
    "points": [
      {
        "decendio_idx": 0,
        "label": "1-10 Sep",
        "crops": [
          {
            "code": 1150,
            "planting_date": "2027-09-01",
            "harvest_date": "2027-12-09",
            "risk_percentage": 26.7,
            "estimated_yield_loss_pct": 14.4,
            "in_calendar": false,
            "temperature_ok": true
          }
        ],
        "scores": { "productivity": 85.6, "water_security": 73.3, "soil_protection": 19.1, "overall": 59.3, "aggregation": "WORST_CROP" }
      }
    ],
    "best_decendio_idx": 6,
    "worst_decendio_idx": 26
  },
  "second_planting_sweep": null,
  "assumptions": {
    "loss_threshold_pct": 20.0,
    "initial_soil_water_fraction": 0.2,
    "spinup_days": 30,
    "planting_gap_days": 5,
    "planting_tolerance_days": 30
  },
  "warnings": [
    "...",
    "Custom rotations are simulated as sent, without the recommendation filters (botanical family, temperature, regional calendar); issues are listed in their checks. Their rank is the position they would take among the recommended rotations. A one-crop rotation is ranked by the same indicators: the fallow after it lowers its soil_protection."
  ]
}
```

Exemplo de `checks` com plantio tardio (`planting_date: "2028-07-01"`, 200 OK):

```json
[
  "soya bean: planting on 2028-07-01 is 121 days from the regional calendar date (2027-10-31); recommendations accept up to 30.",
  "soya bean is harvested on 2028-10-08, after the end of the agricultural year (2028-08-31)."
]
```

## Testes de referência

`tests/test_rotations.py`: `test_single_crop_custom_rotation_leaves_the_season_fallow` e
`test_recommendations_accept_a_single_crop_custom_rotation`.

## Novo: faturamento de referência e prioridade `revenue`

Vale para `/recommendations` e `/simulate`, com 1 ou 2 culturas. Os exemplos acima não mostram
estes campos.

| Campo | O que é |
|---|---|
| `priorities.revenue` | `0` (padrão) ou `1`. Ligado, entra na média das prioridades que ordena as rotações e a varredura (`best_decendio_idx`). |
| `crops[].economics` | Preço ao produtor (US$/t), produtividade (kg/ha) e `gross_revenue_usd_ha` da cultura, média do Brasil (FAOSTAT, 3 últimos anos). Cada valor traz `source`, `series`, `period`, `area`, `url` e `method` (`direct` ou `proxy`: milheto e caupi). `international_price` (Banco Mundial) quando existe. `null` para culturas fora do catálogo de rotação. |
| `rotation.gross_revenue_usd_ha` | Soma do faturamento de referência das culturas, sem custos. |
| `rotation.water_adjusted_revenue_usd_ha` | Mesmo valor, com cada cultura descontada pela sua `estimated_yield_loss_pct` no local. |
| `scores.revenue` | `water_adjusted_revenue_usd_ha` ÷ teto × 100. Teto = as duas culturas de maior faturamento do catálogo sem perda (hoje algodão + amendoim, ~US$ 5.876/ha). Sempre preenchido, mesmo com a prioridade desligada; `null` se faltar dado. Também aparece em `points[].scores` da varredura. |
| `scores.overall` | Continua a média de productivity, water_security e soil_protection; **não** inclui revenue. |
| `warnings` | Com `revenue: 1`, inclui o aviso de que é uma estimativa nacional, sem custos, e que a perda hídrica pesa duas vezes se `productivity` também estiver ligada. |

Uma rotação de 1 cultura só soma o faturamento dela; o pousio não fatura, então a nota tende a ser
menor que a de uma rotação de 2 culturas.
