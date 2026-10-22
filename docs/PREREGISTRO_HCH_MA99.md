# Pre-registro — Long en la MA99 después de un hombro-cabeza-hombro de techo

> **Fijado el 2026-10-22**, confirmado por Nichelson, antes de calcular un
> solo resultado. Hasta este commit solo existían el detector
> (`marea/patterns.py`) y el conteo de eventos de la §6, que no mira
> retornos. Después de correr, este archivo no se edita salvo para enlazar
> el artefacto.

---

## 1. Qué se pregunta y por qué

Nichelson (22-10) quiere entrar long en ETHUSDT perpetuo cerca de **2.300**.
Su lectura del gráfico diario: un **HCH de techo** (hombro 2.666 el 11-09,
cabeza 2.807 el 21-09, hombro 2.779 el 02-10, cuello 2.357) y la **MA99
simple** en ~2.301 y subiendo. La idea es que, si la tendencia sigue alcista,
el toque a la MA99 tiene momentum y fuerza. Piensa usar 500 USD, 10x,
margen aislado, 5 o más días.

La lectura clásica del HCH de techo dice lo contrario: es una figura de
giro bajista. Ninguna de las dos lecturas está medida en el repo. Lo que sí
está medido (05-10, `PREREGISTRO_TECNICO.md`) es que barridos y order blocks
en ETH 15m/1h no tienen ventaja. Esta medición contesta tres preguntas:

1. ¿El long en la MA99 después de un HCH de techo gana, neto de costos?
2. ¿El HCH agrega algo, o es lo mismo que comprar cualquier toque de la MA99?
3. ¿Cuántas veces, a 10x, el precio atravesó la liquidación antes del stop?

## 2. Hipótesis

- **H1 (la tesis):** la expectancy neta del trade de la §3.3, en R, es > 0.
- **H0:** es ≤ 0. Sin ventaja, el valor esperado es −costo.

## 3. Definiciones fijadas

### 3.1 Universo y muestra

- Panel diario de `universe-1d.json`: 659 perpetuos USDT con los
  deslistados incluidos (Fase 10).
- Elegibilidad de `bob.signals.universe.eligibility_mask`, sin cambios:
  180 velas de historia previa y mediana de volumen ≥ 5 M USD en 30 días.
- Velas hasta el cierre del **2026-10-21** (`--until 2026-10-21`).

### 3.2 La figura (`marea.patterns.detect_hs_top`, defaults)

- Pivotes `swing_points` con **k = 3** y zigzag alternado. Con k = 5 el
  zigzag se come el hombro derecho de ETH.
- La cabeza supera a los dos hombros por ≥ 0,5%; los hombros difieren
  ≤ 5% de la cabeza; ancho de hombro a hombro de 10 a 90 velas; cuello
  horizontal en `min(L1, L2)`; cada hombro ≥ 3% sobre el cuello.
- La figura **se conoce** en `j_H3 + 3`. Verificado sobre ETH: reconoce la
  figura de Nichelson y la da por conocida el 2026-10-05.

### 3.3 El trade

- **Condición**: figura conocida en `t`, símbolo elegible en `t`, cierre de
  `t−1` sobre la MA99.
- **Nivel**: MA99 simple de los 99 cierres **anteriores** (`prior_sma`): es
  lo que se conoce al abrir la vela y donde se deja la orden límite.
- **Entrada**: la primera vela `j` en `[t, t+30)` con mínimo ≤ nivel y MA99
  creciente (nivel de `j` > nivel de `j−10`), elegible en `j`. Se llena al
  nivel, o a la apertura si la vela abre por debajo (gap). Si la primera
  vela que toca no cumple la pendiente, no hay trade.
- **Stop**: entrada − **1,1 × ATR14**: media simple del rango verdadero de
  las 14 velas **anteriores** a la de entrada (la misma cuenta que
  `marea.tradeplan`). Es
  la geometría de Nichelson: 2.190 ≈ 2.301 − 1,1 × 101,6.
- **Salida**: el stop si el mínimo lo toca (al stop, o a la apertura si abre
  debajo); si no, el cierre de la vela `entrada + 6` (**H = 7 días**).
  Si la vela de entrada toca el nivel y el stop, cuenta como stop: es el
  supuesto conservador.
- **Costo**: 0,14% del notional ida y vuelta, más funding de 0,01% cada 8 h
  durante los días abiertos.
- **Resultado en R** = (salida − entrada − costos) / (entrada − stop).

### 3.4 Unidad de inferencia

