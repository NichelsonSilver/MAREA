---
name: estrategia
description: Evalúa una idea de trading o inversión que trae Nichelson (activo, entrada, figura, leverage) con todo lo que MAREA mide. Entrega factibilidad, stop, tamaño, leverage máximo, liquidación y ganancia por escenario. Nunca ejecuta órdenes.
---

# /estrategia

Agente: `.claude/agents/estratega.md`. Herramienta: `uv run python -m marea.tradeplan` (en `backend/`).

Delega en el agente `estratega` con la tesis de Nichelson ($ARGUMENTS). Si falta capital o riesgo por trade, usa 500 USD y 1%, y dilo.

Antes de entregar, revisa tres cosas. Que cada cifra tenga su comando de origen. Que ninguna figura chartista aparezca con probabilidad, porque no están medidas. Y que el leverage propuesto no supere el "máximo prudente" que da el comando. Si Nichelson pide guardar el plan, se registra en la bitácora (`uv run python -m marea.journal add`, fase E2). Se resuelve solo contra las velas, y `report` dice cómo le va a su criterio.
