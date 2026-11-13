import pandas as pd

import dashboard


def _fake():
    state = {
        "capital0": 10_000_000, "cash": 4_000_000,
        "equity_log": [{"date": "2026-11-09", "equity": 10_000_000},
                       {"date": "2026-11-10", "equity": 10_050_000},
                       {"date": "2026-11-11", "equity": 10_120_000}],
        "positions": {"005930": {"name": "삼성전자", "qty": 50, "cost": 3_500_000, "units": 1, "opened": "2026-11-10"}},
        "pending": [{"side": "buy", "name": "<SK하이닉스>", "code": "000660", "why": "BUY", "amount": 1_000_000,
                     "created": "2026-11-11"},
                    {"side": "sell", "name": "현대차", "code": "005380", "why": "SELL", "amount": None,
                     "created": "2026-11-11"}],
        "history": [{"name": "LG화학", "code": "051910", "opened": "2026-11-02", "closed": "2026-11-06",
                     "units": 2, "ret": 0.0321}],
    }
    snap = {"asof": "2026-11-11", "mkt_on": True, "last_run": "2026-11-11 16:00",
            "rows": [{"코드": "005930", "종목": "삼성전자", "종가": 71000, "%K": 15.2, "%D": 12.1, "신호": "BUY"},
                     {"코드": "000660", "종목": "SK하이닉스", "종가": 180000, "%K": 50, "%D": 55, "신호": ""}]}
    k200 = pd.Series([35000.0, 35100.0, 35300.0, 35200.0],
                     index=pd.to_datetime(["2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11"]))
    return state, snap, k200


def test_build_writes_index_html(tmp_path):
    state, snap, k200 = _fake()
    out = dashboard.build(state, snap, k200, tmp_path / "dash")
    assert out == tmp_path / "dash" / "index.html"
    page = out.read_text(encoding="utf-8")
    for sec in ("점검", "보유 종목", "내일 시가 주문(대기)", "오늘 신호", "실전 vs 백테스트 기대치", "최근 청산"):
        assert sec in page, sec
    assert "삼성전자" in page and "LG화학" in page and "+3.21%" in page
    assert "10,120,000원" in page                 # 평가자산 타일
    assert "ON · 매수 가능" in page
    assert "&lt;SK하이닉스&gt;" in page and "<SK하이닉스>" not in page   # HTML 이스케이프
    assert '"d": "2026-11-11"' in page             # 차트 데이터


def test_build_empty_state(tmp_path):
    state = {"capital0": 1_000_000, "cash": 1_000_000, "equity_log": [], "positions": {},
             "pending": [], "history": []}
    snap = {"asof": "2026-11-11", "mkt_on": False, "rows": []}
    out = dashboard.build(state, snap, pd.Series(dtype=float), tmp_path)
    page = out.read_text(encoding="utf-8")
    assert "OFF · 신규매수 중지" in page
    assert "없음" in page


def test_health_flags_negative_cash_and_stale_data():
    state, snap, _ = _fake()
    state["cash"] = -1
    hl = dashboard.health(state, snap, today=pd.Timestamp("2026-11-30"))
    grades = {name: g for g, name, _ in hl}
    assert grades["현금"] == "critical"
    assert grades["데이터 최신성"] == "critical"
