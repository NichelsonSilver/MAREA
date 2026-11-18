"""El mercado completo agrupado por función: hacia dónde rota el dinero. Descriptivo.

    uv run python -m marea.flow
    uv run python -m marea.flow --json

Une los dos universos que el proyecto mantiene separados —los 23 ETFs macro
(`ETFS`) y los 11 sectores de EE.UU. (`SECTOR_ETFS`)— más BTC y ETH, y los
agrupa por **la función que cumple el dinero** (refugio, defensivo, cíclico,
energía y materias primas...), no por clase de activo. Responde "¿salió el
dinero de lo especulativo hacia lo defensivo?", que la tabla por clase no ve:
salud y energía viven dentro de "acciones".

**Esto no toca la M3 ni el brief.** `ETFS` sigue siendo el universo
pre-registrado; los grupos viven acá, en código nuevo (regla 12).

Definiciones, todas declaradas porque cada una es una elección:

- **Retorno**: retorno total absoluto compuesto en la ventana, no relativo a
  una mediana. Mezclar sectores con SPY en una mediana contaría dos veces la
  bolsa de EE.UU. (los 11 sectores particionan el S&P 500).
- **SPY y QQQ** quedan como **referencias**, fuera de los grupos, por el mismo
  motivo: se solapan con los sectores.
- **Grupo**: promedio simple de sus miembros con datos completos en la
  ventana. Un miembro con un hueco queda fuera y se informa.
- **Cripto** se alinea al calendario de las sesiones de EE.UU.: para la sesión
  `D` se toma el cierre diario de Binance de la vela que abre `D` (00:00 UTC
  de `D+1`, unas 3 h después del cierre de Nueva York). Declarado.
- **Apetito por riesgo** = promedio de los grupos de riesgo (cíclico,
  crecimiento, internacional) menos el de los de defensa (refugio, bonos,
  defensivo). Su percentil es contra su **propio pasado estricto**, con
  ventanas que no se solapan con la actual. Cripto no entra: su historia es
  corta y cambiaría la composición del índice en el tiempo.

**No es flujo de dinero literal**: un precio que sube no prueba que haya
entrado un dólar (las creaciones de ETFs no están en ninguna fuente gratuita).
Dice a quién premió el mercado. **No es pronóstico**: la M3 midió que este tipo
de orden no se sostiene hacia adelante.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from marea.config import DB_PATH
from marea.data import store
from marea.data.catalog import ETF_BY_TICKER, SECTOR_BY_TICKER
from marea.returns import total_returns
from marea.strength import Panel, align

#: Ventanas, en sesiones de EE.UU.
WINDOWS: tuple[tuple[str, int], ...] = (
    ("1s", 5),
    ("4s", 20),
    ("13s", 65),
    ("26s", 130),
    ("52s", 252),
)

#: Grupo por función → miembros. Orden = de lo más defensivo a lo más especulativo.
GROUPS: dict[str, tuple[str, ...]] = {
    "refugio": ("UUP", "SHY"),
    "bonos": ("IEF", "TLT", "TIP"),
    "defensivo": ("XLV", "XLP", "XLU"),
    "credito": ("LQD", "HYG"),
    "inmobiliario": ("VNQ", "REET", "XLRE"),
    "metales": ("GLD", "SLV"),
    "energia_materias": ("USO", "UNG", "XLE", "CPER", "DBA"),
    "ciclico": ("XLF", "XLI", "XLB", "XLY", "IWM"),
    "crecimiento": ("XLK", "XLC"),
    "internacional": ("EFA", "EEM", "FXI", "EWJ", "ECH"),
    "cripto": ("BTC", "ETH"),
}
REFERENCES: tuple[str, ...] = ("SPY", "QQQ")
RISK_ON: tuple[str, ...] = ("ciclico", "crecimiento", "internacional")
RISK_OFF: tuple[str, ...] = ("refugio", "bonos", "defensivo")
CRYPTO: dict[str, str] = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

GROUP_OF: dict[str, str] = {t: g for g, ts in GROUPS.items() for t in ts}


def _r(v: float | None) -> float | None:
    return None if v is None or not math.isfinite(v) else round(float(v), 6)


def label_of(ticker: str) -> str:
    if ticker in ETF_BY_TICKER:
        return ETF_BY_TICKER[ticker].why
    if ticker in SECTOR_BY_TICKER:
        return SECTOR_BY_TICKER[ticker].why
    return {"BTC": "Bitcoin", "ETH": "Ether"}.get(ticker, ticker)


# --- Cálculo puro ------------------------------------------------------------


def window_returns(log_r: NDArray[np.float64], n: int) -> NDArray[np.float64]:
    """Retorno simple acumulado en las `n` sesiones que terminan en cada fecha.

    NaN si falta algún día de la ventana o si no hay `n` sesiones previas.
    """
    out = np.full(log_r.shape, np.nan)
    if log_r.size < n:
        return out
    finite = np.isfinite(log_r)
    x = np.where(finite, log_r, 0.0)
    cs = np.concatenate([[0.0], np.cumsum(x)])
    cnt = np.concatenate([[0], np.cumsum(finite)])
    s = cs[n:] - cs[:-n]
    full = (cnt[n:] - cnt[:-n]) == n
    out[n - 1 :] = np.where(full, np.expm1(s), np.nan)
    return out


def group_series(
    rets: Mapping[str, NDArray[np.float64]], groups: Mapping[str, Sequence[str]]
) -> dict[str, NDArray[np.float64]]:
    """Promedio simple por fecha de los miembros con dato; NaN si no hay ninguno."""
    out: dict[str, NDArray[np.float64]] = {}
    for g, members in groups.items():
        cols = [rets[t] for t in members if t in rets]
        if not cols:
            continue
        mat = np.vstack(cols)
        n = np.sum(np.isfinite(mat), axis=0)
        tot = np.nansum(mat, axis=0)
        out[g] = np.where(n > 0, tot / np.maximum(n, 1), np.nan)
    return out


def risk_appetite(groups: Mapping[str, NDArray[np.float64]]) -> NDArray[np.float64]:
    """Riesgo menos defensa; NaN donde falte algún grupo de los seis."""
    on = [groups[g] for g in RISK_ON if g in groups]
    off = [groups[g] for g in RISK_OFF if g in groups]
    if len(on) < len(RISK_ON) or len(off) < len(RISK_OFF):
        raise ValueError("faltan grupos para el apetito por riesgo")
    out: NDArray[np.float64] = np.mean(np.vstack(on), axis=0) - np.mean(np.vstack(off), axis=0)
    return out


def strict_percentile(series: NDArray[np.float64], n: int) -> tuple[float | None, int]:
    """Percentil del último valor contra el pasado que no solapa su ventana."""
    last = series[-1]
    past = series[: max(len(series) - n, 0)]
    past = past[np.isfinite(past)]
    if not math.isfinite(last) or past.size == 0:
        return None, int(past.size)
    return float(np.mean(past < last)), int(past.size)


def build(panel: Panel) -> dict[str, Any]:
    days, daily = panel
    if not days:
        raise ValueError("panel vacío")
    log_r = {t: np.log1p(r) for t, r in daily.items()}
    by_window: dict[str, dict[str, NDArray[np.float64]]] = {
        key: {t: window_returns(lr, n) for t, lr in log_r.items()} for key, n in WINDOWS
    }

    assets = []
    for t in [*(t for ts in GROUPS.values() for t in ts), *REFERENCES]:
        if t not in daily:
            continue
        row: dict[str, Any] = {
            "ticker": t,
            "label": label_of(t),
            "group": GROUP_OF.get(t),
            "reference": t in REFERENCES,
        }
        for key, _ in WINDOWS:
            row[f"ret_{key}"] = _r(by_window[key][t][-1])
        assets.append(row)

    group_now: dict[str, dict[str, Any]] = {}
    risk: dict[str, Any] = {}
    for key, n in WINDOWS:
        gs = group_series(by_window[key], GROUPS)
        for g in GROUPS:
            if g not in gs:
                continue
            members = [t for t in GROUPS[g] if t in daily]
            with_data = [t for t in members if math.isfinite(by_window[key][t][-1])]
            entry = group_now.setdefault(g, {"group": g, "members": members})
            entry[f"ret_{key}"] = _r(gs[g][-1])
            entry[f"n_{key}"] = len(with_data)
        ra = risk_appetite(gs)
        pct, n_past = strict_percentile(ra, n)
        risk[key] = {"score": _r(ra[-1]), "pct": _r(pct), "n_past": n_past}

    # Puesto de cada grupo en cada ventana (1 = el que más rindió).
    for key, _ in WINDOWS:
        ranked = sorted(
            (g for g in group_now if group_now[g].get(f"ret_{key}") is not None),
            key=lambda g: -group_now[g][f"ret_{key}"],
        )
        for i, g in enumerate(ranked, 1):
            group_now[g][f"rank_{key}"] = i

    groups_out = list(group_now.values())
    for row in groups_out:
        a, b = row.get("rank_4s"), row.get("rank_13s")
        # Positivo = subió en el orden: rinde mejor en 4 semanas que en 13.
        row["rotation"] = None if a is None or b is None else b - a

    lead = {
        key: sorted(
            (g for g in groups_out if g.get(f"rank_{key}") is not None),
            key=lambda g: g[f"rank_{key}"],
        )
        for key, _ in WINDOWS
    }
    return {
        "evidence": "medido (descriptivo: pasado, no pronóstico; precio, no flujo literal)",
        "as_of": days[-1].isoformat(),
        "windows": [k for k, _ in WINDOWS],
        "groups": groups_out,
        "assets": assets,
        "risk_appetite": risk,
        "risk_on": list(RISK_ON),
        "risk_off": list(RISK_OFF),
        "leaders": {k: [g["group"] for g in v[:3]] for k, v in lead.items()},
        "laggards": {k: [g["group"] for g in v[-3:][::-1]] for k, v in lead.items()},
        "method": dict(METHOD),
    }


METHOD: dict[str, str] = {
    "returns": "retorno total absoluto compuesto en la ventana (sesiones de EE.UU.)",
    "group": "promedio simple de los miembros con datos completos en la ventana",
    "references": "SPY y QQQ fuera de los grupos: se solapan con los 11 sectores",
    "crypto": "cierre diario de Binance de la vela que abre la sesión (00:00 UTC siguiente)",
    "risk_appetite": (
        "promedio de cíclico, crecimiento e internacional menos el de refugio, bonos y "
        "defensivo; percentil contra su propio pasado estricto sin solapar la ventana"
    ),
    "rotation": "puesto a 13 semanas menos puesto a 4: positivo = el grupo viene subiendo",
    "not_flow": "precio, no flujo literal: un precio que sube no prueba que haya entrado dinero",
}


# --- I/O ---------------------------------------------------------------------


def _etf_series(conn: sqlite3.Connection, t: str) -> tuple[list[date], NDArray[np.float64]] | None:
    bars = store.load_bars(conn, t)
    if len(bars) < 2:
        return None
    r = total_returns(
        np.array([b.close for b in bars]),
        np.array([b.div_cash for b in bars]),
        np.array([b.split_factor for b in bars]),
    )
    return [b.day for b in bars[1:]], r[1:]


def crypto_on_sessions(
    closes: Mapping[date, float], sessions: Sequence[date]
) -> NDArray[np.float64]:
    """Retornos de cripto entre sesiones consecutivas de EE.UU.; NaN sin cierre."""
    px = np.array([closes.get(d, np.nan) for d in sessions])
    out = np.full(px.shape, np.nan)
    out[1:] = px[1:] / px[:-1] - 1.0
    return out


def _crypto_closes(bob_db: Path, symbol: str) -> dict[date, float]:
    if not bob_db.exists():
        return {}
    conn = sqlite3.connect(f"file:{bob_db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "select open_time, close from candlerecord where symbol=? and timeframe='1d'",
            (symbol,),
        ).fetchall()
    finally:
        conn.close()
    return {datetime.fromtimestamp(ot / 1000, tz=UTC).date(): float(c) for ot, c in rows}


def load_panel(conn: sqlite3.Connection, bob_db: Path | None) -> Panel:
    series: dict[str, tuple[Sequence[date], NDArray[np.float64]]] = {}
    for t in [*(t for ts in GROUPS.values() for t in ts), *REFERENCES]:
        if t in CRYPTO:
            continue
        s = _etf_series(conn, t)
        if s is not None:
            series[t] = s
    days, rets = align(series)
    if bob_db is not None:
        for t, sym in CRYPTO.items():
            closes = _crypto_closes(bob_db, sym)
            if closes:
                rets[t] = crypto_on_sessions(closes, days)
    return days, rets


def from_db(conn: sqlite3.Connection, bob_db: Path | None) -> dict[str, Any]:
    return build(load_panel(conn, bob_db))


def rarity(daily: NDArray[np.float64]) -> list[dict[str, Any]]:
    """Qué tan raro es el movimiento reciente de UN activo contra su propia historia.

    Por ventana: retorno, percentil contra el pasado estricto que no solapa la
    ventana, y z = retorno log sobre la volatilidad diaria del año previo a la
    ventana escalada por raíz de n. Descriptivo: no dice nada del futuro.
    """
    lr = np.log1p(daily)
    out = []
    for key, n in WINDOWS:
        w = window_returns(lr, n)
        pct, n_past = strict_percentile(w, n)
        prev = lr[-n - 252 : -n] if lr.size > n + 252 else lr[: max(lr.size - n, 0)]
        prev = prev[np.isfinite(prev)]
        vol = float(np.std(prev)) * math.sqrt(n) if prev.size > 20 else float("nan")
        last = w[-1]
        z = math.log1p(last) / vol if math.isfinite(last) and vol > 0 else float("nan")
        out.append({"window": key, "ret": _r(last), "pct": _r(pct), "n_past": n_past, "z": _r(z)})
    return out


def _pct(v: float | None) -> str:
    return f"{'—':>8}" if v is None else f"{100 * v:>+7.1f}%"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m marea.flow", description=__doc__)
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--rareza",
        metavar="TICKER",
        help="qué tan raro es el movimiento de un activo contra su propia historia",
    )
    args = p.parse_args(argv)
    conn = store.connect(DB_PATH)
    try:
        if args.rareza:
            days, rets = load_panel(conn, DB_PATH.parent / "bob.db")
            t = args.rareza.upper()
            if t not in rets:
                print(f"{t} no está en el universo: {sorted(rets)}")
                return 2
            first = next(d for d, v in zip(days, rets[t], strict=True) if math.isfinite(v))
            print(f"RAREZA de {t} · al {days[-1]} · historia desde {first} · descriptivo")
            for r in rarity(rets[t]):
                pct = "—" if r["pct"] is None else f"{r['pct']:.3f}"
                z = "—" if r["z"] is None else f"{r['z']:+.2f}"
                print(f"  {r['window']:>4}: {_pct(r['ret'])}  pct {pct} (n={r['n_past']})  z {z}")
            return 0
        rep = from_db(conn, DB_PATH.parent / "bob.db")
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    ws = rep["windows"]
    print(f"MERCADO COMPLETO POR FUNCIÓN · al {rep['as_of']} · {rep['evidence']}")
    print(f"{'grupo':<18}" + "".join(f"{w:>9}" for w in ws) + "   rotación")
    for g in sorted(rep["groups"], key=lambda g: g.get("rank_13s", 99)):
        rot = g["rotation"]
        print(
            f"{g['group']:<18}"
            + "".join(_pct(g.get(f"ret_{w}")) + " " for w in ws)
            + ("" if rot is None else f"  {rot:+d}")
        )
    print("\nApetito por riesgo (riesgo - defensa) y su percentil histórico:")
    for w in ws:
        ra = rep["risk_appetite"][w]
        pct = "—" if ra["pct"] is None else f"{ra['pct']:.2f}"
        print(f"  {w:>4}: {_pct(ra['score'])}  pct {pct} (n={ra['n_past']})")
    print("\nPor activo (13s):")
    for a in sorted(rep["assets"], key=lambda a: -(a["ret_13s"] or -9)):
        print(
            f"  {a['ticker']:<5}{(a['group'] or 'referencia'):<18}"
            + "".join(_pct(a.get(f"ret_{w}")) + " " for w in ws)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
