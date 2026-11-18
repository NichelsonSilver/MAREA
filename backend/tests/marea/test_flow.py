from datetime import date, timedelta

import numpy as np
import pytest

from marea import flow


def test_window_returns_compone_y_exige_ventana_completa() -> None:
    lr = np.log1p(np.array([0.10, 0.10, np.nan, 0.10, 0.10]))
    out = flow.window_returns(lr, 2)
    assert np.isnan(out[0])
    assert out[1] == pytest.approx(0.21)
    assert np.isnan(out[2]) and np.isnan(out[3])  # el hueco no se rellena
    assert out[4] == pytest.approx(0.21)


def test_window_returns_serie_corta() -> None:
    assert np.all(np.isnan(flow.window_returns(np.zeros(3), 5)))


def test_group_series_promedia_solo_miembros_con_dato() -> None:
    rets = {"A": np.array([0.1, np.nan]), "B": np.array([0.3, 0.2])}
    g = flow.group_series(rets, {"g": ("A", "B", "Z"), "vacio": ("Z",)})
    assert g["g"][0] == pytest.approx(0.2) and g["g"][1] == pytest.approx(0.2)
    assert "vacio" not in g


def test_risk_appetite_es_riesgo_menos_defensa() -> None:
    gs = {k: np.array([0.05]) for k in flow.RISK_ON}
    gs.update({k: np.array([0.01]) for k in flow.RISK_OFF})
    assert flow.risk_appetite(gs)[0] == pytest.approx(0.04)
    del gs["bonos"]
    with pytest.raises(ValueError):
        flow.risk_appetite(gs)


def test_strict_percentile_no_mira_la_ventana_actual() -> None:
    s = np.array([0.0, 1.0, 2.0, 3.0, 99.0, 5.0])
    # con n=2 el pasado comparable son los 4 primeros: 99 no cuenta.
    pct, n = flow.strict_percentile(s, 2)
    assert n == 4 and pct == 1.0
    assert flow.strict_percentile(np.array([np.nan, 1.0, np.nan]), 1)[0] is None


def test_crypto_on_sessions_alinea_a_las_sesiones() -> None:
    d0 = date(2026, 1, 1)
    closes = {d0: 100.0, d0 + timedelta(days=3): 110.0}
    out = flow.crypto_on_sessions(closes, [d0, d0 + timedelta(days=3), d0 + timedelta(days=4)])
    assert np.isnan(out[0]) and out[1] == pytest.approx(0.10) and np.isnan(out[2])


def _panel(n: int = 400) -> tuple[list[date], dict[str, np.ndarray]]:
    rng = np.random.default_rng(0)
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
    tickers = [t for ts in flow.GROUPS.values() for t in ts] + list(flow.REFERENCES)
    rets = {t: rng.normal(0, 0.01, n) for t in tickers}
    rets["XLK"][-5:] = 0.05  # crecimiento lidera la última semana
    rets["SLV"][:] = np.nan  # un miembro sin datos queda fuera, no rompe el grupo
    return days, rets


def test_build_ordena_grupos_y_reporta_riesgo() -> None:
    rep = flow.build(_panel())
    assert rep["windows"] == [k for k, _ in flow.WINDOWS]
    assert rep["leaders"]["1s"][0] == "crecimiento"
    metales = next(g for g in rep["groups"] if g["group"] == "metales")
    assert metales["n_1s"] == 1
    ranks = sorted(g["rank_1s"] for g in rep["groups"])
    assert ranks == list(range(1, len(rep["groups"]) + 1))
    ra = rep["risk_appetite"]["1s"]
    assert ra["score"] is not None and 0.0 <= ra["pct"] <= 1.0
    spy = next(a for a in rep["assets"] if a["ticker"] == "SPY")
    assert spy["reference"] and spy["group"] is None


def test_build_cuadra_con_el_retorno_de_cada_miembro() -> None:
    days, rets = _panel()
    rep = flow.build((days, rets))
    xlk = next(a for a in rep["assets"] if a["ticker"] == "XLK")
    assert xlk["ret_1s"] == pytest.approx(1.05**5 - 1, abs=1e-6)


def test_build_panel_vacio() -> None:
    with pytest.raises(ValueError):
        flow.build(([], {}))


def test_rarity_movimiento_extremo_queda_arriba() -> None:
    rng = np.random.default_rng(1)
    daily = rng.normal(0, 0.005, 2000)
    daily[-5:] = 0.02  # una semana muy fuerte
    r = {x["window"]: x for x in flow.rarity(daily)}
    assert r["1s"]["pct"] > 0.99 and r["1s"]["z"] > 3
    assert r["52s"]["n_past"] > 0
