import pandas as pd
import pytest

from backtest_hyung import stoch
from backtest_hyung3 import weekly_ok


def test_weekly_ok_shape_and_type(random_walk):
    w = weekly_ok(random_walk)
    assert w.index.equals(random_walk.index)
    assert w.dtype == bool


def test_weekly_ok_varies(random_walk):
    w = weekly_ok(random_walk)
    assert w.any() and (~w).any()


# ---------------------------------------------------------------- 미래 데이터 차단
@pytest.mark.parametrize("t", list(range(150, 600, 7)) + [597, 598, 599])
def test_weekly_ok_no_lookahead_truncate(random_walk, t):
    full = weekly_ok(random_walk)
    part = weekly_ok(random_walk.iloc[: t + 1])
    pd.testing.assert_series_equal(full.iloc[: t + 1], part, check_names=False)


def test_weekly_ok_current_week_change_does_not_affect_today(random_walk):
    """이번 주(월요일~오늘) 데이터를 바꿔도 오늘 값은 그대로 — 완성된 과거 주봉만 쓴다."""
    df = random_walk
    base = weekly_ok(df)
    for t in range(200, 590):
        day = df.index[t]
        wk_start = day.normalize() - pd.Timedelta(days=day.weekday())   # 이번 주 월요일
        mask = (df.index >= wk_start) & (df.index <= day)
        alt = df.copy()
        alt.loc[mask, ["Open", "High", "Low", "Close"]] *= 5.0 if (t % 2) else 0.2
        res = weekly_ok(alt.iloc[: t + 1])
        assert res.iloc[-1] == base.iloc[t], f"{day:%Y-%m-%d}"


def test_weekly_ok_uses_last_completed_week_on_mon_to_thu(random_walk):
    """docstring '직전 완성 주봉 기준': 월~목에는 지난 금요일에 끝난 주의 K>D 를 써야 한다."""
    df = random_walk
    w = df.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min",
                                  "Close": "last", "Volume": "sum"}).dropna()
    w = stoch(w)
    wk_up = w["K"] > w["D"]
    got = weekly_ok(df)
    days = [d for d in df.index[200:] if d.weekday() < 4]
    exp = [bool(wk_up[wk_up.index < d].iloc[-1]) for d in days]
    assert [bool(got[d]) for d in days] == exp
