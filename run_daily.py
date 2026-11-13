r"""매일 장 마감 후 실행: 형님 스토캐스틱 v2 신호 -> 모의 장부(페이퍼 트레이딩) -> (선택) 키움 모의주문

흐름
 1) 지난 실행에서 만든 '대기 주문'을 오늘 시가로 체결 처리 (모의 장부)
 2) 관심종목 + 보유종목의 일봉으로 신호 계산 (backtest_hyung2.prep 그대로 사용 = 백테스트와 같은 규칙)
    - 신규 매수: BUY 신호 + 시장 필터(KODEX200 > 200일선) + 종목 필터(200일선 위, 120일 수익률 > 0)
                 + 주봉 필터(직전 주봉 스토캐스틱 %K > %D) — v3, backtest_hyung3.py 근거
    - 추가 매수: ADD 신호, 최대 3회, 직전 매수 후 5거래일 경과
    - 전량 매도: SELL 신호
 3) 내일 시가에 낼 대기 주문 생성 -> state/paper.json 저장
 4) --kiwoom 이면 키움 모의투자로 같은 주문 전송 (kiwoom_client.py, 기본 모의 서버)
 5) 리포트: reports/daily_YYYYMMDD.md

관심종목: watchlist.csv(Code,Name; 직접 관리)가 있으면 그것, 없으면 최신 watchlist/candidates_*.csv 상위 N
실행: python run_daily.py [--top 30] [--kiwoom] [--capital 10000000]
"""
from pathlib import Path
import argparse, json, math
import datetime as dt
import pandas as pd
import FinanceDataReader as fdr
from backtest_hyung2 import prep, UNITS
from backtest_hyung3 import weekly_ok   # v3: 주봉 스토캐스틱 상승 중일 때만 신규 매수

HERE = Path(__file__).parent
STATE = HERE / "state" / "paper.json"
REPORTS = HERE / "reports"
DASH = HERE / "dashboard"
MAX_POS = 10
BUY_COST, SELL_COST = 0.00015, 0.00015 + 0.0020   # 실매매 비용(슬리피지 제외)


def load_watchlist(top):
    f = HERE / "watchlist.csv"
    if f.exists():
        w = pd.read_csv(f, dtype={"Code": str})
        return dict(zip(w["Code"].str.zfill(6), w["Name"]))
    c = sorted((HERE / "watchlist").glob("candidates_*.csv"))
    if not c:
        raise SystemExit("관심종목 없음: watchlist.csv 를 만들거나 screener.py 를 먼저 실행하세요.")
    w = pd.read_csv(c[-1], dtype={"Code": str}).head(top)
    return dict(zip(w["Code"].str.zfill(6), w["Name"]))


def load_state(capital):
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"capital0": capital, "cash": capital, "positions": {}, "pending": [], "history": []}


def save_state(s):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def prices(code, days=420):
    start = (dt.date.today() - dt.timedelta(days=int(days * 1.5))).isoformat()
    df = fdr.DataReader(code, start)[["Open", "High", "Low", "Close", "Volume"]]
    return df[df["Volume"] > 0]


