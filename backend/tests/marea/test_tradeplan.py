import math

import numpy as np
import pytest

from marea.tradeplan import DAY_MS, _day, build_plan, daily_structure, horizon_sigma, render


def _series(n: int = 200, seed: int = 0):
    rng = np.random.default_rng(seed)
    close = 2000 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    t = np.arange(n, dtype=np.int64) * DAY_MS + 1_700_000_000_000
    return t, high, low, close


def test_medias_simples_y_rangos():
    t, h, lo, c = _series()
    s = daily_structure(t, h, lo, c)
    assert s.ma[99] == pytest.approx(c[-99:].mean())
    assert s.ma[7] == pytest.approx(c[-7:].mean())
    assert s.range60 == (pytest.approx(lo[-60:].min()), pytest.approx(h[-60:].max()))
    assert s.last_close == c[-1]


def test_no_lista_pivotes_sin_confirmar():
    t, h, lo, c = _series()
    # Un máximo nuevo en la penúltima vela: todavía no tiene k velas detrás.
    h = h.copy()
    h[-2] = h.max() * 1.5
    s = daily_structure(t, h, lo, c, k=5)
    unconfirmed = {_day(int(x)) for x in t[-5:]}
    assert not any(p.day in unconfirmed for p in s.pivots)


def test_mutar_el_futuro_no_cambia_los_pivotes_confirmados_del_pasado():
    t, h, lo, c = _series()
    a = daily_structure(t[:-10], h[:-10], lo[:-10], c[:-10])
    h2, lo2 = h.copy(), lo.copy()
    h2[-10:] *= 3.0
    lo2[-10:] *= 0.3
    b = daily_structure(t, h2, lo2, c, lookback=130)
    assert a.pivots and set(a.pivots) <= set(b.pivots)


def test_pocas_velas_es_error():
    t, h, lo, c = _series(100)
    with pytest.raises(ValueError):
        daily_structure(t, h, lo, c)


def test_sigma_conservadora():
    t, h, lo, c = _series()
    s = daily_structure(t, h, lo, c)
    assert horizon_sigma(s, 4) == pytest.approx(max(s.vol30, s.vol90) * 2)


def test_el_leverage_no_cambia_la_perdida_al_stop():
    t, h, lo, c = _series()
    s = daily_structure(t, h, lo, c)
    p = build_plan(
        s,
        mark=2400,
        entry=2300,
        stop=2190,
        targets=[2470, 2555],
        capital=500,
        risk=0.01,
        leverages=[3, 10, 20],
        horizon_days=5,
        maker_entry=True,
    )
    losses = {round(z.loss_at_stop, 9) for z in p.sizings}
    assert len(losses) == 1
    assert p.sizings[0].margin > p.sizings[-1].margin
    assert p.sizings[-1].liquidation_to_stop < p.sizings[0].liquidation_to_stop
    # Sin edge el EV es −costo, igual para los dos objetivos.
    assert p.targets[0].ev_no_edge_usd == p.targets[1].ev_no_edge_usd < 0
    assert p.targets[1].reward_risk_net > p.targets[0].reward_risk_net
    txt = render(s, p, funding=0.0001, stale_mark=False)
    assert "NO medidas" in txt and "Nunca ejecuta órdenes" in txt
    assert not math.isnan(p.sigma_horizon)


def test_unidad_del_simbolo_y_aviso_de_redondeo():
    # BTC a ~81k con 0,001 de paso: 5 USD de riesgo con stop a 4% caben en
    # 0,0015 BTC y el redondeo deja 0,001, o sea ~2/3 de lo pedido.
    t, h, lo, c = _series()
    c, h, lo = c * 40, h * 40, lo * 40
    s = daily_structure(t, h, lo, c)
    entry = float(c[-1])
    p = build_plan(
        s,
        mark=entry,
        entry=entry,
        stop=entry * 0.96,
        targets=[entry * 1.08],
        capital=500,
        risk=0.01,
        leverages=[3],
        horizon_days=5,
        maker_entry=True,
    )
    txt = render(s, p, funding=None, stale_mark=False, symbol="BTCUSDT")
    assert " BTC · notional" in txt and " ETH · notional" not in txt
    assert p.sizings[0].loss_at_stop < 0.8 * p.sizings[0].risk_budget
    assert "redondea hacia abajo" in txt
