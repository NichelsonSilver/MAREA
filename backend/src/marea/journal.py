# ruff: noqa: E501 — lineas de formato del reporte
"""Bitácora de planes — fase E2 del estratega.

    uv run python -m marea.journal add --file plan.json   # valida y agrega
    uv run python -m marea.journal decide P-20261022-0001 --taken si
    uv run python -m marea.journal report

Cada plan que sale de `/estrategia` se puede registrar acá **antes** de que
el mercado lo resuelva, y después se resuelve solo contra las velas
diarias. Es el paper tracking del criterio de Nichelson: la única forma de
saber con el tiempo si sus lecturas tienen ventaja, porque la E1 mostró que
la figura que traía no la tiene.

El registro es `bitacora/planes.jsonl` en la raíz del repo, versionado y
**solo se agrega**: un plan no se edita, se registra otro que lo reemplaza
(`supersedes`). La decisión de entrar o no (`decide`) es otra línea. Los
resultados **no se guardan**: se recalculan de las velas cada vez, así que
no hay nada que corregir a mano. El commit de git fecha cada línea.

Reglas de resolución, declaradas y conservadoras (las mismas de la E1):

- El plan empieza a correr en la **primera vela diaria que abre después de
  registrarlo**. Lo que el precio hizo antes, el mismo día, no cuenta: si
  contara, registrar a media tarde un límite que ya se tocó a la mañana
  sería un plan ganado a posteriori.
- `limite`: se llena si el precio llega a la entrada dentro de `valid_days`
  (a la apertura si abre más allá). `stop`: entrada por ruptura, igual pero
  del otro lado. `mercado`: a la apertura de la primera vela.
- Sale en el stop, en el **primer** objetivo o al cierre de la vela
  `horizon_days` contando la de entrada. Vela que toca stop y objetivo, o
  entrada y stop, cuenta como stop.
- R neto de 0,14% ida y vuelta y funding de 0,01% cada 8 h. Los USD son
  R × riesgo planeado (`capital × risk`).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from bob.models.projection import LeverageProfile, liquidation_price
from marea.hs_study import TradeRule, bootstrap_mean, iso_week, simulate

REGISTRY = Path(__file__).resolve().parent.parent.parent.parent / "bitacora" / "planes.jsonl"
MS_DAY = 86_400_000
#: Efecto que la bitácora debería poder detectar para decir algo: el mismo
#: umbral que la potencia de la E1.
MDE_R = 0.2

Direction = Literal["long", "short"]
Order = Literal["limite", "stop", "mercado"]


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["plan"] = "plan"
    id: str = ""
    registered_at_utc: str = ""
    symbol: str = Field(min_length=3)
    direction: Direction
    order: Order
    #: Para `mercado`, el precio de referencia al registrar (no se usa para llenar).
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    targets: list[float] = Field(default_factory=list)
    horizon_days: int = Field(ge=1, le=90)
    valid_days: int = Field(default=7, ge=1, le=60)
    capital: float = Field(gt=0)
    risk: float = Field(gt=0, le=0.10)
    leverage: float = Field(ge=1, le=125)
    thesis: str = Field(min_length=10, max_length=800)
    tags: list[str] = Field(default_factory=list)
    #: Comandos o artefactos que respaldan las cifras del plan.
    evidence: list[str] = Field(default_factory=list)
    supersedes: str | None = None

    @model_validator(mode="after")
    def _geometry(self) -> Plan:
        s = 1.0 if self.direction == "long" else -1.0
        if s * (self.entry - self.stop) <= 0:
            raise ValueError("el stop tiene que quedar del lado de la pérdida")
        if any(s * (t - self.entry) <= 0 for t in self.targets):
            raise ValueError("cada objetivo tiene que quedar del lado de la ganancia")
        self.targets = sorted(self.targets, reverse=self.direction == "short")
        self.symbol = self.symbol.upper()
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["decision"] = "decision"
    plan_id: str
    taken: bool
    note: str = ""
    registered_at_utc: str = ""


Line = Plan | Decision


def read(path: Path = REGISTRY) -> list[Line]:
    if not path.exists():
        return []
    out: list[Line] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        try:
            out.append(
                Decision.model_validate(raw)
                if raw.get("kind") == "decision"
                else Plan.model_validate(raw)
            )
        except ValidationError as exc:
            raise ValueError(f"{path.name}:{n} no cumple el esquema: {exc}") from exc
    return out


def _append(line: Line, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line.model_dump_json() + "\n")


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).replace(microsecond=0)


def add(raw: dict[str, Any], path: Path = REGISTRY, now: datetime | None = None) -> Plan:
    """Valida y agrega un plan. `id` y la hora los asigna el registro."""
    if raw.get("id") or raw.get("registered_at_utc"):
        raise ValueError("id y registered_at_utc los asigna el registro")
    existing = read(path)
    plan = Plan.model_validate(raw)
    ids = {p.id for p in existing if isinstance(p, Plan)}
    if plan.supersedes and plan.supersedes not in ids:
        raise ValueError(f"supersedes={plan.supersedes} no existe en la bitácora")
    ts = _now(now)
    plan.id = f"P-{ts:%Y%m%d}-{len(ids) + 1:04d}"
    plan.registered_at_utc = ts.isoformat()
    _append(plan, path)
    return plan


def decide(
    plan_id: str, taken: bool, note: str = "", path: Path = REGISTRY, now: datetime | None = None
) -> Decision:
    if plan_id not in {p.id for p in read(path) if isinstance(p, Plan)}:
        raise ValueError(f"{plan_id} no existe en la bitácora")
    d = Decision(plan_id=plan_id, taken=taken, note=note, registered_at_utc=_now(now).isoformat())
    _append(d, path)
    return d


# --------------------------------------------------------------------------- resolución (PURA)


@dataclass(frozen=True)
class Outcome:
    plan_id: str
    status: str  # pendiente | sin_llenar | abierto | cerrado | hueco
    fill_time: int | None = None
    fill: float | None = None
    exit: float | None = None
    exit_time: int | None = None
    reason: str | None = None  # stop | objetivo | tiempo
    r: float | None = None
    usd: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    liq_crossed: bool | None = None
    targets_hit: list[bool] = field(default_factory=list)


def _fill(
    plan: Plan, o: np.ndarray, h: np.ndarray, lo: np.ndarray, start: int
) -> tuple[int, float] | None | str:
    """(vela, precio) del llenado; None si la ventana venció; "pendiente" si sigue abierta."""
    n = len(o)
    if plan.order == "mercado":
        return (start, float(o[start])) if start < n else "pendiente"
    long = plan.direction == "long"
    for j in range(start, start + plan.valid_days):
        if j >= n:
            return "pendiente"
        e = plan.entry
        if plan.order == "limite":
            if long and lo[j] <= e:
                return j, float(min(o[j], e))
            if not long and h[j] >= e:
                return j, float(max(o[j], e))
        else:
            if long and h[j] >= e:
                return j, float(max(o[j], e))
            if not long and lo[j] <= e:
                return j, float(min(o[j], e))
    return None


def resolve(
    plan: Plan,
    open_time: np.ndarray,
    o: np.ndarray,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cost: float = 0.0014,
    funding_per_day: float = 0.0003,
) -> Outcome:
    reg_ms = int(datetime.fromisoformat(plan.registered_at_utc).timestamp() * 1000)
    start = int(np.searchsorted(open_time, reg_ms, side="right"))
    f = _fill(plan, o, h, lo, start)
    if f == "pendiente":
        return Outcome(plan.id, "pendiente")
    if f is None:
        return Outcome(plan.id, "sin_llenar")
    assert not isinstance(f, str)
    j, fill = f
    sign = 1.0 if plan.direction == "long" else -1.0
    risk = sign * (fill - plan.stop)
    end = min(j + plan.horizon_days, len(c))
    if np.any(np.diff(open_time[start:end]) != MS_DAY):
        return Outcome(plan.id, "hueco", int(open_time[j]), fill)
    if risk <= 0:  # abrió más allá del stop: se sale en el llenado
        res: tuple[float, int, str] | None = (fill, j, "stop")
    else:
        t1 = plan.targets[0] if plan.targets else None
        rule = TradeRule(
            horizon=plan.horizon_days,
            target_r=None if t1 is None else sign * (t1 - fill) / risk,
            max_hold=plan.horizon_days,
        )
        res = simulate(o, h, lo, c, j, fill, risk, plan.direction, rule)
    if res is None:
        return Outcome(plan.id, "abierto", int(open_time[j]), fill)
    exit_px, k, reason = res
    reason = {"target": "objetivo", "time": "tiempo"}.get(reason, reason)
    days = k - j + 1
    pnl = sign * (exit_px - fill) - fill * (cost + funding_per_day * days)
    risk_planned = sign * (plan.entry - plan.stop)
    r = pnl / (risk if risk > 0 else risk_planned)
    hi, lw = float(np.max(h[j : k + 1])), float(np.min(lo[j : k + 1]))
    fav, adv = (hi - fill, fill - lw) if sign > 0 else (fill - lw, hi - fill)
    rr = risk if risk > 0 else risk_planned
    # La vela de salida por stop no cuenta para objetivos: no se sabe qué tocó primero.
    upto = k if reason == "stop" else k + 1
    best = (np.max(h[j:upto]) if sign > 0 else np.min(lo[j:upto])) if upto > j else fill
    liq = liquidation_price(fill, plan.direction, LeverageProfile(leverage=plan.leverage))
    liq_hit = (lw <= liq) if sign > 0 else (hi >= liq)
    stop_before_liq = sign * (plan.stop - liq) > 0
    crossed = liq_hit and (not stop_before_liq or sign * (exit_px - liq) <= 0)
    return Outcome(
        plan.id,
        "cerrado",
        int(open_time[j]),
        fill,
        exit_px,
        int(open_time[k]),
        reason,
        r,
        r * plan.capital * plan.risk,
        fav / rr,
        adv / rr,
        bool(crossed),
        [bool(sign * (best - t) >= 0) for t in plan.targets],
    )


def live_plans(lines: list[Line]) -> list[Plan]:
    """Planes vigentes: los reemplazados por otro (`supersedes`) no cuentan."""
    plans = [x for x in lines if isinstance(x, Plan)]
    gone = {p.supersedes for p in plans if p.supersedes}
    return [p for p in plans if p.id not in gone]


def decisions(lines: list[Line]) -> dict[str, bool]:
    """La última decisión registrada por plan."""
    return {x.plan_id: x.taken for x in lines if isinstance(x, Decision)}


def aggregate(outcomes: list[Outcome]) -> dict[str, Any]:
    closed = [x for x in outcomes if x.status == "cerrado" and x.r is not None]
    out: dict[str, Any] = {"n_closed": len(closed)}
    if not closed:
        return out
    r = np.array([x.r for x in closed])
    w = iso_week(np.array([x.fill_time for x in closed], dtype=np.int64))
    out |= {
        "mean_r": float(r.mean()),
        "win_rate": float((r > 0).mean()),
        "weeks": int(np.unique(w).size),
    }
    if out["weeks"] > 1:
        out["ci95"] = bootstrap_mean(r, w)
    if len(r) >= 10:
        sd = float(r.std(ddof=1))
        out["n_needed_for_mde"] = int(np.ceil(((1.96 + 0.84) * sd / MDE_R) ** 2))
    return out


# --------------------------------------------------------------------------- I/O


def _fmt_day(ms: int | None) -> str:
    return "—" if ms is None else f"{datetime.fromtimestamp(ms / 1000, UTC):%Y-%m-%d}"


def render(rows: list[tuple[Plan, Outcome, bool | None]], last_bar: dict[str, int]) -> str:
    if not rows:
        return "bitácora vacía"
    lines = ["BITÁCORA DE PLANES (resuelta contra velas diarias; nada se guarda resuelto)", ""]
    for p, x, taken in rows:
        dec = {True: "entró", False: "no entró", None: "sin decisión"}[taken]
        head = f"{p.id}  {p.symbol} {p.direction} {p.order} {p.entry:,.2f}  stop {p.stop:,.2f}"
        if p.targets:
            head += "  obj " + " / ".join(f"{t:,.2f}" for t in p.targets)
        lines.append(f"{head}  H={p.horizon_days} d  [{dec}]")
        if x.status == "cerrado":
            assert (
                x.r is not None
                and x.usd is not None
                and x.mfe_r is not None
                and x.mae_r is not None
            )
            lines.append(
                f"    {x.status}: llenado {_fmt_day(x.fill_time)} a {x.fill:,.2f}, salida {_fmt_day(x.exit_time)} a {x.exit:,.2f} ({x.reason})  "
                f"{x.r:+.2f} R = {x.usd:+.2f} USD  MFE {x.mfe_r:+.2f} R  MAE {x.mae_r:.2f} R"
                + ("  ⚠ cruzó la liquidación" if x.liq_crossed else "")
            )
        else:
            lines.append(f"    {x.status} (última vela {_fmt_day(last_bar.get(p.symbol))})")
    agg = aggregate([x for _, x, _ in rows])
    taken_agg = aggregate([x for _, x, t in rows if t])
    lines += [
        "",
        f"TODOS LOS PLANES: {_agg_txt(agg)}",
        f"SOLO LOS QUE ENTRÓ: {_agg_txt(taken_agg)}",
    ]
    lines.append(
        "Sin ventaja, lo esperable es −costo por trade. Antes del n necesario, la media es descriptiva."
    )
    return "\n".join(lines)


def _agg_txt(a: dict[str, Any]) -> str:
    if not a["n_closed"]:
        return "ningún plan cerrado"
    txt = f"{a['n_closed']} cerrados, media {a['mean_r']:+.2f} R, ganan {a['win_rate'] * 100:.0f}%"
    if "ci95" in a:
        txt += f", IC95% por semanas [{a['ci95'][0]:+.2f}, {a['ci95'][1]:+.2f}]"
    if "n_needed_for_mde" in a:
        txt += f"; para detectar {MDE_R} R hacen falta ~{a['n_needed_for_mde']}"
    else:
        txt += "; menos de 10 cerrados, no se estima potencia"
    return txt


def report(path: Path = REGISTRY) -> str:
    from bob.data.store import load_series

    lines = read(path)
    plans, dec = live_plans(lines), decisions(lines)
    rows, last_bar = [], {}
    cache: dict[str, Any] = {}
    for p in plans:
        if p.symbol not in cache:
            cache[p.symbol] = load_series(p.symbol, "1d")
        s = cache[p.symbol]
        ot = np.asarray(s.open_time, dtype=np.int64)
        o, h, lo, c = (np.asarray(x, dtype=np.float64) for x in (s.open, s.high, s.low, s.close))
        if ot.size:
            last_bar[p.symbol] = int(ot[-1])
        rows.append((p, resolve(p, ot, o, h, lo, c), dec.get(p.id)))
    return render(rows, last_bar)


def main(argv: list[str] | None = None) -> int:
    from bob.utils.console import enable_utf8_stdout

    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description="bitácora de planes del estratega")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="lee UN plan JSON de stdin o --file")
    a.add_argument("--file", type=Path)
    d = sub.add_parser("decide", help="registra si Nichelson entró o no")
    d.add_argument("plan_id")
    d.add_argument("--taken", choices=("si", "no"), required=True)
    d.add_argument("--note", default="")
    sub.add_parser("report")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "add":
            text = args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()
            plan = add(json.loads(text))
            print(
                f"registrado {plan.id} ({plan.registered_at_utc}); corre desde la próxima vela diaria"
            )
        elif args.cmd == "decide":
            decide(args.plan_id, args.taken == "si", args.note)
            print(f"decisión registrada para {args.plan_id}")
        else:
            print(report())
    except (ValueError, ValidationError) as exc:
        print(f"rechazado: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