def fill_pending(s, px_cache, log):
    keep = []
    for o in s["pending"]:
        df = px_cache.get(o["code"])
        if df is None:
            df = px_cache[o["code"]] = prices(o["code"])
        after = df[df.index > pd.Timestamp(o["created"])]
        if after.empty:
            keep.append(o)
            continue
        day, op = after.index[0], float(after["Open"].iloc[0])
        pos = s["positions"].get(o["code"])
        if o["side"] == "buy":
            qty = math.floor(o["amount"] / (op * (1 + BUY_COST)))
            if qty <= 0 or qty * op * (1 + BUY_COST) > s["cash"]:
                log.append(f"- 체결 실패(현금/수량 부족): {o['name']} {o['why']}")
                continue
            cost = qty * op * (1 + BUY_COST)
            s["cash"] -= cost
            if pos is None:
                pos = s["positions"][o["code"]] = {"name": o["name"], "qty": 0, "cost": 0.0, "units": 0,
                                                   "unit_amt": o["amount"], "opened": str(day.date())}
            pos["qty"] += qty
            pos["cost"] += cost
            pos["units"] += 1
            pos["last_buy"] = str(day.date())
            log.append(f"- 매수 체결 {day.date()} {o['name']} {qty}주 @{op:,.0f} ({o['why']})")
        elif pos:
            proceeds = pos["qty"] * op * (1 - SELL_COST)
            s["cash"] += proceeds
            r = proceeds / pos["cost"] - 1
            s["history"].append({"code": o["code"], "name": pos["name"], "opened": pos["opened"],
                                 "closed": str(day.date()), "units": pos["units"], "ret": round(r, 4)})
            del s["positions"][o["code"]]
            log.append(f"- 매도 체결 {day.date()} {o['name']} @{op:,.0f} 수익률 {100*r:+.2f}%")
    s["pending"] = keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--capital", type=float, default=10_000_000)
    ap.add_argument("--kiwoom", action="store_true", help="키움 모의투자로 주문 전송")
    a = ap.parse_args()

    s = load_state(a.capital)
    wl = load_watchlist(a.top)
    px, log = {}, []
    fill_pending(s, px, log)

    k200 = prices("069500")
    mkt_on = bool(k200["Close"].iloc[-1] > k200["Close"].rolling(200).mean().iloc[-1])
    asof = k200.index[-1]

    codes = dict(wl)
    codes.update({c: p["name"] for c, p in s["positions"].items()})
    rows, new_orders = [], []
    equity = s["cash"]
    for code, name in codes.items():
        try:
            df = px.get(code)
            if df is None:
                df = px[code] = prices(code)
            if len(df) < 250:
                raise ValueError(f"일봉 {len(df)}개(250개 미만)")
            f = prep(df)
            f["WOK"] = weekly_ok(f)
        except Exception as e:
            rows.append({"코드": code, "종목": name, "메모": f"데이터 오류 {e}"})
            continue
        last = f.iloc[-1]
        stale = f.index[-1] < asof
        pos = s["positions"].get(code)
        if pos:
            equity += pos["qty"] * float(last["Close"])
        sig = ""
        if stale:
            sig = "데이터 지연"
        elif pos:
            since = (f.index > pd.Timestamp(pos["last_buy"])).sum()
            if last["SELL"]:
                sig = "매도"
                new_orders.append({"side": "sell", "code": code, "name": name, "why": "SELL 신호"})
            elif last["ADD"] and pos["units"] < UNITS and since >= 5:
                sig = "추가매수"
                new_orders.append({"side": "buy", "code": code, "name": name, "amount": pos["unit_amt"],
                                   "why": f"ADD {pos['units']+1}/{UNITS}"})
        elif last["BUY"]:
            if not mkt_on:
                sig = "매수신호(시장필터 OFF)"
            elif not last["TREND"]:
                sig = "매수신호(종목필터 탈락)"
            elif not last["WOK"]:
                sig = "매수신호(주봉 하락 중)"
            else:
                sig = "신규매수"
        rows.append({"코드": code, "종목": name, "종가": int(last["Close"]), "%K": round(last["K"], 1),
                     "%D": round(last["D"], 1), "추세OK": bool(last["TREND"]), "보유": "Y" if pos else "",
                     "신호": sig})

    # 신규 매수: 빈 슬롯만큼, %K 낮은 순
    slots = MAX_POS - len(s["positions"]) - sum(1 for o in s["pending"] if o["side"] == "buy" and o["code"] not in s["positions"])
    cands = sorted([r for r in rows if r.get("신호") == "신규매수"], key=lambda r: r["%K"])
    alloc = a.capital / MAX_POS
    for r in cands[:max(slots, 0)]:
        new_orders.append({"side": "buy", "code": r["코드"], "name": r["종목"], "amount": alloc / UNITS,
                           "why": "BUY 1/3"})
    for r in cands[max(slots, 0):]:
        r["신호"] += "(슬롯 없음)"
    for o in new_orders:
        o["created"] = str(asof.date())
    s["pending"] += new_orders
    s["last_run"] = dt.datetime.now().isoformat(timespec="seconds")
    s.setdefault("equity_log", [])
    s["equity_log"] = [e for e in s["equity_log"] if e["date"] != str(asof.date())]
    s["equity_log"].append({"date": str(asof.date()), "equity": round(equity), "cash": round(s["cash"]),
                            "n_pos": len(s["positions"]), "mkt_on": mkt_on})

    if a.kiwoom and new_orders:
        try:
            from kiwoom_client import KiwoomClient
            kc = KiwoomClient(mock=True)
            for o in new_orders:
                if o["side"] == "buy":
                    qty = math.floor(o["amount"] / int(next(r["종가"] for r in rows if r["코드"] == o["code"])))
                    res = kc.buy(o["code"], qty) if qty > 0 else "수량 0"
                else:
                    res = kc.sell(o["code"], s["positions"][o["code"]]["qty"])
                log.append(f"- 키움 모의주문 {o['side']} {o['name']}: {res}")
        except Exception as e:
            log.append(f"- 키움 모의주문 생략: {e}")
    save_state(s)

    REPORTS.mkdir(exist_ok=True)
    tbl = pd.DataFrame(rows)
    hot = tbl[tbl["신호"].fillna("") != ""] if "신호" in tbl else tbl
    hist = pd.DataFrame(s["history"])
    md = [f"# 일일 신호 {asof:%Y-%m-%d}",
          f"- 시장 필터(KODEX200 > 200일선): **{'ON' if mkt_on else 'OFF'}**",
          f"- 평가자산 {equity:,.0f}원 (초기 {s['capital0']:,.0f}, {100*(equity/s['capital0']-1):+.2f}%) / 현금 {s['cash']:,.0f}원 / 보유 {len(s['positions'])}종목",
          "", "## 오늘 체결(모의)", *(log or ["- 없음"]),
          "", "## 내일 시가 주문(대기)", *([f"- {o['side']} {o['name']}({o['code']}) {o['why']}" for o in new_orders] or ["- 없음"]),
          "", "## 신호 종목", hot.to_markdown(index=False) if len(hot) else "- 없음",
          "", "## 보유", *([f"- {p['name']}({c}) {p['qty']}주, 매수 {p['units']}회, 평단 {p['cost']/p['qty']:,.0f}" for c, p in s["positions"].items()] or ["- 없음"]),
          "", f"## 청산 누적: {len(hist)}건" + (f", 승률 {100*(hist['ret']>0).mean():.0f}%, 평균 {100*hist['ret'].mean():+.2f}%" if len(hist) else "")]
    out = REPORTS / f"daily_{asof:%Y%m%d}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    snap = {"asof": str(asof.date()), "mkt_on": mkt_on, "rows": rows, "new_orders": new_orders,
            "log": log, "last_run": s["last_run"]}
    STATE.with_name(STATE.stem + "_snapshot.json").write_text(
        json.dumps(snap, ensure_ascii=False, default=str), encoding="utf-8")
    from dashboard import build
    md.append(f"\n[대시보드] {build(s, snap, k200['Close'], DASH)}")
    print("\n".join(md))
    print(f"\n[저장] {out}")


if __name__ == "__main__":
    main()