Los eventos de una misma semana están correlacionados (en cripto la
correlación media entre pares es +0,42, Fase 10). El IC sale de un
**bootstrap por bloques de semanas calendario**: se remuestrean semanas, no
eventos. Semana = semana ISO en UTC (lunes a domingo) de la vela de entrada.
10.000 remuestreos, semilla 20261022, IC por percentiles 2,5 / 97,5. La
diferencia tesis − B1 se remuestrea con las mismas semanas en las dos series.

## 4. Prueba confirmatoria (una)

**C1.** Expectancy media en R del trade de la §3.3, con IC95% por bloques
de semanas. **Pasa si el límite inferior es > 0.**

Es la única que decide. Una exploratoria positiva no la reemplaza.

## 5. Comparaciones obligatorias (no deciden, se reportan siempre)

- **B1, el toque sin figura:** el mismo trade en todo toque de la MA99
  creciente desde arriba, con 20 días de enfriamiento por símbolo y sin
  exigir HCH. La diferencia tesis − B1, con su IC, dice si la figura agrega
  algo.
- **Placebo:** el mismo stop en ATR y el mismo H, entrando a la apertura en
  días al azar de los mismos símbolos, elegibles, sobre la MA99 y con MA99
  creciente: 5.000 sorteos sin reposición, semilla 20261022. Debe dar ≈ −costo. Si no, la plomería fabrica
  resultado y nada de lo demás se lee.
- **Liquidación a 10x:** fracción de trades donde el mínimo entre entrada y
  salida pasó el precio de liquidación aislado a 10x (−9,5%, misma fórmula
  que `projection.py`). Con el stop más cerca, solo pasa por gap.
- Por año, peor trade, racha de pérdidas más larga y % de stops.

## 6. Potencia, medida antes de fijar el criterio (solo conteos)

Conteo con el detector y las condiciones de §3.2–3.3, sin calcular
retornos:

| | eventos | semanas distintas |
|---|---|---|
| HCH elegibles en su confirmación | 2.196 | — |
| **tesis: HCH → toque de MA99 creciente en ≤ 30 d** | **493** | **146** |
| B1: todo toque de MA99 creciente (enfriamiento 20 d) | 3.110 | 279 |

Por año, tesis: 2020: 8 · 2021: 70 · 2022: 19 · 2023: 82 · 2024: 159 ·
2025: 97 · 2026: 58.

Con 146 semanas como unidades independientes y un desvío supuesto de
**~0,9 R por semana** (supuesto, no estimado de resultados), el efecto
mínimo detectable es de **~0,2 R** por trade (α = 0,025 a una cola,
potencia 80%). Un edge más chico que eso no se puede distinguir de cero con
esta muestra, y el reporte lo dirá así.

En ETH solo hay 26 HCH desde 2019, y la tesis se dispara en unos pocos.
**ETH solo no tiene potencia** y se reporta descriptivo.

## 7. Exploratorias declaradas (no deciden)

1. H = 5 y H = 10 días.
2. Stop a 1,5 × ATR.
3. Objetivo a 2 R en lugar de salida por tiempo.
4. Sin la condición de MA99 creciente.
5. Pivotes k = 2 y k = 4.
6. Ventana de 15 y de 60 días después de la figura.
7. Costo doble (0,28%) para medir sensibilidad en altcoins ilíquidas.
8. Solo BTC y ETH, y solo ETH (descriptivas, sin potencia).
9. La lectura clásica: short al primer cierre bajo el cuello, mismo stop en
   ATR y mismo H.

Son 10 pruebas sobre la misma muestra: que alguna salga positiva por azar
es lo esperable.

## 8. Qué se hace con cada desenlace

- **C1 pasa y el placebo está limpio:** la tesis tiene respaldo offline. El
  agente puede citar su expectancy con su IC, como **validado offline**. La
  validación forward entra por la bitácora (E2).
- **C1 no pasa:** el agente sigue tratando el HCH y la MA99 como
  descripción, y lo dice al lado de cada plan que los use.
- **El placebo no da ≈ −costo:** hay un bug. No se reporta nada hasta
  encontrarlo.

Ningún desenlace convierte esto en una señal ni cambia la regla 1.

---

## Resultado (enlace al artefacto)

Corrido el 2026-10-22 con `uv run python -m marea.hs_study --until
2026-10-21` (runner `06066a4`). Artefacto:
`backend/artifacts/hch-ma99-20261022T205545Z.txt|json`.
**C1 no pasa**: −0,178 R por trade, IC95% [−0,350; +0,026], n = 485 en
139 semanas. Placebo −0,051 R [−0,176; +0,079], o sea ≈ −costo.
