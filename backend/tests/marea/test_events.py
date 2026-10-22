# ruff: noqa: E501 — HTML de muestra
from datetime import UTC, date, datetime

import pytest

from marea.events import (
    Event,
    drop_small_expiries,
    fred_events,
    options_expiries,
    parse_fomc,
    render,
    window,
)


def _meeting(month: str, days: str) -> str:
    return (
        f'<div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>{month}</strong></div>\n'
        f'    <div class="fomc-meeting__date col-xs-4">{days}</div></div>'
    )


# Recorte con la forma de federalreserve.gov/monetarypolicy/fomccalendars.htm
# (22-10-2026): 2026 arriba, años viejos, y 2027 al final.
HTML = "\n".join(
    [
        "<h4>2026 FOMC Meetings</h4>",
        _meeting("October", "27-28"),
        _meeting("December", "8-9*"),
        "<h4>2025 FOMC Meetings</h4>",
        _meeting("August", "22 (notation vote)"),
        "<h4>2024 FOMC Meetings</h4>",
        _meeting("Apr/May", "30-1"),
        "<h4>2027 FOMC Meetings</h4>",
        _meeting("January", "26-27"),
    ]
)


def test_fomc_dia_de_decision_anio_por_seccion_y_cruce_de_mes():
    ev = parse_fomc(HTML)
    assert [e.day for e in ev] == [
        date(2024, 5, 1),
        date(2026, 10, 28),
        date(2026, 12, 9),
        date(2027, 1, 27),
    ]
    assert ev[2].detail == "con proyecciones (SEP)" and ev[1].detail == ""


def test_fomc_pagina_sin_reuniones_da_lista_vacia():
    assert parse_fomc("<html>otra cosa</html>") == []


def test_vencimientos_suman_oi_en_usd_e_ignoran_los_vencidos():
    now = datetime(2026, 10, 22, 19, tzinfo=UTC)
    rows = [
        {
            "instrument_name": "ETH-30OCT26-2500-C",
            "open_interest": 100.0,
            "underlying_price": 2400.0,
        },
        {
            "instrument_name": "ETH-30OCT26-2000-P",
            "open_interest": 50.0,
            "underlying_price": 2400.0,
        },
        {
            "instrument_name": "ETH-23OCT26-2400-C",
            "open_interest": 50.0,
            "underlying_price": 2400.0,
        },
        {
            "instrument_name": "ETH-22OCT26-2400-C",
            "open_interest": 999.0,
            "underlying_price": 2400.0,
        },
        {
            "instrument_name": "ETH-23OCT26-2600-C",
            "open_interest": None,
            "underlying_price": 2400.0,
        },
    ]
    ev = options_expiries(rows, "ETH", now)
    assert [e.day for e in ev] == [date(2026, 10, 23), date(2026, 10, 30)]
    assert ev[1].oi_share == pytest.approx(0.75)
    assert ev[1].at_utc == datetime(2026, 10, 30, 8, tzinfo=UTC)
    keep, dropped = drop_small_expiries(ev, 0.5)
    assert [e.day for e in keep] == [date(2026, 10, 30)] and dropped == 1


def test_ventana_semiabierta_y_orden():
    evs = fred_events({10: ["2026-11-10"], 50: ["2026-11-06", "2026-10-02"]})
    got = window(evs, date(2026, 11, 6), 4)
    assert [(e.day, e.detail) for e in got] == [(date(2026, 11, 6), "release 50")]
    assert window(evs, date(2026, 11, 6), 5)[-1].day == date(2026, 11, 10)


def test_render_dice_lo_que_no_cubre():
    txt = render(
        [Event(date(2026, 10, 28), "FOMC: decisión de tasas", "fed")], date(2026, 10, 23), 7, ["x"]
    )
    assert "hora no informada" in txt and "no cubre" in txt and "nota: x" in txt
    assert "sin eventos" in render([], date(2026, 10, 23), 7, [])
