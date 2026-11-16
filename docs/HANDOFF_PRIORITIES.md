# Handoff: prioridades do ranking (`priorities`)

Para o frontend. Este documento cobre as prioridades que o backend aceita, o que cada uma mede e
como elas mudam a ordem das rotações. O contrato completo está no Swagger (`/docs`) e em
`app/schemas/rotation.py` (`Priorities`, `RotationScores`).

## Resumo para quem implementa

- São **4 prioridades**, cada uma `0` (desligada) ou `1` (ligada):
  - `productivity`, `water_security` e `soil_protection` vêm ligadas por padrão;
  - `revenue` vem desligada.
- O ranking ordena pela **média das notas das prioridades ligadas**.
- Se tudo estiver desligado, ordena pelo menor risco hídrico.
- As **notas** (`scores`) são sempre devolvidas, todas as quatro, mesmo para prioridades desligadas.
  As prioridades só mudam a **ordem**, nunca o valor das notas.
- O campo é o mesmo em `POST /api/rotations/recommendations` e `POST /api/rotations/simulate`.
- A resposta devolve `priorities` com os valores efetivamente usados, já com os padrões preenchidos.
  Use esse campo para sincronizar os toggles.

## Request

```json
POST /api/rotations/recommendations
{
  "location": { "lat": -12.55, "lon": -55.72 },
  "agricultural_year": 2027,
  "priorities": {
    "productivity": 1,
    "water_security": 1,
    "soil_protection": 1,
    "revenue": 0
  }
}
```

| Campo | Padrão | Nota usada (`scores.*`) | O que mede |
|---|---|---|---|
| `productivity` | `1` | `scores.productivity` | Produtividade preservada: 100 − perda média de rendimento por falta de água (FAO Ky), da **pior** das culturas da rotação. 100 = sem perda. |
| `water_security` | `1` | `scores.water_security` | % de safras históricas (30 anos) sem perda crítica (> `assumptions.loss_threshold_pct`, hoje 20%), da **pior** cultura. 100 = nenhuma safra crítica. |
| `soil_protection` | `1` | `scores.soil_protection` | Cobertura do solo planejada e troca de família botânica: 70% cobertura + 30% diversidade. 100 = solo coberto todos os dias e toda transição muda de família. |
| `revenue` | `0` | `scores.revenue` | Faturamento bruto de referência (preço ao produtor × produtividade, média do Brasil, FAOSTAT), com cada cultura descontada pela sua perda hídrica no local. A nota é dividida pelo teto, que é o maior faturamento bruto, sem perda, entre as rotações viáveis no local. O teto muda conforme o local. 100 = a melhor rotação viável do local sem perda hídrica; uma rotação customizada acima do teto fica em 100. |

### Regras de entrada

- **Valores aceitos:** `0` ou `1`. `2`, `-1` ou `1.5` → **422**. `true`/`false` e `"1"` também são
  aceitos e convertidos, mas envie `0`/`1`.
- **Envio parcial:** pode enviar só parte do objeto, e o que faltar recebe o padrão.
  `{"revenue": 1}` liga o faturamento **e mantém as outras três ligadas**. Para usar só o
  faturamento, envie as quatro chaves explicitamente.
- **Chave desconhecida é ignorada sem erro.** Um erro de digitação como `"revenu": 1` não liga
  nada. Confira em `priorities` da resposta.
- **Objeto omitido:** se `priorities` não for enviado, vale o padrão
  `{1, 1, 1, 0}`.

## Como o ranking usa as prioridades

1. Para cada rotação candidata, o backend faz a **média das notas das prioridades ligadas**. Os
   pesos são iguais e não há pesos personalizados.
2. Ordena da maior média para a menor. Em caso de empate, a rotação com menor risco hídrico vem
   primeiro.
3. Uma rotação com nota `null` numa prioridade ligada vai para o **fim** da lista. Isso acontece
   quando falta o risco hídrico, ou falta o faturamento de uma cultura fora do catálogo de 9 culturas.
