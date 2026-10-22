# Fases del estratega (frente de decisión de trades)

Frente abierto el **2026-10-22** por pedido de Nichelson: un agente que tome
una idea concreta de trade o inversión y la evalúe con todo lo que MAREA
mide. No se mezcla con la fase M en curso ni toca la corrida forward
(regla 12). Al final de cada fase: resumir y esperar confirmación.

| fase | contenido | estado |
|---|---|---|
| **E0** | Agente `estratega`, skill `/estrategia` y `marea.tradeplan`: estructura diaria, tamaño por riesgo fijo, leverage máximo prudente, liquidación aislada y escenarios con el EV sin edge al lado. Primer caso real: long ETH a 2.300 sobre la MA99 tras un HCH de techo | ✅ 2026-10-22 (`02373f3`) |
| **E1** | **¿Funciona la tesis?** Medir, con pre-registro, el long en el toque de la MA99 creciente después de un HCH de techo, sobre los 659 perpetuos del panel diario (un solo símbolo no tiene potencia). Detector causal en `marea/patterns.py`. Pre-registro `docs/PREREGISTRO_HCH_MA99.md` | 🟡 detector y conteo de potencia hechos; pre-registro en borrador |
| **E2** | **Bitácora de planes**: cada plan de `/estrategia` se registra solo-agregar (fecha, tesis, entrada, stop, objetivos, tamaño) y se resuelve después contra las velas, igual que el paper tracking. Es la única forma de medir con el tiempo si el criterio propio de Nichelson tiene ventaja | pendiente |
| **E3** | **Calendario de eventos** como fuente: FOMC, CPI, empleo y vencimientos de opciones, para que el plan nombre lo que cae dentro del horizonte sin depender de la memoria | pendiente |

## Lo que el frente no hace

- No ejecuta órdenes ni las recomienda (regla 1).
- No le pone probabilidad a una figura hasta que su fase de medición pase.
  Si la E1 no pasa, el HCH sigue como descripción y el agente lo dice.
- No cambia el tamaño por el leverage: el riesgo por trade decide el tamaño,
  el leverage solo el margen y la liquidación.
