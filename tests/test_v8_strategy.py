from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import dashboard
import run_daily
import settings
import strategy
from conftest import make_ohlcv


def _steady_bench(n_days: int = 240, daily_ret: float = 0.001, tail_rets: np.ndarray | None = None) -> pd.Series:
    idx = pd.bdate_range("2020-01-01", periods=n_days + (len(tail_rets) if tail_rets is not None else 0))
    r = np.full(n_days - 1, daily_ret, dtype=float)
    if tail_rets is not None:
        r = np.concatenate([r, tail_rets])
    close = np.empty(len(idx), dtype=float)
    close[0] = 100.0
    close[1:] = 100.0 * np.cumprod(1.0 + r)
    return pd.Series(close, index=idx)


def test_market_regime_four_states():
    # 1. ON: 200일선 위 + 50일선 위 + VolRatio <= 1.50
    up = _steady_bench(240, daily_ret=0.001)
    r_on = strategy.market_regime(up, target_vol=0.16, lo=0.35, hi=1.25, vol_ratio_max=1.50, yellow_mult=0.50)
    assert r_on["mkt_on"] is True
    assert r_on["above50"] is True
    assert r_on["vol_brake"] is False
    assert r_on["yellow_light"] is False
    assert r_on["label"] == "ON"
    assert r_on["regime_scale"] == pytest.approx(r_on["vol_scale"])

    # 2. YELLOW(x0.50): 200일선 위지만 50일선 아래 + 완만한 조정(VolRatio <= 1.50)
    mild_pull = np.array([-0.0022] * 35)
    yellow_s = _steady_bench(220, daily_ret=0.0012, tail_rets=mild_pull)
    r_yel = strategy.market_regime(yellow_s, target_vol=0.16, lo=0.35, hi=1.25, vol_ratio_max=1.50, yellow_mult=0.50)
    assert r_yel["mkt_on"] is True
    assert r_yel["above50"] is False
    assert r_yel["vol_brake"] is False
    assert r_yel["yellow_light"] is True
    assert r_yel["label"] == "YELLOW(x0.50)"
    assert r_yel["regime_scale"] == pytest.approx(r_yel["vol_scale"] * 0.50)

    # 3. BRAKE(VolRatio): 200일선 위지만 최근 20일 변동성이 60일 대비 1.50배 급등
    rng = np.random.default_rng(7)
    base_rets = rng.normal(0.0015, 0.004, 220)
    shock_rets = np.array([0.04 if i % 2 == 0 else -0.036 for i in range(18)])
    idx = pd.bdate_range("2020-01-01", periods=1 + len(base_rets) + len(shock_rets))
    c = np.empty(len(idx), dtype=float)
    c[0] = 100.0
    c[1:] = 100.0 * np.cumprod(1.0 + np.concatenate([base_rets, shock_rets]))
    brake_s = pd.Series(c, index=idx)
    r_brk = strategy.market_regime(brake_s, target_vol=0.16, lo=0.35, hi=1.25, vol_ratio_max=1.50, yellow_mult=0.50)
    assert r_brk["mkt_on"] is True
    assert r_brk["vol_ratio"] > 1.50
    assert r_brk["vol_brake"] is True
    assert r_brk["label"] == "BRAKE(VolRatio)"
    assert r_brk["regime_scale"] == 0.0

    # 4. OFF: 200일선 아래
    bear = _steady_bench(240, daily_ret=-0.0015)
    r_off = strategy.market_regime(bear, target_vol=0.16, lo=0.35, hi=1.25, vol_ratio_max=1.50, yellow_mult=0.50)
    assert r_off["mkt_on"] is False
    assert r_off["label"] == "OFF"
    assert r_off["regime_scale"] == 0.0


@pytest.mark.parametrize("t", [60, 180, 220, 350, 500])
def test_market_regime_no_lookahead_future_shock(random_walk, t):
    close = random_walk["Close"].copy()
    base = strategy.market_regime(close.iloc[: t + 1])

    shocked = close.copy()
    if t + 1 < len(shocked):
        shocked.iloc[t + 1 :] *= 3.0
    alt = strategy.market_regime(shocked.iloc[: t + 1])

    assert base == alt


def test_check_entry_vol_brake_and_v8_alias():
    row = pd.Series({"PULLBACK_V7": True, "PULLBACK_V6": True, "BUY": True, "TREND": True, "WOK": True})
    assert strategy.check_entry(row, mkt_on=True, entry_mode="pullback_v7", vol_brake=False) == (True, "신규매수")
    assert strategy.check_entry(row, mkt_on=True, entry_mode="pullback_v8", vol_brake=False) == (True, "신규매수")
    assert strategy.check_entry(row, mkt_on=True, entry_mode="pullback_v7", vol_brake=True) == (False, "매수신호(변동성 브레이크)")
    assert strategy.check_entry(row, mkt_on=False, entry_mode="pullback_v7", vol_brake=True) == (False, "매수신호(시장필터 OFF)")


