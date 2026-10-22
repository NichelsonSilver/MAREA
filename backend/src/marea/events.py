# ruff: noqa: E501 — lineas de formato del reporte
"""Calendario de eventos — fase E3 del estratega.

    uv run python -m marea.events --days 14
    uv run python -m marea.events --from 2026-10-23 --days 7

Que un plan nombre lo que cae dentro de su horizonte sin depender de la
memoria de nadie. Tres fuentes, todas gratuitas y primarias:

- **Datos de EE.UU.** (CPI, empleo, PPI, PIB, PCE, JOLTS, ventas minoristas):
  fechas programadas de FRED (`/release/dates` con
  `include_release_dates_with_no_data`). FRED da la **fecha**, no la hora.
- **Reuniones del FOMC**: la página oficial de la Fed
  (`fomccalendars.htm`). La entrada es el día de la decisión, el segundo de
  la reunión; `*` marca las que traen proyecciones (SEP). El release 101 de
  FRED ("FOMC Press Release") se publica todos los días y no sirve para
  esto: medido el 22-10.
- **Vencimientos de opciones** de BTC y ETH en Deribit, a las 08:00 UTC, con
  el interés abierto en USD de la **última foto guardada** por la tarea
  `MAREA_snapshot_diario`. Se lee la DB en solo lectura.

No se persiste: un calendario se publica por adelantado y no tiene la
trampa de tiempo real que tienen las series. Lo que no cubre, y se dice:
el BCCh, eventos no programados y cualquier hora de publicación que la
fuente no traiga.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import zlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from marea.options import parse_instrument

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

#: release_id de FRED → nombre que se muestra. Elegidos por mover tasas y dólar.
FRED_RELEASES: dict[int, str] = {
    10: "CPI (inflación EE.UU.)",
    50: "Empleo EE.UU. (nóminas, desempleo)",
    46: "PPI (precios al productor)",
    53: "PIB EE.UU.",
    54: "PCE (ingreso y gasto personal)",
    192: "JOLTS (vacantes)",
    9: "Ventas minoristas EE.UU.",
}

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}


@dataclass(frozen=True)
class Event:
    day: date
    name: str
    source: str
    detail: str = ""
    #: Hora UTC si la fuente la da; None si solo hay fecha.
    at_utc: datetime | None = None
    #: Para vencimientos: fracción del OI total de la moneda.
    oi_share: float | None = None


# --------------------------------------------------------------------------- parseo (PURO)


def parse_fomc(html: str) -> list[Event]:
    """Reuniones programadas del FOMC, una por día de decisión.

    Las entradas sin rango de días (votos por notación, reuniones no
    programadas) se ignoran: no se anuncian por adelantado.
    """
    years = [(m.start(), int(m.group(1))) for m in re.finditer(r"(\d{4}) FOMC Meetings", html)]
    pat = re.compile(
        r'fomc-meeting__month[^>]*><strong>([^<]+)</strong></div>\s*<div class="fomc-meeting__date[^>]*>([^<]+)</div>'
    )
    out = []
    for m in pat.finditer(html):
        # El año es el de la sección más cercana hacia arriba: la página no
        # las ordena (2027 aparece al final, después de 2021).
        prior = [t for t in years if t[0] < m.start()]
        if not prior:
            continue
        year = max(prior)[1]
        months = [_MONTHS[x.strip()[:3].lower()] for x in m.group(1).split("/")]
        rng = re.fullmatch(r"\s*(\d{1,2})-(\d{1,2})(\*?)\s*", m.group(2))
        if not rng:
            continue
        month = months[-1]
        day = date(year, month, int(rng.group(2)))
        sep = rng.group(3) == "*"
        out.append(
            Event(
                day,
                "FOMC: decisión de tasas",
                "federalreserve.gov",
                "con proyecciones (SEP)" if sep else "",
            )
        )
    return sorted(out, key=lambda e: e.day)


def options_expiries(
    rows: Iterable[dict[str, Any]], currency: str, captured_at: datetime
) -> list[Event]:
    """Un evento por vencimiento vigente, con su OI en USD y su peso en el total."""
    oi: dict[datetime, float] = defaultdict(float)
    for row in rows:
        inst = parse_instrument(str(row["instrument_name"]))
        if inst.expiry <= captured_at:
            continue
        o, px = row.get("open_interest"), row.get("underlying_price")
        if isinstance(o, int | float) and isinstance(px, int | float):
            oi[inst.expiry] += float(o) * float(px)
    total = sum(oi.values())
    out = []
    for exp, usd in sorted(oi.items()):
        share = usd / total if total > 0 else 0.0
        detail = f"OI {usd / 1e9:,.2f} mil M USD ({share * 100:.0f}% del total), foto {captured_at:%Y-%m-%d %H:%M} UTC"
        out.append(
            Event(
                exp.date(),
                f"Vencimiento opciones {currency} (Deribit)",
                "deribit.com",
                detail,
                exp,
                share,
            )
        )
    return out


def fred_events(dates_by_release: Mapping[int, Sequence[str]]) -> list[Event]:
    return [
        Event(date.fromisoformat(d), FRED_RELEASES[rid], "FRED", f"release {rid}")
        for rid, ds in dates_by_release.items()
        for d in ds
    ]


def window(events: Iterable[Event], start: date, days: int) -> list[Event]:
    end = start + timedelta(days=days)
    return sorted((e for e in events if start <= e.day < end), key=lambda e: (e.day, e.name))


def drop_small_expiries(events: list[Event], min_share: float) -> tuple[list[Event], int]:
    """Saca los vencimientos con menos de `min_share` del OI; devuelve cuántos sacó."""
    keep = [e for e in events if e.oi_share is None or e.oi_share >= min_share]
    return keep, len(events) - len(keep)


def render(events: list[Event], start: date, days: int, notes: list[str]) -> str:
    end = start + timedelta(days=days - 1)
    lines = [f"CALENDARIO {start:%Y-%m-%d} → {end:%Y-%m-%d} ({days} d)", ""]
    if not events:
        lines.append("  sin eventos programados en la ventana")
    for e in events:
        when = f"{e.at_utc:%H:%M} UTC" if e.at_utc else "hora no informada"
        lines.append(f"  {e.day:%Y-%m-%d} {e.day:%a}  {e.name:42s} {when:18s} {e.detail}".rstrip())
    lines += ["", *[f"  nota: {n}" for n in notes]]
    lines.append("  no cubre: BCCh, eventos no programados, horas que la fuente no trae.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- I/O


def fetch_fred(start: date) -> dict[int, list[str]]:
    from marea.config import MareaSettings
    from marea.data.fred import FredClient

    key = MareaSettings().api_fred
    client = FredClient(key.get_secret_value() if key else "")
    try:
        out = {}
        for rid in FRED_RELEASES:
            body = client._get(
                "/release/dates",
                {
                    "release_id": rid,
                    "realtime_start": start.isoformat(),
                    "include_release_dates_with_no_data": "true",
                    "sort_order": "asc",
                    "limit": 100,
                },
            )
            out[rid] = [x["date"] for x in body["release_dates"]]
        return out
    finally:
        client.close()


def fetch_fomc_html() -> str:
    import httpx

    r = httpx.get(FOMC_URL, timeout=30, headers={"User-Agent": "MAREA research (uso personal)"})
    r.raise_for_status()
    return r.text


def load_option_rows(currency: str) -> tuple[list[dict[str, Any]], datetime] | None:
    """Filas de la última foto de opciones, leyendo la DB de MAREA en solo lectura."""
    from marea.config import DB_PATH

    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT captured_at, body_zlib FROM options_raw WHERE currency = ? ORDER BY captured_at DESC LIMIT 1",
            (currency,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not row:
        return None
    body = json.loads(zlib.decompress(row[1]))
    captured = datetime.fromisoformat(row[0])
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=UTC)
    return list(body.get("result", body if isinstance(body, list) else [])), captured


def collect(start: date) -> tuple[list[Event], list[str]]:
    events: list[Event] = []
    notes: list[str] = []
    try:
        events += fred_events(fetch_fred(start))
    except Exception as exc:  # noqa: BLE001 — una fuente caída no tumba el calendario
        notes.append(f"FRED no respondió ({exc}); faltan los datos de EE.UU.")
    try:
        fomc = parse_fomc(fetch_fomc_html())
        if not fomc:
            notes.append(
                "la página del FOMC no trajo reuniones: cambió el formato, revisar el parser"
            )
        events += fomc
    except Exception as exc:  # noqa: BLE001
        notes.append(f"federalreserve.gov no respondió ({exc}); faltan las reuniones del FOMC")
    for cur in ("BTC", "ETH"):
        got = load_option_rows(cur)
        if got is None:
            notes.append(f"sin foto de opciones de {cur} en la DB")
            continue
        rows, captured = got
        age = datetime.now(UTC) - captured
        if age > timedelta(days=2):
            notes.append(
                f"la foto de opciones de {cur} tiene {age.days} días: el OI puede estar viejo"
            )
        events += options_expiries(rows, cur, captured)
    return events, notes


def main(argv: list[str] | None = None) -> int:
    from bob.utils.console import enable_utf8_stdout

    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description="calendario de eventos para el horizonte de un plan")
    ap.add_argument("--from", dest="start", default=None, help="YYYY-MM-DD (default: hoy UTC)")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument(
        "--min-oi-share", type=float, default=0.05, help="vencimientos más chicos se omiten"
    )
    args = ap.parse_args(argv)
    start = date.fromisoformat(args.start) if args.start else datetime.now(UTC).date()
    events, notes = collect(start)
    shown, dropped = drop_small_expiries(window(events, start, args.days), args.min_oi_share)
    if dropped:
        notes.append(
            f"{dropped} vencimientos de opciones con menos del {args.min_oi_share * 100:.0f}% del OI omitidos"
        )
    print(render(shown, start, args.days, notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
