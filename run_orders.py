r"""아침 주문 (평일 08:30~08:55): 어제 장 마감 후 run_daily.py 가 만든 대기주문을 증권사로 전송

- paper 모드: 아무것도 보내지 않음 (모의 장부는 오후 run_daily.py 가 시가로 체결 처리)
- mock 모드 : 키움 모의투자 서버로 시장가 주문 (장전 동시호가 -> 시가 체결)
- live 모드 : config.json live.enabled=true + --live 플래그가 모두 있어야 실전 주문

안전장치 (하나라도 걸리면 주문 0건)
- KILL_SWITCH 파일 존재 (손실 한도 초과 시 run_daily.py 가 자동 생성 / 사람이 만들어도 됨)
- 주말, 이미 오늘 주문 전송함(중복 방지)
- 1건 상한 limits.max_order_krw, 하루 매수 합계 상한 limits.max_daily_buy_krw, 주문가능현금 초과분은 잘라냄
매도는 증권사 잔고의 매도가능수량 기준 (장부와 다르면 잔고가 우선)

실행: python run_orders.py [--dry-run] [--live]
결과: state/orders_YYYYMMDD.json, state/paper.json 의 대기주문에 status=submitted 표시
"""
from pathlib import Path
import argparse, json, math
import datetime as dt
import FinanceDataReader as fdr
import settings
from broker import get_broker

HERE = Path(__file__).parent
STATE = HERE / "state" / "paper.json"


def prev_close(code):
    d = fdr.DataReader(code, (dt.date.today() - dt.timedelta(days=15)).isoformat())
    return float(d["Close"].iloc[-1])


def plan_orders(state, holdings, cash, cfg, price_fn=prev_close):
    """대기주문 -> 실제 주문 목록 [(side, code, name, qty, why)] + 건너뛴 사유. (테스트 가능하도록 분리)"""
    lim = cfg["limits"]
    orders, skipped, buy_total = [], [], 0.0
    pend = [o for o in state.get("pending", []) if o.get("status") is None]
    for o in [p for p in pend if p["side"] == "sell"]:
        h = holdings.get(o["code"])
        if not h or h["sellable"] <= 0:
            skipped.append(f"매도 생략 {o['name']}: 증권사 잔고 없음")
            continue
        orders.append(("sell", o["code"], o["name"], h["sellable"], o["why"]))
    for o in [p for p in pend if p["side"] == "buy"]:
        px = price_fn(o["code"])
        amt = min(o["amount"], lim["max_order_krw"], lim["max_daily_buy_krw"] - buy_total, cash - buy_total)
        qty = math.floor(amt / (px * 1.003)) if px > 0 and amt > 0 else 0   # 시가 갭 여유 0.3%
        if qty <= 0:
            skipped.append(f"매수 생략 {o['name']}: 한도/현금 부족 (요청 {o['amount']:,.0f}원)")
            continue
        buy_total += qty * px
        orders.append(("buy", o["code"], o["name"], qty, o["why"]))
    return orders, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="주문을 보내지 않고 계획만 출력")
    ap.add_argument("--live", action="store_true", help="실전 주문 확인 플래그 (config live.enabled 도 필요)")
    a = ap.parse_args()
    cfg = settings.load()
    today = dt.date.today()
    log_f = HERE / "state" / f"orders_{today:%Y%m%d}.json"

    if cfg["mode"] == "paper" and not a.dry_run:
        print("paper 모드: 증권사 주문 없음 (모의 장부는 오후 run_daily.py 가 처리)")
        return
    if (r := settings.kill_reason()):
        print(f"[중지] KILL_SWITCH: {r}  -> 주문 0건. 해제하려면 KILL_SWITCH 파일 삭제")
        return
    if today.weekday() >= 5:
        print("주말: 주문 없음")
        return
    if log_f.exists() and not a.dry_run:
        print(f"오늘 이미 주문 전송함: {log_f.name} (중복 방지)")
        return
    if not STATE.exists():
        print("state/paper.json 없음: run_daily.py 를 먼저 실행")
        return

    state = json.loads(STATE.read_text(encoding="utf-8"))
    broker = get_broker(cfg, live_flag=a.live, dry_run=a.dry_run)
    holdings = broker.holdings() if not a.dry_run else {
        c: {"name": p["name"], "qty": p["qty"], "sellable": p["qty"], "avg_price": p["cost"] / max(p["qty"], 1)}
        for c, p in state["positions"].items()}
    cash = broker.orderable_cash() if not a.dry_run else state["cash"]
    orders, skipped = plan_orders(state, holdings, cash, cfg)

    print(f"[{broker.name}] 모드 {cfg['mode']}, 주문가능현금 {cash:,.0f}원, 주문 {len(orders)}건")
    results = []
    for side, code, name, qty, why in orders:
        try:
            res = broker.buy(code, qty) if side == "buy" else broker.sell(code, qty)
            ok = True
        except Exception as e:  # 한 건 실패가 나머지를 막지 않도록
            res, ok = {"error": f"{type(e).__name__}: {e}"}, False
        results.append({"side": side, "code": code, "name": name, "qty": qty, "why": why, "ok": ok, "result": res})
        print(f"  {'✓' if ok else '✗'} {side} {name}({code}) {qty}주 — {why}")
    for s in skipped:
        print("  -", s)

    if not a.dry_run:
        for o in state["pending"]:
            hit = next((r for r in results if r["code"] == o["code"] and r["side"] == o["side"]), None)
            if hit and hit["ok"]:
                o.update(status="submitted", qty=hit["qty"], submitted=dt.datetime.now().isoformat(timespec="seconds"))
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        log_f.write_text(json.dumps({"mode": cfg["mode"], "broker": broker.name, "cash": cash, "orders": results,
                                     "skipped": skipped}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
