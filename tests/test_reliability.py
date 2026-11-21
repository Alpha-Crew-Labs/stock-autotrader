import datetime as dt
import json
import logging

import pandas as pd
import pytest

import broker as br
import notify
import run_daily
import run_orders
import settings
from conftest import make_ohlcv


def test_atomic_write_json_clean(tmp_path):
    target = tmp_path / "sub" / "state.json"
    settings.atomic_write_json(target, {"ok": True, "n": 3})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True, "n": 3}
    assert [p.name for p in target.parent.iterdir()] == ["state.json"]


def test_read_only_broker_blocks_orders(monkeypatch, tmp_path):
    cfg_p = tmp_path / "config.local.json"
    cfg_p.write_text(json.dumps({"kiwoom_appkey": "DUMMY_A", "kiwoom_secretkey": "DUMMY_S"}), encoding="utf-8")
    import kiwoom_client as kc
    monkeypatch.setattr(kc, "CONFIG_PATH", cfg_p)

    cfg = settings.load()
    cfg["mode"] = "live"
    cfg["live"]["enabled"] = False
    b = br.get_broker(cfg, live_flag=False, read_only=True)
    assert b.read_only is True
    with pytest.raises(PermissionError):
        b.buy("005930", 1)
    with pytest.raises(PermissionError):
        b.sell("005930", 1)


def test_plan_orders_isolates_price_error_and_expires_old_buy():
    cfg = settings.load()
    state = {
        "pending": [
            {"side": "sell", "code": "000660", "name": "SK하이닉스", "why": "SELL", "created": "2026-11-01"},
            {"side": "buy", "code": "999999", "name": "오류종목", "amount": 300_000, "why": "BUY", "created": "2026-11-12"},
            {"side": "buy", "code": "005930", "name": "삼성전자", "amount": 300_000, "why": "BUY", "created": "2026-11-12"},
            {"side": "buy", "code": "035420", "name": "NAVER", "amount": 300_000, "why": "BUY", "created": "2026-11-02"},
        ]
    }
    holdings = {"000660": {"name": "SK하이닉스", "qty": 2, "sellable": 2, "avg_price": 200_000}}

    def fake_px(code):
        if code == "999999":
            raise RuntimeError("시세 서버 오류")
        return 50_000.0

    orders, skipped = run_orders.plan_orders(
        state, holdings, cash=5_000_000, cfg=cfg, price_fn=fake_px, today=dt.date(2026, 11, 13)
    )
    # 오래된 매도(2026-11-01)는 리스크 청산이므로 실행, 오래된 매수(2026-11-02)는 만료, 오류종목은 격리, 삼성전자는 정상 실행
    assert ("sell", "000660", "SK하이닉스", 2, "SELL") in orders
    assert any(o[1] == "005930" and o[0] == "buy" for o in orders)
    assert not any(o[1] in ("999999", "035420") for o in orders)
    assert any("가격 조회 오류" in s for s in skipped)
    assert any("만료된 대기주문" in s for s in skipped)


def test_run_daily_idempotent_and_preserves_holding_on_price_error(monkeypatch, tmp_path):
    idx_close = [100.0 * (1.001 ** i) for i in range(300)]
    k200 = make_ohlcv(idx_close, start="2025-01-01")

    # V자 바닥 반등으로 마지막 날 BUY 신호 발생 유도
    import numpy as np
    c_buy = np.r_[np.linspace(100, 140, 220), np.linspace(140, 95, 45)[1:], np.linspace(95, 112, 10)[1:]]
    # 200일선 위 + 120일 수익 > 0 만족하도록 조정
    c_buy = np.r_[np.full(200, 80.0), np.linspace(80, 150, 60)[1:], np.linspace(150, 115, 25)[1:], np.linspace(115, 126, 7)[1:]]
    df_buy = make_ohlcv(c_buy, start="2025-01-01")
    df_buy.index = k200.index[: len(df_buy)]
    k200 = k200.iloc[: len(df_buy)]

    state_path = tmp_path / "state" / "paper.json"
    state_path.parent.mkdir(parents=True)
    # 보유 종목 000660 은 시세 조회 오류 발생 가정 -> 직전가 200,000원 유지되어야 함
    state_path.write_text(
        json.dumps({
            "capital0": 10_000_000,
            "cash": 9_000_000,
            "positions": {
                "000660": {
                    "name": "SK하이닉스",
                    "qty": 5,
                    "cost": 1_000_000.0,
                    "units": 1,
                    "unit_amt": 333_333.0,
                    "opened": str(k200.index[-10].date()),
                    "last_buy": str(k200.index[-10].date()),
                    "last_price": 200_000.0,
                }
            },
            "pending": [],
            "history": [],
            "equity_log": [{"date": str(k200.index[-2].date()), "equity": 10_000_000, "cash": 9_000_000, "n_pos": 1, "mkt_on": True}],
        }),
        encoding="utf-8",
    )

    def fake_prices(code, days=420):
        if code == "069500":
            return k200.copy()
        if code == "000660":
            raise RuntimeError("일시 조회 실패")
        return df_buy.copy()

    monkeypatch.setattr(run_daily, "STATE", state_path)
    monkeypatch.setattr(run_daily, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(run_daily, "DASH", tmp_path / "dashboard")
    monkeypatch.setattr(run_daily, "prices", fake_prices)
    monkeypatch.setattr(run_daily, "load_watchlist", lambda top: {"005930": "삼성전자"})
    monkeypatch.setattr(settings, "KILL", tmp_path / "KILL_SWITCH")
    monkeypatch.setattr(run_daily, "CFG", settings.load())
    monkeypatch.setattr("sys.argv", ["run_daily.py", "--no-notify"])

    run_daily.main()
    st1 = json.loads(state_path.read_text(encoding="utf-8"))
    # 보유 평가 5*200,000=1,000,000 + 현금 9,000,000 = 10,000,000원 유지 (거짓 손실 없음)
    assert st1["equity_log"][-1]["equity"] == 10_000_000
    n_pend1 = len(st1["pending"])

    # 같은 날 재실행해도 pending 이 불어나지 않는다
    run_daily.main()
    st2 = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(st2["pending"]) == n_pend1


def test_notify_noop_and_no_secret_leak(monkeypatch, caplog, tmp_path):
    assert notify.send_telegram("hello", local_cfg={}) is False

    secret_token = "SECRET_BOT_TOKEN_XYZ"
    def boom(*a, **k):
        raise RuntimeError(f"https://api.telegram.org/bot{secret_token}/sendMessage failed")

    monkeypatch.setattr(notify.requests, "post", boom)
    with caplog.at_level(logging.WARNING, logger="notify"):
        ok = notify.send_telegram("hello", local_cfg={"telegram_bot_token": secret_token, "telegram_chat_id": "123"})
    assert ok is False
    assert secret_token not in caplog.text
