"""Plan de un trade que propone Nichelson: estructura, tamaño y riesgo. PURO + CLI.

Es la herramienta del agente `estratega`. No busca entradas ni dice hacia
dónde va el precio: toma la entrada, el stop y los objetivos que **trae el
usuario** y devuelve las cifras con las que se decide.

- **Estructura diaria descriptiva**: medias simples 7/25/99 (las de Binance
  por defecto), ATR 14, volatilidad realizada, rangos y pivotes confirmados.
  Nada de esto tiene ventaja medida. Los pivotes son los de
  `bob.signals.technical.swing_points`, y se listan solo los ya confirmados:
  un pivote en `j` se conoce recién en `j + k`.
- **Tamaño por riesgo fijo** con `bob.models.sizing.size_position`, uno por
  leverage pedido. El leverage no cambia lo que se pierde al stop, solo el
  margen y la distancia a liquidación.
- **El ancla honesta**: sin edge direccional (gate de la Fase 4, reglas
  técnicas del 05-10) el EV esperado de un trade es −costo, sea cual sea el
  R:R. Se imprime al lado de cada objetivo.
- La sigma del horizonte usa la **mayor** de las volatilidades de 30 y 90
  días, escalada por √H. Es la elección conservadora, y la `P(toque)` que sale
  de ahí es la aproximación de `prob_touch` (camino sin deriva), calibrada en
  15m a 4 h (Fase 5-bis, stop) y **no** medida en diario.

    uv run python -m marea.tradeplan --entry 2300 --stop 2190 --target 2470
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np

from bob.models.sizing import Sizing, size_position
from bob.signals.technical import swing_points

ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts"
MA_WINDOWS = (7, 25, 99)
DAY_MS = 86_400_000


@dataclass(frozen=True)
class Pivot:
    day: date
    kind: str  # "alto" o "bajo"
    price: float


@dataclass(frozen=True)
class Structure:
    last_day: date
    last_close: float
    ma: dict[int, float]
    ma99_slope_10d: float  # cambio relativo de la MA99 en 10 días
    atr14: float
    vol30: float  # desvío de retornos log diarios
    vol90: float
    range60: tuple[float, float]
    range180: tuple[float, float]
    pivots: list[Pivot] = field(default_factory=list)


def _day(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, UTC).date()


def daily_structure(
    open_time: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    k: int = 5,
    lookback: int = 120,
) -> Structure:
    """Lectura descriptiva de velas diarias **cerradas**."""
    n = close.size
    if n < max(MA_WINDOWS) + 11:
        raise ValueError(f"hacen falta al menos {max(MA_WINDOWS) + 11} velas diarias")
    ma = {w: float(close[-w:].mean()) for w in MA_WINDOWS}
    ma99_prev = float(close[-109:-10].mean())
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    r = np.diff(np.log(close))
    is_high, is_low = swing_points(high, low, k)
    pivots: list[Pivot] = []
    # El último índice confirmado es n − 1 − k: después de él faltan velas.
    for j in range(max(0, n - lookback), n - k):
        if is_high[j]:
            pivots.append(Pivot(_day(int(open_time[j])), "alto", float(high[j])))
        if is_low[j]:
            pivots.append(Pivot(_day(int(open_time[j])), "bajo", float(low[j])))
    return Structure(
        last_day=_day(int(open_time[-1])),
        last_close=float(close[-1]),
        ma=ma,
        ma99_slope_10d=ma[99] / ma99_prev - 1.0,
        atr14=float(tr[-14:].mean()),
        vol30=float(r[-30:].std(ddof=1)),
        vol90=float(r[-90:].std(ddof=1)),
        range60=(float(low[-60:].min()), float(high[-60:].max())),
        range180=(float(low[-180:].min()), float(high[-180:].max())),
        pivots=pivots,
    )


def horizon_sigma(s: Structure, horizon_days: float) -> float:
    """Sigma del horizonte, con la mayor de las dos volatilidades (conservadora)."""
    return max(s.vol30, s.vol90) * math.sqrt(horizon_days)


@dataclass(frozen=True)
class TargetRead:
    target: float
    target_pct: float
    reward_risk_net: float
    breakeven_win_rate: float
    p_touch_target: float | None
    ev_no_edge_usd: float  # −costo: lo que se espera sin ventaja direccional


@dataclass(frozen=True)
class Plan:
    mark: float
    entry: float
    stop: float
    horizon_days: float
    sigma_horizon: float
    sizings: list[Sizing]
    targets: list[TargetRead]


def build_plan(
    s: Structure,
    *,
    mark: float,
    entry: float,
    stop: float,
    targets: list[float],
    capital: float,
    risk: float,
    leverages: list[float],
    horizon_days: float,
    maker_entry: bool,
) -> Plan:
    sigma = horizon_sigma(s, horizon_days)
    first = targets[0] if targets else None
    sizings = [
        size_position(
            capital,
            risk,
            entry,
            stop,
            lev,
            target=first,
            maker_entry=maker_entry,
            sigma_horizon=sigma,
        )
        for lev in leverages
    ]
    reads: list[TargetRead] = []
    for t in targets:
        z = size_position(
            capital,
            risk,
            entry,
            stop,
            leverages[0],
            target=t,
            maker_entry=maker_entry,
            sigma_horizon=sigma,
        )
        assert z.target_pct is not None and z.reward_risk_net is not None
        assert z.breakeven_win_rate is not None
        reads.append(
            TargetRead(
                t,
                z.target_pct,
                z.reward_risk_net,
                z.breakeven_win_rate,
                z.p_touch_target,
                -z.cost_usd,
            )
        )
    return Plan(mark, entry, stop, horizon_days, sigma, sizings, reads)


# --------------------------------------------------------------------------- #
# Evidencia del repo (leída de artefactos, no escrita a mano)
# --------------------------------------------------------------------------- #
def technical_evidence(artifacts: Path = ARTIFACTS) -> list[str]:
    paths = sorted(glob.glob(str(artifacts / "technical-ETHUSDT-*.json")))
    if not paths:
        return ["reglas técnicas: sin artefacto"]
    j = json.loads(Path(paths[-1]).read_text(encoding="utf-8"))
    out = [f"reglas técnicas pre-registradas ({Path(paths[-1]).stem}):"]
    for c in j["confirmatory"]:
        st = c["stats"]
        out.append(
            f"  {c['rule']:<12} {c['timeframe']:<4} n={st['n']:<5} "
            f"expectancy {st['mean_r']:+.2f} R [{st['ci_low']:+.2f}, {st['ci_high']:+.2f}]"
            f"  {c['verdict']}"
        )
    return out


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def _p(x: float) -> str:
    return f"{x * 100:+.2f}%"


def render(
    s: Structure, plan: Plan, funding: float | None, stale_mark: bool, symbol: str = "ETHUSDT"
) -> str:
    L: list[str] = []
    side = "long" if plan.stop < plan.entry else "short"
    L.append(f"PLAN DE TRADE — {symbol} {side} · entrada {plan.entry:,.2f} · stop {plan.stop:,.2f}")
    L.append("")
    L.append("1. MERCADO AHORA — medido, descriptivo")
    tag = "  ⚠ REST falló: es el último cierre" if stale_mark else ""
    L.append(
        f"  precio marca {plan.mark:,.2f}{tag}; "
        f"la entrada está a {_p(plan.entry / plan.mark - 1)} del precio"
    )
    if funding is not None:
        L.append(
            f"  funding vigente {funding * 100:+.4f}% por 8 h "
            f"({funding * 3 * 365 * 100:+.1f}% anualizado)"
        )
    L.append(f"  último cierre diario {s.last_day}: {s.last_close:,.2f}")
    L.append(
        "  medias simples: "
        + "  ".join(f"MA{w} {v:,.1f}" for w, v in s.ma.items())
        + f"  (MA99 {_p(s.ma99_slope_10d)} en 10 d)"
    )
    L.append(
        f"  ATR14 {s.atr14:,.1f} ({s.atr14 / s.last_close * 100:.2f}% del cierre); "
        f"vol diaria 30 d {s.vol30 * 100:.2f}%, 90 d {s.vol90 * 100:.2f}%"
    )
    L.append(
        f"  rango 60 d {s.range60[0]:,.0f}–{s.range60[1]:,.0f}; "
        f"180 d {s.range180[0]:,.0f}–{s.range180[1]:,.0f}"
    )
    L.append("  pivotes confirmados (k=5):")
    for p in s.pivots:
        L.append(f"    {p.day}  {p.kind:<4} {p.price:,.2f}")
    L.append("")
    L.append(
        f"2. TAMAÑO POR RIESGO FIJO — horizonte {plan.horizon_days:g} d, "
        f"sigma {plan.sigma_horizon * 100:.2f}%"
    )
    z0 = plan.sizings[0]
    L.append(
        f"  cuenta {z0.capital:,.0f} USD · riesgo {z0.risk_fraction * 100:.1f}% "
        f"= {z0.risk_budget:,.2f} USD"
        f" · stop a {z0.stop_pct * 100:.2f}% ({z0.stop_sigmas:.2f}σ)"
    )
    L.append(
        f"  cantidad {z0.quantity:.3f} ETH · notional {z0.notional:,.2f} USD · pérdida al stop "
        f"{z0.loss_at_stop:,.2f} USD (costos {z0.cost_usd:,.2f} incluidos)"
    )
    if z0.p_touch_stop is not None:
        L.append(
            f"  P(el ruido toca el stop en {plan.horizon_days:g} d) "
            f"≈ {z0.p_touch_stop * 100:.0f}%  (aprox. sin deriva)"
        )
    L.append(
        f"  leverage mínimo para abrirlo con toda la cuenta {z0.required_leverage:.2f}x · "
        f"máximo prudente {z0.max_safe_leverage:.1f}x (liquidación ≥ 1,5× el stop)"
    )
    L.append("  leverage   margen     liquidación   dist.   liq/stop   avisos")
    for z in plan.sizings:
        warn = "; ".join(w.code for w in z.warnings) or "—"
        L.append(
            f"  {z.leverage:>5.0f}x  {z.margin:>9,.2f}  {z.liquidation_price:>11,.2f}  "
            f"{z.liquidation_pct * 100:>5.1f}%  {z.liquidation_to_stop:>7.2f}×   {warn}"
        )
    L.append(
        "  rachas de pérdidas al riesgo elegido: "
        + "  ".join(f"{n} → −{v * 100:.1f}%" for n, v in z0.losing_streaks.items())
    )
    L.append("")
    L.append("3. OBJETIVOS — escenario, no pronóstico")
    for t in plan.targets:
        pt = f"P(toque) ≈ {t.p_touch_target * 100:.0f}%" if t.p_touch_target is not None else ""
        L.append(
            f"  {t.target:,.2f} ({_p(t.target_pct)}): R neto {t.reward_risk_net:.2f} · "
            f"aciertos para empatar {t.breakeven_win_rate * 100:.1f}% · {pt}"
        )
    if plan.targets:
        L.append(
            f"  sin edge direccional el EV esperado es el costo: "
            f"{plan.targets[0].ev_no_edge_usd:+.2f} USD por trade,"
            " sea cual sea el R:R"
        )
    L.append("")
    L.append("4. EVIDENCIA DEL REPO")
    L.append("  dirección 15m: no pasó el gate (Fase 4) → sin edge direccional medido")
    L.extend("  " + x for x in technical_evidence())
    L.append("  figuras chartistas (HCH, medias como soporte): NO medidas en este repo")
    L.append("")
    L.append("Nunca ejecuta órdenes. Decide Nichelson.")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI (I/O)
# --------------------------------------------------------------------------- #
async def _live(symbol: str) -> tuple[float, float]:
    from bob.data.binance_rest import BinanceRestClient

    async with BinanceRestClient() as b:
        mp = await b.mark_price(symbol)
    return float(mp["markPrice"]), float(mp["lastFundingRate"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="marea.tradeplan", description=__doc__.splitlines()[0])
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--entry", type=float, required=True)
    ap.add_argument("--stop", type=float, required=True)
    ap.add_argument("--target", type=float, action="append", default=[])
    ap.add_argument("--capital", type=float, default=500.0)
    ap.add_argument("--risk", type=float, default=0.01, help="fracción de la cuenta (0.01 = 1%%)")
    ap.add_argument("--leverage", default="3,5,10,20", help="lista separada por comas")
    ap.add_argument("--horizon-days", type=float, default=5.0)
    ap.add_argument("--maker", action="store_true", help="entrada con orden límite")
    a = ap.parse_args(argv)

    from bob.data.store import load_series

    ser = load_series(a.symbol, "1d")
    s = daily_structure(ser.open_time, ser.high, ser.low, ser.close)
    age = (datetime.now(UTC).date() - s.last_day).days
    try:
        mark, funding = asyncio.run(_live(a.symbol))
        stale = False
    except Exception:  # noqa: BLE001 — sin red se sigue con el último cierre, declarado
        mark, funding, stale = s.last_close, None, True
    plan = build_plan(
        s,
        mark=mark,
        entry=a.entry,
        stop=a.stop,
        targets=a.target,
        capital=a.capital,
        risk=a.risk,
        leverages=[float(x) for x in a.leverage.split(",")],
        horizon_days=a.horizon_days,
        maker_entry=a.maker,
    )
    out = render(s, plan, funding, stale, a.symbol)
    if age > 2:
        out = f"⚠ la última vela diaria tiene {age} días: correr la ingesta del panel\n\n" + out
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