def test_v8_settings_defaults_and_invariants(tmp_path):
    cfg = settings.load()
    # 절대 불변 운영 안전장치 확인
    assert cfg["mode"] == "paper"
    assert cfg["live"]["enabled"] is False
    assert cfg["limits"] == {
        "max_order_krw": 1_000_000,
        "max_daily_buy_krw": 3_000_000,
        "daily_loss_stop_pct": 3.0,
        "max_drawdown_stop_pct": 15.0,
        "buy_price_buffer_pct": 0.003,
    }
    st = cfg["strategy"]
    assert st["vol_ratio_brake"] == pytest.approx(1.50)
    assert st["yellow_light_mult"] == pytest.approx(0.50)
    assert st["cash_park"] is True
    assert st["cash_park_etf_kr"] == "153130"
    assert st["cash_park_etf_us"] == "BIL"
    assert st["cash_park_buffer_pct"] == pytest.approx(0.02)
    assert st["cash_park_deadband_pct"] == pytest.approx(0.05)

    for bad_st in (
        {"vol_ratio_brake": 0.0},
        {"yellow_light_mult": 0.0},
        {"yellow_light_mult": 1.2},
        {"cash_park_buffer_pct": 0.6},
        {"cash_park_deadband_pct": -0.1},
    ):
        p = tmp_path / "bad_v8.json"
        p.write_text(json.dumps({"mode": "paper", "strategy": bad_st}), encoding="utf-8")
        with pytest.raises(ValueError):
            settings.load(p)


def test_rebalance_cash_park_deadband_raise_and_fill_pending_isolation():
    # 1. 5% 데드밴드 이내면 매수하지 않음 (2% 버퍼 + 5% 데드밴드 = 7% 초과분만 매수)
    s = {"capital0": 10_000_000.0, "cash": 650_000.0, "positions": {}, "pending": [], "history": []}
    log = []
    run_daily.rebalance_cash_park_open(
        s, "153130", cp_open=100_000.0, prev_equity=10_000_000.0,
        buffer_pct=0.02, deadband_pct=0.05, etf_cost=0.0005, day_str="2026-01-05", log=log,
    )
    assert s["cash_park"]["qty"] == 0
    assert s["cash"] == 650_000.0
    assert log == []

    # 2. 초과 유휴현금 자동 파킹 -> positions/history 비오염, cash >= 0 불변
    s["cash"] = 5_000_000.0
    run_daily.rebalance_cash_park_open(
        s, "153130", cp_open=100_000.0, prev_equity=10_000_000.0,
        buffer_pct=0.02, deadband_pct=0.05, etf_cost=0.0005, day_str="2026-01-05", log=log,
    )
    # 초과분 4,800,000 / 100,050 = 47주
    assert s["cash_park"]["qty"] == 47
    assert s["cash"] >= 0.0
    assert s["positions"] == {}
    assert s["history"] == []

    # 3. 다음날 주식 매수 대기주문에 현금이 부족하면 cash_park 선매도 후 주식 매수 정상 체결
    idx = pd.bdate_range("2026-01-05", periods=2)
    cp_df = pd.DataFrame({"Open": [100_000.0, 100_000.0], "Close": [100_000.0, 100_000.0]}, index=idx)
    stock_df = pd.DataFrame({"Open": [50_000.0, 50_000.0], "Close": [50_000.0, 50_000.0]}, index=idx)
    s["pending"] = [
        {"side": "buy", "code": "005930", "name": "삼성전자", "amount": 1_000_000.0, "why": "BUY", "created": "2026-01-05"}
    ]
    px_cache = {"005930": stock_df, "153130": cp_df}
    cp_cfg = {
        "enabled": True, "code": "153130", "df": cp_df,
        "prev_equity": 10_000_000.0, "buffer_pct": 0.02, "deadband_pct": 0.05, "etf_cost": 0.0005,
    }
    log2 = []
    run_daily.fill_pending(s, px_cache, log2, buy_cost=0.00015, sell_cost=0.00215, cash_park_cfg=cp_cfg, asof=idx[1])
    assert "005930" in s["positions"]
    assert s["positions"]["005930"]["qty"] > 0
    assert s["cash_park"]["qty"] < 47
    assert s["cash"] >= 0.0
    assert s["history"] == []


def test_dashboard_renders_cash_park_and_v8_regime(tmp_path):
    idx = pd.to_datetime(["2026-11-10", "2026-11-11"])
    k200 = pd.Series([35000.0, 35200.0], index=idx)
    state = {
        "capital0": 10_000_000, "cash": 200_000,
        "cash_park": {"code": "153130", "qty": 60, "cost": 6_000_000.0, "last_price": 100_000.0, "trades": 1},
        "equity_log": [{"date": "2026-11-10", "equity": 10_000_000}, {"date": "2026-11-11", "equity": 10_100_000}],
        "positions": {"005930": {"name": "삼성전자", "qty": 55, "cost": 3_800_000, "units": 1, "opened": "2026-11-10"}},
        "pending": [], "history": [],
    }
    snap = {
        "asof": "2026-11-11", "mkt_on": True, "regime_label": "YELLOW(x0.50)",
        "cash_park_code": "153130", "cash_park_qty": 60, "cash_park_val": 6_000_000,
        "last_run": "2026-11-11 16:10", "rows": [],
    }
    out = dashboard.build(state, snap, k200, tmp_path)
    page = out.read_text(encoding="utf-8")
    assert "투자비중 (주식 / 단기채)" in page
    assert "YELLOW(x0.50)" in page
    assert "ON · 매수 가능" in page
