import numpy as np
import pandas as pd
import pytest

import strategy
from conftest import make_ohlcv


def test_prep_extra_indicators_no_lookahead_truncate(random_walk):
    full = strategy.prep(random_walk)
    for t in (30, 60, 150, 250, 420, 598, 599):
        part = strategy.prep(random_walk.iloc[: t + 1])
        for col in ("MA20", "RET60", "ATR14"):
            pd.testing.assert_series_equal(full[col].iloc[: t + 1], part[col], check_names=False, obj=f"{col}@{t}")


def test_prep_extra_indicators_future_shock(random_walk):
    t = 350
    base = strategy.prep(random_walk)
    shocked = random_walk.copy()
    shocked.iloc[t + 1 :, :4] *= 4.0
    alt = strategy.prep(shocked)
    for col in ("MA20", "RET60", "ATR14"):
        pd.testing.assert_series_equal(base[col].iloc[: t + 1], alt[col].iloc[: t + 1])


def _pos(opened="2020-01-01", last_buy="2020-01-01", qty=10, cost=100_000.0, units=1, hit80=False, above20=False):
    return {
        "name": "테스트",
        "qty": qty,
        "cost": cost,
        "units": units,
        "unit_amt": 100_000.0,
        "opened": opened,
        "last_buy": last_buy,
        "hit80": hit80,
        "above20": above20,
    }


def _frame(rows):
    idx = pd.bdate_range("2020-01-01", periods=len(rows))
    df = pd.DataFrame(rows, index=idx)
    return df


def test_evaluate_position_e2_profit_vs_loss():
    # 이익 중: K 80 미도달이면 SELL 신호여도 보유
    f = _frame([
        {"Close": 11000, "K": 65.0, "D": 60.0, "MA20": 10500, "SELL": False, "ADD": False},
        {"Close": 11000, "K": 60.0, "D": 62.0, "MA20": 10500, "SELL": True, "ADD": False},
    ])
    res = strategy.evaluate_position(_pos(), f, exit_mode="E2")
    assert res["action"] == "hold"

    # 이익 중 + 80 도달 후 K 하락 -> 매도
    f2 = _frame([
        {"Close": 12000, "K": 85.0, "D": 75.0, "MA20": 10500, "SELL": False, "ADD": False},
        {"Close": 11800, "K": 82.0, "D": 80.0, "MA20": 10600, "SELL": False, "ADD": False},
    ])
    res2 = strategy.evaluate_position(_pos(), f2, exit_mode="E2")
    assert res2["action"] == "sell" and res2["hit80"]

    # 손실 중 + SELL -> 즉시 매도
    res_loss = strategy.evaluate_position(_pos(cost=150_000.0), f, exit_mode="E2")
    assert res_loss["action"] == "sell"


def test_evaluate_position_e3_and_e4():
    # 이익 중 + 20일선 위 유지 -> SELL 신호에도 보유
    f_hold = _frame([
        {"Close": 11000, "K": 70.0, "D": 72.0, "MA20": 10500, "SELL": True, "ADD": False},
    ])
    assert strategy.evaluate_position(_pos(), f_hold, exit_mode="E3")["action"] == "hold"

    # 이익 중 + 20일선 상회 이력 후 이탈 -> E3 매도
    f_exit = _frame([
        {"Close": 11500, "K": 72.0, "D": 68.0, "MA20": 11000, "SELL": False, "ADD": False},
        {"Close": 10800, "K": 55.0, "D": 62.0, "MA20": 11000, "SELL": True, "ADD": False},
    ])
    assert strategy.evaluate_position(_pos(), f_exit, exit_mode="E3")["action"] == "sell"
    assert strategy.evaluate_position(_pos(), f_exit, exit_mode="E4")["action"] == "sell"

    # E0 는 이익 중이어도 SELL 이면 매도
    assert strategy.evaluate_position(_pos(), f_hold, exit_mode="E0")["action"] == "sell"


def test_evaluate_position_hold_add_under20_and_cooldown():
    idx = pd.bdate_range("2020-01-01", periods=8)
    f = pd.DataFrame(
        {
            "Close": [9000] * 8,
            "K": [15.0] * 8,
            "D": [18.0] * 8,
            "MA20": [10000] * 8,
            "SELL": [True] * 8,
            "ADD": [True] * 8,
        },
        index=idx,
    )
    # 기본(hold_add_under20=False): 손실 + SELL 이면 매도
    p_old = _pos(opened="2020-01-01", last_buy="2020-01-01", cost=120_000.0, units=1)
    assert strategy.evaluate_position(p_old, f, exit_mode="E3", hold_add_under20=False)["action"] == "sell"

    # hold_add_under20=True + 5거래일 경과 + ADD -> 매도 보류 후 추가매수
    res_add = strategy.evaluate_position(p_old, f, exit_mode="E3", hold_add_under20=True)
    assert res_add["action"] == "add"

    # 직전 매수 후 5거래일 미만이면 ADD 안 나감
    p_recent = _pos(opened="2020-01-01", last_buy=str(idx[-3].date()), cost=120_000.0, units=1)
    assert strategy.evaluate_position(p_recent, f, exit_mode="E3", hold_add_under20=True)["action"] == "hold"

    # 이미 3회(max units) 채웠으면 hold_add_under20 여도 손실 SELL 실행
    p_full = _pos(opened="2020-01-01", last_buy="2020-01-01", cost=120_000.0, units=3)
    assert strategy.evaluate_position(p_full, f, exit_mode="E3", hold_add_under20=True)["action"] == "sell"


def test_rank_candidates_rs60_and_k():
    rows = [
        {"코드": "001", "%K": 12.0, "RS60": 1.05, "Ret60%": 5.0},
        {"코드": "002", "%K": 18.0, "RS60": 1.40, "Ret60%": 25.0},
        {"코드": "003", "%K": 15.0, "RS60": None, "Ret60%": 1.30},
    ]
    by_rs = strategy.rank_candidates(rows, rank_by="rs60")
    assert [r["코드"] for r in by_rs] == ["002", "003", "001"]
    by_k = strategy.rank_candidates(rows, rank_by="k")
    assert [r["코드"] for r in by_k] == ["001", "003", "002"]
