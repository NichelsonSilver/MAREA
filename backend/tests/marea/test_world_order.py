from datetime import date, timedelta

import numpy as np
import pytest

from marea import world_order as wo


def _days(n: int) -> list[date]:
    return [date(2015, 1, 1) + timedelta(days=i) for i in range(n)]


def test_level_metric_cambios_en_puntos_y_relativos() -> None:
    days = _days(2000)
    v = np.linspace(1.0, 3.0, 2000)
    pp = wo.level_metric(days, v, "pp")
    assert pp["value"] == pytest.approx(3.0)
    assert pp["pct"] == 1.0  # récord: todo lo anterior está por debajo
    one_year = v[-1] - v[-1 - 365]
    assert pp["chg_1a"] == pytest.approx(one_year, abs=1e-6)
    rel = wo.level_metric(days, v, "pct")
    assert rel["chg_1a"] == pytest.approx(v[-1] / v[-1 - 365] - 1, abs=1e-6)


def test_level_metric_horizonte_anterior_a_la_historia() -> None:
    m = wo.level_metric(_days(100), np.ones(100), "pct")
    assert m["chg_5a"] is None and m["chg_13s"] == pytest.approx(0.0)
    with pytest.raises(ValueError):
        wo.level_metric([], np.array([]), "pct")


def test_ratio_metric_percentil_contra_pasado_estricto() -> None:
    n = 1500
    lr = np.concatenate([np.zeros(n - 252), np.linspace(0, 0.5, 252)])
    m = wo.ratio_metric(_days(n), lr)
    assert m["value"] is None
    assert m["chg_1a"] == pytest.approx(np.expm1(0.5 - lr[-253]), abs=1e-6)
    assert m["pct"] == 1.0
    assert wo.ratio_metric(_days(300), np.zeros(300))["pct"] is None


def test_reading_marca_la_fase_contra_la_tendencia() -> None:
    r = wo.reading({"chg_5a": 0.5, "chg_13s": -0.1})
    assert r == {"long_trend": "sube", "short_phase": "baja", "against_trend": True}
    assert wo.reading({"chg_5a": None, "chg_13s": 0.1})["against_trend"] is False
    assert wo.sign_word(0.0) == "plano"


def test_dimensiones_tienen_claves_unicas() -> None:
    keys = [m.key for d in wo.DIMENSIONS for m in (d.headline, *d.support)]
    assert len(keys) == len(set(keys))
    assert len({d.key for d in wo.DIMENSIONS}) == len(wo.DIMENSIONS)
