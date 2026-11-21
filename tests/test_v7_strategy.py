import json

import numpy as np
import pandas as pd
import pytest

import settings
import strategy
from conftest import make_ohlcv

V7_COLS = ["STOCH_RSI", "VOL_SPIKE_DN", "PULLBACK_V7"]


@pytest.mark.parametrize("t", [40, 120, 220, 350, 500, 598, 599])
def test_v7_indicators_no_lookahead_truncate(random_walk, t):
    full = strategy.prep(random_walk)
    part = strategy.prep(random_walk.iloc[: t + 1])
    for col in V7_COLS:
        pd.testing.assert_series_equal(full[col].iloc[: t + 1], part[col], check_names=False, obj=f"{col}@{t}")
    # market_vol_scale 인과성 검증: 미래 시계열 잘라도 과거 시점 값 동일
    full_vs = strategy.market_vol_scale(random_walk["Close"].iloc[: t + 1])
    shocked = random_walk["Close"].copy()
    if t + 1 < len(shocked):
        shocked.iloc[t + 1 :] *= 3.0
    part_vs = strategy.market_vol_scale(shocked.iloc[: t + 1])
    assert full_vs == pytest.approx(part_vs)


def test_v7_indicators_future_shock(random_walk):
    t = 400
    base = strategy.prep(random_walk)
    shocked = random_walk.copy()
    shocked.iloc[t + 1 :, :4] *= 3.5
    shocked.iloc[t + 1 :, 4] *= 10.0
    alt = strategy.prep(shocked)
    for col in V7_COLS:
        pd.testing.assert_series_equal(base[col].iloc[: t + 1], alt[col].iloc[: t + 1])


def test_check_entry_pullback_v7_and_vol_spike_block():
    row_ok = pd.Series({"PULLBACK_V7": True, "PULLBACK_V6": True, "BUY": False})
    assert strategy.check_entry(row_ok, mkt_on=True, entry_mode="pullback_v7") == (True, "신규매수")
    assert strategy.check_entry(row_ok, mkt_on=False, entry_mode="pullback_v7") == (False, "매수신호(시장필터 OFF)")

    row_blocked = pd.Series({"PULLBACK_V7": False, "PULLBACK_V6": True, "BUY": False})
    assert strategy.check_entry(row_blocked, mkt_on=True, entry_mode="pullback_v7") == (False, "")

    # 실제 OHLCV 데이터에서 대량 거래량 급락(VOL_SPIKE_DN) 시 PULLBACK_V7 차단 확인
    close = np.linspace(100.0, 150.0, 260)
    close[-5:] = [148.0, 145.0, 142.0, 139.0, 134.0]
    df = make_ohlcv(close)
    df["High"] = close + 1.5
    df["Low"] = close - 1.5
    df["Open"] = close + 0.5
    df["Volume"] = 100_000.0
    out_clean = strategy.prep(df)
    assert bool(out_clean["VOL_SPIKE_DN"].iloc[-1]) is False
    assert bool(out_clean["PULLBACK_V7"].iloc[-1]) is True

    df_spike = df.copy()
    df_spike.iloc[-1, df_spike.columns.get_loc("Volume")] = 500_000.0
    out_spike = strategy.prep(df_spike)
    assert bool(out_spike["VOL_SPIKE_DN"].iloc[-1]) is True
    assert bool(out_spike["PULLBACK_V7"].iloc[-1]) is False


def test_market_vol_scale_clamping_and_inverse_vol_scaling():
    idx = pd.bdate_range("2020-01-01", periods=40)
    # 초저변동성 장세 -> 1.25 상한 클램프
    calm = pd.Series(100.0 * (1.0005 ** np.arange(40)), index=idx)
    assert strategy.market_vol_scale(calm, target_vol=0.16, lo=0.35, hi=1.25) == pytest.approx(1.25)

    # 고변동성 장세 -> 0.35 하한 클램프
    wild_rets = np.array([0.045 if i % 2 == 0 else -0.043 for i in range(40)])
    wild = pd.Series(100.0 * np.cumprod(1.0 + wild_rets), index=idx)
    assert strategy.market_vol_scale(wild, target_vol=0.16, lo=0.35, hi=1.25) == pytest.approx(0.35)

    # inverse_vol_amount vol_scale 반영 + max_pos_weight 캡
    amt_base = strategy.inverse_vol_amount(10_000_000, 0.25, target_risk=0.018, max_pos_weight=0.11, vol_scale=1.0)
    amt_half = strategy.inverse_vol_amount(10_000_000, 0.25, target_risk=0.018, max_pos_weight=0.11, vol_scale=0.5)
    amt_cap = strategy.inverse_vol_amount(10_000_000, 0.12, target_risk=0.018, max_pos_weight=0.11, vol_scale=1.25)
    assert amt_base == pytest.approx(720_000.0)
    assert amt_half == pytest.approx(360_000.0)
    assert amt_cap == pytest.approx(1_100_000.0)


def test_v7_settings_defaults_and_validation(tmp_path):
    cfg = settings.load()
    st = cfg["strategy"]
    assert st["entry_mode"] == "pullback_v7"
    assert st["target_risk"] == pytest.approx(0.018)
    assert st["max_pos_weight"] == pytest.approx(0.11)
    assert st["vol_targeting"] is True
    assert st["target_mkt_vol"] == pytest.approx(0.16)
    assert st["vol_scale_min"] == pytest.approx(0.35)
    assert st["vol_scale_max"] == pytest.approx(1.25)

    bad_vol = tmp_path / "bad_vol.json"
    bad_vol.write_text(json.dumps({"mode": "paper", "strategy": {"target_mkt_vol": 0.0}}), encoding="utf-8")
    with pytest.raises(ValueError):
        settings.load(bad_vol)

    bad_rng = tmp_path / "bad_rng.json"
    bad_rng.write_text(json.dumps({"mode": "paper", "strategy": {"vol_scale_min": 0.9, "vol_scale_max": 0.5}}), encoding="utf-8")
    with pytest.raises(ValueError):
        settings.load(bad_rng)
