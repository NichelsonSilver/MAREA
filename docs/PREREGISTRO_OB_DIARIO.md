# Pre-registro — Order block alcista en velas diarias, panel completo

> **Fijado el 2026-10-22**, con las dos decisiones abiertas tomadas por
> Nichelson (horizonte de 10 días, solo alcistas), antes de calcular un solo
> resultado. Hasta este commit solo existe el conteo de eventos de la §6, que
> no mira retornos. Después de correr, este archivo no se edita salvo para
> enlazar el artefacto.

---

## 1. Qué se pregunta y por qué

Nichelson ve en ETH 1D un order block bajo el HCH: la vela del 19-08-2026
(+17,5%) crea, con la definición del repo, una zona de demanda en la última
vela bajista previa (16-08, 1.868–1.892), que sigue sin tocarse al 21-10.
Pregunta si "es real".

Que la zona **existe** ya está contestado: el detector la reconoce. Lo que
no se sabe es si **comprar el primer retorno** a una zona así gana. La regla
se midió el 05-10 en ETH a 15m y 1h y **no pasó**
(`PREREGISTRO_TECNICO.md`); en diario no se midió nunca. Un solo símbolo no
tiene potencia (ETH: 39 toques alcistas desde 2019), así que se mide en el
panel, como la E1.

## 2. Hipótesis

- **H1:** la expectancy neta por trade del order block alcista diario, en R,
  es > 0.
- **H0:** es ≤ 0. Sin información, la neta es −costo/riesgo por álgebra.

## 3. Definiciones fijadas

### 3.1 Universo y muestra

Igual que la E1 (`PREREGISTRO_HCH_MA99.md` §3.1): los 659 perpetuos de
`universe-1d.json` con deslistados, elegibilidad de
`bob.signals.universe.eligibility_mask` sin cambios, velas hasta el cierre
del **2026-10-21**, símbolos con ≥ 200 velas. El trade cuenta si el símbolo
es elegible en la vela del toque.

### 3.2 La zona (`bob.signals.technical.scan`, defaults, sin cambios)

Es **la misma función** que dibuja el Mapa y que midió el 05-10:

- pivotes con k = 3, que se conocen al cierre de `j + 3`;
- desplazamiento alcista: cuerpo ≥ 1,0 × ATR14 (Wilder) de la vela
  anterior, y primer cierre sobre el último pivote alto confirmado;
- la zona es el rango de la última vela bajista entre `i − 5` e `i − 1`;
- vive 96 velas; muere si una vela abre debajo de ella o al primer toque.

### 3.3 El trade (`bob.models.technical_rules.order_block_trades`)

- **Entrada**: límite en el borde superior, llenada en la vela del toque; a
  la apertura si abre adentro de la zona.
- **Stop**: borde inferior − 0,1 × ATR (el ATR de la creación de la zona).
- **Objetivo**: **2 R**. **Salida por tiempo**: cierre de la vela 10
  contando la del toque (**H = 10 días**).
- **Costo**: 0,14% ida y vuelta + funding 0,03% por día abierto (0,01% cada
  8 h). Piso de riesgo: stop a ≥ 0,28%.
- Convenciones del 05-10, todas en contra de la regla: en la vela del
  llenado solo cuenta el stop; vela que toca stop y objetivo es stop; un gap
  a través del stop sale a la apertura; un gap a través del objetivo no paga
  el gap.
- **R** = retorno neto / riesgo, ambos en % de la entrada.

### 3.4 Inferencia

Igual que la E1: bootstrap por **semanas ISO en UTC** de la vela de
entrada, 10.000 remuestreos, semilla 20261022, IC por percentiles.

## 4. Prueba confirmatoria (una)

**C1.** Expectancy media en R de los order blocks **alcistas**, con IC95%
por bloques de semanas. **Pasa si el límite inferior es > 0.**

## 5. Comparaciones obligatorias (no deciden, se reportan siempre)

- **Placebo**: cada trade se vuelve a sortear en un día al azar elegible del
  mismo símbolo, con su misma dirección y su mismo riesgo en ATR, entrando a
  la apertura del día siguiente (la convención de `placebo_trades` del
  05-10), semilla fija. Debe dar ≈ −costo/riesgo. Si no, la plomería fabrica
  resultado y nada se lee.
- **Order blocks bajistas**, con la misma regla espejada.
- **Liquidación a 10x aislado**: fracción de trades con el stop detrás de la
  liquidación, y fracción donde la pérdida se realizó más allá de ella.
- Por año, peor trade, racha de pérdidas, % de objetivos, stops y tiempo, y
  riesgo mediano en %.
- ETH solo, descriptivo.

## 6. Potencia, medida antes de fijar el criterio (solo conteos)

Toques elegibles con el piso de riesgo cumplido, sin simular resultados:

| | toques | semanas distintas | riesgo mediano | p90 |
|---|---|---|---|---|
| **alcistas** | **4.735** | **312** | 7,86% | 14,75% |
| bajistas | 3.223 | 302 | 7,10% | 14,69% |

Alcistas por año: 2020: 84 · 2021: 478 · 2022: 517 · 2023: 706 · 2024: 1.162
· 2025: 1.014 · 2026: 774.

Con 312 semanas y un desvío supuesto de ~0,9 R por semana (supuesto, el de la
E1), el efecto mínimo detectable es de **~0,14 R** por trade (α = 0,025 a una
cola, potencia 80%). En ETH hay 39 toques alcistas: **sin potencia**,
descriptivo.

El riesgo mediano de 7,9% ya dice algo sin mirar resultados: a 10x la
liquidación aislada queda a −9,5%, así que en una parte grande de la muestra
llega antes que el stop. La §5 lo cuantifica.

## 7. Exploratorias declaradas (no deciden)

1. H = 5 y H = 48 días (48 = la regla del 05-10 en velas).
2. Objetivo a 1 R y a 3 R.
3. Costo doble (0,28%).
4. Solo con la MA99 a favor: cierre previo sobre la MA99 de los 99 cierres
   anteriores y MA99 creciente en 10 días (la idea de la E1).
5. Solo BTC y ETH (descriptiva).

Son 7 pruebas sobre la misma muestra: que alguna salga positiva por azar es
lo esperable.

## 8. Qué se hace con cada desenlace

- **C1 pasa y el placebo está limpio:** el order block diario tiene respaldo
  **offline**. El Mapa y el estratega pueden citar su expectancy con su IC,
  etiquetada "validada offline, sin forward"; la forward entra por la
  bitácora (E2).
- **C1 no pasa:** las zonas se siguen dibujando como mapa, y al lado va la
  expectancy medida.
- **El placebo no da ≈ −costo:** hay un bug; no se reporta nada hasta
  encontrarlo.

Ningún desenlace convierte esto en una señal ni cambia la regla 1.

---

## Resultado (enlace al artefacto)

Corrido el 2026-10-22 con `uv run python -m marea.ob_study --until
2026-10-21` (runner `390ddb7`). Artefacto:
`backend/artifacts/ob-diario-20261022T224956Z.txt|json`.
**C1 no pasa**: −0,062 R por trade, IC95% [−0,164; +0,048], n = 4.711 en
312 semanas. Placebo −0,088 R [−0,166; −0,012], con el costo solo
(−0,038 R) adentro: la plomería está limpia.
