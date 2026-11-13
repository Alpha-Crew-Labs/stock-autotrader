import numpy as np
import pandas as pd
import pytest

from backtest_hyung2 import prep
from conftest import make_ohlcv

COLS = ["K", "D", "BUY", "ADD", "SELL", "TREND"]


def _vshape(n_down=40, n_up=40, n_down2=40):
    c = np.r_[np.linspace(100, 60, n_down), np.linspace(60, 100, n_up)[1:], np.linspace(100, 70, n_down2)[1:]]
    # 약간의 흔들림을 넣어 교차가 실제로 생기게 한다
    c = c * (1 + 0.004 * np.sin(np.arange(len(c))))
    return make_ohlcv(c)


def test_buy_after_oversold_recovery():
    df = prep(_vshape())
    trough = 39
    win = df.iloc[trough - 5: trough + 15]
    assert win["K"].min() < 20                      # 과매도 구간 진입
    assert win["BUY"].any(), "바닥 반등 구간에서 BUY 가 나와야 함"
    # BUY 는 모두 과매도 근처 조건을 만족
    b = df[df["BUY"]]
    assert (b["K"] < 30).all()


def test_sell_on_dead_cross():
    df = prep(_vshape())
    K, D = df["K"], df["D"]
    dc = (K < D) & (K.shift() >= D.shift())
    assert dc.any()
    assert df.loc[dc, "SELL"].all(), "데드크로스 봉은 항상 SELL"


def test_sell_after_short_rally_then_drop():
    # 하락 40일 -> 짧은 반등 12일(K>D 뚜렷) -> 다시 하락: 고점 직후 데드크로스로 SELL
    c = np.r_[np.linspace(100, 60, 40), np.linspace(60, 75, 13)[1:], np.linspace(75, 55, 30)[1:]]
    df = prep(make_ohlcv(c))
    peak = 51
    assert df["K"].iloc[peak] > df["D"].iloc[peak]
    K, D = df["K"], df["D"]
    dc = (K < D) & (K.shift() >= D.shift())
    after = dc.iloc[peak + 1: peak + 8]
    assert after.any(), "고점 후 며칠 안에 데드크로스"
    assert df["SELL"].iloc[peak + 1: peak + 8].any()


def test_signals_are_bool_no_nan(random_walk):
    df = prep(random_walk)
    for c in ("BUY", "ADD", "SELL", "TREND"):
        assert df[c].dtype == bool
        assert df[c].notna().all()


def test_prep_does_not_mutate_input(random_walk):
    before = random_walk.copy()
    prep(random_walk)
    pd.testing.assert_frame_equal(random_walk, before)


def test_trend_rule():
    up = prep(make_ohlcv(100 * 1.002 ** np.arange(300)))
    assert not up["TREND"].iloc[:199].any(), "MA200 첫 값(199번째 행) 전엔 False"
    assert up["TREND"].iloc[199:].all()

    dn = prep(make_ohlcv(100 * 0.998 ** np.arange(300)))
    assert not dn["TREND"].any()

    # 종가 > MA200 이지만 120일 수익률 <= 0 이면 False: 길게 오른 뒤 최근 120일 소폭 하락
    c = np.r_[100 * 1.01 ** np.arange(250), (100 * 1.01 ** 249) * 0.9995 ** np.arange(1, 131)]
    df = prep(make_ohlcv(c))
    last = df.iloc[-1]
    assert last["Close"] > df["Close"].rolling(200).mean().iloc[-1]
    assert df["Close"].pct_change(120).iloc[-1] < 0
    assert not last["TREND"]


def test_trend_matches_formula(random_walk):
    df = prep(random_walk)
    c = random_walk["Close"]
    exp = (c > c.rolling(200).mean()) & (c.pct_change(120) > 0)
    assert (df["TREND"] == exp).all()


# ---------------------------------------------------------------- 미래 데이터 차단
@pytest.mark.parametrize("t", [30, 60, 150, 220, 250, 333, 420, 500, 598, 599])
def test_prep_no_lookahead_truncate(random_walk, t):
    full = prep(random_walk)
    part = prep(random_walk.iloc[: t + 1])
    for c in COLS:
        a, b = full[c].iloc[: t + 1], part[c]
        pd.testing.assert_series_equal(a, b, check_names=False, obj=f"{c} @ t={t}")


def test_prep_future_perturbation_does_not_change_past(random_walk):
    t = 400
    base = prep(random_walk)
    shocked = random_walk.copy()
    shocked.iloc[t + 1:, :4] *= 3.0           # 미래 가격을 크게 바꿈
    alt = prep(shocked)
    pd.testing.assert_frame_equal(base.iloc[: t + 1][COLS], alt.iloc[: t + 1][COLS])
