import json

import numpy as np
import pandas as pd
import pytest

import market_data
import settings
import strategy
from conftest import make_ohlcv

V6_COLS = ["RSI2", "IBS", "SMA5", "SMA50", "SMA100", "SMA200", "VOL20", "CLENOW90", "SKIP_MOM", "PULLBACK_V6"]


@pytest.mark.parametrize("t", [30, 90, 150, 220, 350, 500, 598, 599])
def test_v6_indicators_no_lookahead_truncate(random_walk, t):
    full = strategy.prep(random_walk)
    part = strategy.prep(random_walk.iloc[: t + 1])
    for col in V6_COLS:
        pd.testing.assert_series_equal(full[col].iloc[: t + 1], part[col], check_names=False, obj=f"{col}@{t}")


def test_v6_indicators_future_shock(random_walk):
    t = 400
    base = strategy.prep(random_walk)
    shocked = random_walk.copy()
    shocked.iloc[t + 1 :, :4] *= 3.5
    alt = strategy.prep(shocked)
    for col in V6_COLS:
        pd.testing.assert_series_equal(base[col].iloc[: t + 1], alt[col].iloc[: t + 1])


def test_check_entry_modes():
    row = pd.Series({"PULLBACK_V6": True, "BUY": False, "TREND": True, "WOK": True})
    assert strategy.check_entry(row, mkt_on=True, entry_mode="pullback_v6") == (True, "신규매수")
    assert strategy.check_entry(row, mkt_on=False, entry_mode="pullback_v6") == (False, "매수신호(시장필터 OFF)")
    assert strategy.check_entry(row, mkt_on=True, entry_mode="stoch_v5") == (False, "")

    row2 = pd.Series({"PULLBACK_V6": False, "BUY": True, "TREND": True, "WOK": False})
    assert strategy.check_entry(row2, mkt_on=True, entry_mode="stoch_v5") == (False, "매수신호(주봉 하락 중)")


def test_inverse_vol_amount_scaling_and_cap():
    low_vol = strategy.inverse_vol_amount(10_000_000, 0.12, target_risk=0.016, max_pos_weight=0.10)
    mid_vol = strategy.inverse_vol_amount(10_000_000, 0.25, target_risk=0.016, max_pos_weight=0.10)
    high_vol = strategy.inverse_vol_amount(10_000_000, 0.50, target_risk=0.016, max_pos_weight=0.10)
    assert low_vol == pytest.approx(1_000_000.0)   # 10% 상한 캡
    assert mid_vol == pytest.approx(640_000.0)      # 0.016 / 0.25 = 6.4%
    assert high_vol == pytest.approx(320_000.0)     # 변동성 2배 -> 절반 크기


def test_evaluate_position_v6_modes():
    idx = pd.bdate_range("2020-01-01", periods=3)
    pos = {
        "name": "AAPL", "qty": 10, "cost": 1_000.0, "units": 1,
        "opened": "2020-01-01", "last_buy": "2020-01-01",
        "entry_px": 100.0, "entry_atr": 2.0, "max_high": 112.0, "above20": True,
    }
    # E3_atr: 손실 + -2.5*ATR 손절 (100 - 5 = 95 미만)
    f_atr = pd.DataFrame({
        "High": [101.0, 98.0, 95.0],
        "Close": [100.0, 96.0, 94.0],
        "MA20": [102.0, 101.0, 100.0],
        "SMA200": [90.0, 90.0, 90.0],
        "SMA5": [99.0, 98.0, 97.0],
        "ATR14": [2.0, 2.0, 2.0],
        "K": [30.0, 25.0, 20.0],
        "SELL": [False, False, False],
        "ADD": [False, False, False],
    }, index=idx)
    res_atr = strategy.evaluate_position(pos, f_atr, exit_mode="E3_atr", units=1, sell_cost=0.0005)
    assert res_atr["action"] == "sell" and "ATR" in res_atr["sig"]

    # E3_atr: 손실 + SMA200 이탈
    f_sma200 = f_atr.copy()
    f_sma200["Close"] = [100.0, 99.0, 97.5]
    f_sma200["SMA200"] = [98.0, 98.0, 98.0]
    res_sma = strategy.evaluate_position(pos, f_sma200, exit_mode="E3_atr", units=1, sell_cost=0.0005)
    assert res_sma["action"] == "sell" and "200일선" in res_sma["sig"]

    # Chandelier: 이익 중 고점 112 - 2.5*2 = 107 미만 이탈 시 매도
    f_chan = f_atr.copy()
    f_chan["High"] = [110.0, 112.0, 107.0]
    f_chan["Close"] = [108.0, 111.0, 106.5]
    res_chan = strategy.evaluate_position(pos, f_chan, exit_mode="chandelier", units=1, sell_cost=0.0005)
    assert res_chan["action"] == "sell" and "Chandelier" in res_chan["sig"]

    # meanrev: 종가 > SMA5 시 익절/회귀 매도
    f_mr = f_atr.copy()
    f_mr["Close"] = [99.0, 99.5, 102.0]
    f_mr["SMA5"] = [100.0, 100.0, 100.5]
    res_mr = strategy.evaluate_position(pos, f_mr, exit_mode="meanrev", units=1, sell_cost=0.0005)
    assert res_mr["action"] == "sell" and "평균회귀" in res_mr["sig"]


def test_rank_candidates_clenow():
    rows = [
        {"코드": "MSFT", "%K": 15.0, "Clenow90": 0.42, "RS60": 1.1},
        {"코드": "NVDA", "%K": 25.0, "Clenow90": 0.85, "RS60": 1.3},
        {"코드": "AAPL", "%K": 18.0, "Clenow90": None, "RS60": 0.95},
    ]
    ranked = strategy.rank_candidates(rows, rank_by="clenow")
    assert [r["코드"] for r in ranked] == ["NVDA", "MSFT", "AAPL"]


def test_is_us_ticker_and_settings_validation(tmp_path):
    assert market_data.is_us_ticker("AAPL") is True
    assert market_data.is_us_ticker("BRK-B") is True
    assert market_data.is_us_ticker("005930") is False

    cfg = settings.load()
    assert cfg["strategy"]["entry_mode"] == "pullback_v6"
    assert cfg["strategy"]["exit_mode"] == "E3_atr"
    assert cfg["strategy"]["sizing"] == "inverse_vol"

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"mode": "paper", "strategy": {"sizing": "martingale"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        settings.load(bad)
