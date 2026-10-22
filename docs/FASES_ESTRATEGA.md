# Fases del estratega (frente de decisión de trades)

Frente abierto el **2026-10-22** por pedido de Nichelson: un agente que tome
una idea concreta de trade o inversión y la evalúe con todo lo que MAREA
mide. No se mezcla con la fase M en curso ni toca la corrida forward
(regla 12). Al final de cada fase: resumir y esperar confirmación.

| fase | contenido | estado |
|---|---|---|
| **E0** | Agente `estratega`, skill `/estrategia` y `marea.tradeplan`: estructura diaria, tamaño por riesgo fijo, leverage máximo prudente, liquidación aislada y escenarios con el EV sin edge al lado. Primer caso real: long ETH a 2.300 sobre la MA99 tras un HCH de techo | ✅ 2026-10-22 (`02373f3`) |
| **E1** | **¿Funciona la tesis?** Medir, con pre-registro, el long en el toque de la MA99 creciente después de un HCH de techo, sobre los 659 perpetuos del panel diario (un solo símbolo no tiene potencia). Detector causal en `marea/patterns.py`. Pre-registro `docs/PREREGISTRO_HCH_MA99.md` | ✅ 2026-10-22: **no pasa**. −0,18 R por trade, IC95% [−0,35; +0,03], placebo limpio; peor que el toque de MA99 sin figura (−0,18 R, IC [−0,31; −0,05]). Pre-registro `0fea916`, runner `06066a4`, artefacto `hch-ma99-20261022T205545Z` |
| **E2** | **Bitácora de planes**: cada plan de `/estrategia` se registra solo-agregar (fecha, tesis, entrada, stop, objetivos, tamaño) y se resuelve después contra las velas, igual que el paper tracking. Es la única forma de medir con el tiempo si el criterio propio de Nichelson tiene ventaja | ✅ 2026-10-22: `marea/journal.py`, `bitacora/planes.jsonl`. Resuelve contra velas diarias desde la vela siguiente al registro; los resultados se recalculan, no se guardan |
| **E3** | **Calendario de eventos** como fuente: FOMC, CPI, empleo y vencimientos de opciones, para que el plan nombre lo que cae dentro del horizonte sin depender de la memoria | ✅ 2026-10-22: `marea/events.py`. FRED (fechas programadas, 7 publicaciones), página oficial del FOMC y vencimientos de Deribit con su OI de la última foto. Sin persistir; no cubre BCCh ni lo no programado |

## Lo que el frente no hace

- No ejecuta órdenes ni las recomienda (regla 1).
- No le pone probabilidad a una figura hasta que su fase de medición pase.
  Si la E1 no pasa, el HCH sigue como descripción y el agente lo dice.
- No cambia el tamaño por el leverage: el riesgo por trade decide el tamaño,
  el leverage solo el margen y la liquidación.