4. **Tudo desligado:** ordena pelo pior risco hídrico individual. A resposta traz o aviso
   `"Every priority is off: ..."`.

As prioridades também decidem:
- **a melhor data de plantio** de cada par de culturas em `/recommendations`;
- **a posição** (`rank`, `would_be_recommended`) das rotações personalizadas;
- **o melhor e o pior ponto da varredura** (`best_decendio_idx` / `worst_decendio_idx`) em
  `/simulate`.

Por isso, ao mudar um toggle, refaça a requisição. Não dá para reordenar no cliente, porque a
data de plantio escolhida para cada par também pode mudar.

## Response: `scores`

Aparece em `rotations[].scores`, `custom_rotations[].scores`, `rotation.scores` (simulate) e
`*_planting_sweep.points[].scores`. Exemplo real: soja + milho em Sorriso, 2027:

```json
"scores": {
  "productivity": 98.3,
  "water_security": 96.7,
  "soil_protection": 71.3,
  "revenue": 40.7,
  "overall": 88.8,
  "aggregation": "WORST_CROP"
}
```

| Campo | Observação |
|---|---|
| `productivity`, `water_security`, `revenue` | Podem vir `null`, quando falta o risco ou o faturamento. |
| `soil_protection` | Sempre preenchido. |
| `overall` | Média de **productivity, water_security e soil_protection apenas**: não inclui `revenue` e não depende das prioridades. Não use `overall` como "a nota que ordenou a lista". Quando as prioridades não são as três agronômicas, a ordem não segue `overall`. |
| `aggregation` | Sempre `"WORST_CROP"`: productivity e water_security usam a pior cultura da rotação. |

Campos ligados ao faturamento, por rotação (detalhes no
`HANDOFF_SIMULATE_SINGLE_CROP.md`, seção "faturamento"):

| Campo | O que é |
|---|---|
| `gross_revenue_usd_ha` | Soma do faturamento de referência das culturas, sem desconto. |
| `water_adjusted_revenue_usd_ha` | Com o desconto hídrico. É o valor por trás de `scores.revenue`. |

## Avisos (`warnings`) que dependem das prioridades

| Quando | Começa com |
|---|---|
| Alguma prioridade ligada | `"Rotations are ranked by the mean of the enabled priorities (...)"` (lista as ligadas) |
| Tudo desligado | `"Every priority is off: ..."` |
| `soil_protection: 1` | `"The soil_protection priority favors long cycles ..."`: ciclos longos cobrem o solo, mas costumam ter mais risco hídrico. |
| `revenue: 1` | `"The revenue priority uses the national FAOSTAT reference ..."`: estimativa nacional, sem custos de produção. Com `productivity` também ligada, a perda hídrica pesa duas vezes. |

## Exemplo: Sorriso-MT, ano agrícola 2027, solo médio

| Prioridades | 1ª | 2ª | 3ª | 4ª |
|---|---|---|---|---|
| Padrão (3 agronômicas) | caupi + girassol | caupi + sorgo | soja + girassol | soja + sorgo |
| Só `revenue` | soja + algodão (69) | feijão + algodão (63) | caupi + algodão (58) | soja + milho (41) |

## Sugestões de UI

- **Toggles:** quatro toggles; revenue começa desligado.
- **Atalho de faturamento:** se houver um botão "priorizar faturamento", envie as quatro chaves,
  por exemplo `{"productivity": 0, "water_security": 0, "soil_protection": 0, "revenue": 1}`.
- **Gráficos:** mostre as quatro notas mesmo com prioridade desligada. Elas são escalas absolutas
  (0–100) e podem ser comparadas entre rotações e requisições.
- **Texto junto à nota de faturamento:** "estimativa com base em médias nacionais (FAO), sem
  custos". O aviso do backend tem o texto completo.
