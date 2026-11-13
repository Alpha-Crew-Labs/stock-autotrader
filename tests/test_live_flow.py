"""실전 흐름(아침 주문 -> 체결 -> 오후 장부 맞추기)을 가짜 증권사로 검증 — 네트워크 없음"""
import pandas as pd
import pytest

import settings
from broker import DryRunBroker, get_broker
from run_orders import plan_orders


CFG = settings.load()
CFG["limits"].update(max_order_krw=1_000_000, max_daily_buy_krw=1_500_000)


def _state():
    return {"capital0": 10_000_000, "cash": 10_000_000, "history": [], "equity_log": [],
            "positions": {"000660": {"name": "SK하이닉스", "qty": 3, "cost": 600_000.0, "units": 1,
                                     "unit_amt": 333_333, "opened": "2026-11-01", "last_buy": "2026-11-01"}},
            "pending": [
                {"side": "sell", "code": "000660", "name": "SK하이닉스", "why": "이익 + %K 80 후 하락", "created": "2026-11-12"},
                {"side": "buy", "code": "005930", "name": "삼성전자", "amount": 900_000, "why": "BUY 1/3", "created": "2026-11-12"},
                {"side": "buy", "code": "035420", "name": "NAVER", "amount": 900_000, "why": "BUY 1/3", "created": "2026-11-12"},
            ]}


def test_plan_orders_limits_and_sell_from_broker_balance():
    hold = {"000660": {"name": "SK하이닉스", "qty": 3, "sellable": 3, "avg_price": 200_000}}
    orders, skipped = plan_orders(_state(), hold, cash=5_000_000, cfg=CFG, price_fn=lambda c: 100_000)
    sides = [(o[0], o[1], o[3]) for o in orders]
    assert ("sell", "000660", 3) in sides                      # 매도는 증권사 매도가능수량
    assert ("buy", "005930", 8) in sides                       # 900,000 / (100,000*1.003) -> 8주
    # 하루 매수 상한 1,500,000: 두 번째 매수는 남은 한도 700,000 -> 6주
    assert ("buy", "035420", 6) in sides
    assert not skipped


def test_plan_orders_skips_when_no_cash_or_no_holding():
    orders, skipped = plan_orders(_state(), {}, cash=0, cfg=CFG, price_fn=lambda c: 100_000)
    assert orders == []
    assert any("잔고 없음" in s for s in skipped) and any("부족" in s for s in skipped)


def test_submitted_orders_are_not_resent():
    st = _state()
    for o in st["pending"]:
        o["status"] = "submitted"
    orders, _ = plan_orders(st, {}, cash=5_000_000, cfg=CFG, price_fn=lambda c: 100_000)
    assert orders == []


def test_reconcile_updates_ledger_from_broker(monkeypatch):
    import run_daily
    st = _state()
    for o in st["pending"]:
        o["status"] = "submitted"
    # 체결 후 잔고: 하이닉스 매도 완료, 삼성전자 8주 매수, NAVER 미체결
    broker = DryRunBroker(holdings={"005930": {"name": "삼성전자", "qty": 8, "sellable": 8, "avg_price": 100_000}},
                          cash=9_000_000)
    idx = pd.to_datetime(["2026-11-12", "2026-11-13"])
    monkeypatch.setattr(run_daily, "prices", lambda code, days=420: pd.DataFrame(
        {"Open": [210_000, 220_000], "High": 0, "Low": 0, "Close": 0, "Volume": 1}, index=idx))
    log = []
    run_daily.reconcile(st, broker, pd.Timestamp("2026-11-13"), {}, log)
    assert "000660" not in st["positions"] and st["history"][-1]["code"] == "000660"
    assert st["history"][-1]["ret"] > 0                       # 600,000 원가 -> 220,000*3 매도
    assert st["positions"]["005930"]["qty"] == 8 and st["positions"]["005930"]["units"] == 1
    assert st["pending"] == [] and st["cash"] == 9_000_000
    assert any("미체결" in x and "NAVER" in x for x in log)


def test_live_requires_double_unlock():
    cfg = settings.load()
    cfg["mode"] = "live"
    cfg["live"]["enabled"] = False
    with pytest.raises((PermissionError, Exception)):
        get_broker(cfg, live_flag=True)                        # enabled=false 면 거부
    cfg["live"]["enabled"] = True
    with pytest.raises((PermissionError, Exception)):
        get_broker(cfg, live_flag=False)                       # --live 없으면 거부


def test_paper_mode_never_uses_real_broker():
    cfg = settings.load()
    cfg["mode"] = "paper"
    assert isinstance(get_broker(cfg, live_flag=True), DryRunBroker)


def test_loss_limits():
    cfg = settings.load()
    log = [{"equity": 100}, {"equity": 96}]
    assert "하루 손실" in settings.check_loss_limits(cfg, log)
    log = [{"equity": 100}, {"equity": 120}, {"equity": 102}, {"equity": 101}]
    assert "고점 대비" in settings.check_loss_limits(cfg, log)
    assert settings.check_loss_limits(cfg, [{"equity": 100}, {"equity": 99}]) is None
