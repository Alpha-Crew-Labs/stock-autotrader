r"""매일 장 마감 후 실행(16:10~): 스토캐스틱 v5(E3+RS60) 신호 -> 장부 갱신 -> 내일 아침 주문(대기) 생성

모드(config.json mode): paper = 모의 장부를 시가로 체결 처리 / mock·live = 증권사 잔고로 장부를 맞춤(reconcile, 읽기 전용)
아침 주문 전송은 run_orders.py. 손실 한도 초과 시 KILL_SWITCH 자동 생성(mock·live).

흐름
 1) 지난 실행에서 만든 '대기 주문'을 오늘 시가로 체결 처리 (모의 장부)
 2) 관심종목 + 보유종목의 일봉으로 신호 계산 (strategy.py 단일 진실 공급원)
    - 신규 매수: BUY 신호 + 시장 필터(KODEX200 > 200일선) + 종목 필터(200일선 위, 120일 수익률 > 0)
                 + 주봉 필터(직전 주봉 스토캐스틱 %K > %D)
    - 후보 순위: strategy.rank_by (v5 기본: rs60 = 60일 상대강도 높은 순)
    - 추가 매수: ADD 신호, 최대 3회, 직전 매수 후 5거래일 경과
    - 전량 매도: strategy.evaluate_position (v5 기본 E3: 이익 중 20일선 이탈 / 손실 중 데드크로스)
 3) 내일 시가에 낼 대기 주문 생성(같은 날 재실행 중복 방지) -> state/paper.json 원자적 저장
 4) (mock·live) 다음 날 아침 run_orders.py 가 대기주문을 증권사로 전송
 5) 리포트: reports/daily_YYYYMMDD.md + dashboard/index.html (+ 선택적 Telegram 요약)

관심종목: watchlist.csv(Code,Name; 직접 관리)가 있으면 그것, 없으면 최신 watchlist/candidates_*.csv 상위 N
실행: python run_daily.py [--top N] [--capital 원] [--check] [--no-notify]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

import pandas as pd

import market_data
import notify
import settings
import strategy

CFG = settings.load()

HERE = Path(__file__).parent
STATE = HERE / "state" / "paper.json"
REPORTS = HERE / "reports"
DASH = HERE / "dashboard"
MAX_POS = CFG["max_positions"]
BUY_COST, SELL_COST = 0.00015, 0.00015 + 0.0020   # 실매매 비용(슬리피지 제외)

# 전략별 백테스트 기대치 (universe200, 2016~2026)
EXPECT_BY_MODE = {
    "E3": {"승률%": 38.1, "거래당 평균%": 1.27, "PF": 1.42, "평균 보유일": 11.1},
    "E2": {"승률%": 30.5, "거래당 평균%": 0.28, "PF": 1.08, "평균 보유일": 11.5},
    "E4": {"승률%": 38.1, "거래당 평균%": 1.27, "PF": 1.42, "평균 보유일": 11.1},
    "E0": {"승률%": 35.3, "거래당 평균%": -0.05, "PF": 0.98, "평균 보유일": 8.0},
}


def load_watchlist(top: int) -> dict[str, str]:
    f = HERE / "watchlist.csv"
    if f.exists():
        w = pd.read_csv(f, dtype={"Code": str})
        return dict(zip(w["Code"].str.zfill(6), w["Name"]))
    c = sorted((HERE / "watchlist").glob("candidates_*.csv"))
    if not c:
        raise SystemExit("관심종목 없음: watchlist.csv 를 만들거나 screener.py 를 먼저 실행하세요.")
    w = pd.read_csv(c[-1], dtype={"Code": str}).head(top)
    return dict(zip(w["Code"].str.zfill(6), w["Name"]))


def load_state(capital: float) -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"capital0": capital, "cash": capital, "positions": {}, "pending": [], "history": []}


def save_state(s: dict) -> None:
    settings.atomic_write_json(STATE, s, indent=1)


def prices(code: str, days: int = 420) -> pd.DataFrame:
    return market_data.load_prices(code, days=days)


def fill_pending(s: dict, px_cache: dict, log: list[str]) -> None:
    keep = []
    for o in s["pending"]:
        df = px_cache.get(o["code"])
        if df is None:
            df = px_cache[o["code"]] = prices(o["code"])
        if df is None or df.empty:
            keep.append(o)
            continue
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
                pos = s["positions"][o["code"]] = {
                    "name": o["name"],
                    "qty": 0,
                    "cost": 0.0,
                    "units": 0,
                    "unit_amt": o["amount"],
                    "opened": str(day.date()),
                    "hit80": False,
                    "above20": False,
                }
            pos["qty"] += qty
            pos["cost"] += cost
            pos["units"] += 1
            pos["last_buy"] = str(day.date())
            pos["last_price"] = op
            log.append(f"- 매수 체결 {day.date()} {o['name']} {qty}주 @{op:,.0f} ({o['why']})")
        elif pos:
            proceeds = pos["qty"] * op * (1 - SELL_COST)
            s["cash"] += proceeds
            r = (proceeds / pos["cost"] - 1) if pos["cost"] > 0 else 0.0
            s["history"].append({
                "code": o["code"],
                "name": pos["name"],
                "opened": pos["opened"],
                "closed": str(day.date()),
                "units": pos["units"],
                "ret": round(r, 4),
                "why": o.get("why", ""),
            })
            del s["positions"][o["code"]]
            log.append(f"- 매도 체결 {day.date()} {o['name']} @{op:,.0f} 수익률 {100*r:+.2f}%")
    s["pending"] = keep


def reconcile(s: dict, broker, asof: pd.Timestamp, px_cache: dict, log: list[str]) -> None:
    """mock·live: 증권사 잔고를 진실로 장부를 맞춘다. 전략 메타(매수횟수·진입일)는 장부 것을 유지."""
    h = broker.holdings()
    day = str(asof.date())
    sub = {(o["code"], o["side"]): o for o in s["pending"] if o.get("status") == "submitted"}
    for code, b in h.items():
        pos = s["positions"].get(code)
        cost = b["qty"] * b["avg_price"] * (1 + BUY_COST)
        if pos is None:
            o = sub.get((code, "buy"))
            s["positions"][code] = {
                "name": b["name"],
                "qty": b["qty"],
                "cost": cost,
                "units": 1,
                "unit_amt": (o or {}).get("amount", cost),
                "opened": day,
                "last_buy": day,
                "hit80": False,
                "above20": False,
                "last_price": b["avg_price"],
            }
            log.append(f"- 매수 체결 확인 {b['name']} {b['qty']}주" + ("" if o else " (장부에 없던 보유 — 수동 매매?)"))
        elif b["qty"] > pos["qty"]:
            pos.update(qty=b["qty"], cost=cost, units=pos["units"] + 1, last_buy=day)
            log.append(f"- 추가매수 체결 확인 {b['name']} → {b['qty']}주")
        elif b["qty"] != pos["qty"]:
            pos.update(qty=b["qty"], cost=cost)
            log.append(f"- 수량 변경 {b['name']} → {b['qty']}주 (부분 체결/수동 매매)")
    for code in [c for c in s["positions"] if c not in h]:
        pos = s["positions"].pop(code)
        df = px_cache.get(code)
        if df is None:
            df = px_cache[code] = prices(code)
        if df is not None and not df.empty:
            op = float(df["Open"].iloc[-1])
        else:
            op = float(pos.get("last_price") or (pos["cost"] / max(pos["qty"], 1)))
        r = (pos["qty"] * op * (1 - SELL_COST) / pos["cost"] - 1) if pos["cost"] > 0 else 0.0
        sub_sell = sub.get((code, "sell"))
        s["history"].append({
            "code": code,
            "name": pos["name"],
            "opened": pos["opened"],
            "closed": day,
            "units": pos["units"],
            "ret": round(r, 4),
            "est": True,
            "why": (sub_sell or {}).get("why", "증권사 청산 확인"),
        })
        log.append(f"- 매도 체결 확인 {pos['name']} (시가 기준 추정 수익률 {100*r:+.2f}%)")
    for (code, side), o in sub.items():
        filled = (side == "buy" and code in h) or (side == "sell" and code not in h)
        if not filled:
            log.append(f"- 미체결 {side} {o['name']} — 오늘 신호로 다시 판단")
    s["pending"] = []
    s["cash"] = broker.orderable_cash()


def run_check(cfg: dict) -> int:
    """상태 변경 없이 설정·키 존재 여부·KILL_SWITCH·장부 상태를 진단한다."""
    from dashboard import health

    local_cfg = settings.load_local_config(HERE / "config.local.json")
    strat = cfg["strategy"]
    kill = settings.kill_reason()
    print("=== 주식 자동매매 상태 점검 (--check) ===")
    print(f"- 모드: {cfg['mode']} (live.enabled={bool(cfg['live'].get('enabled'))})")
    print(f"- 전략: exit_mode={strat['exit_mode']}, rank_by={strat['rank_by']}, units={cfg['units']}, max_pos={cfg['max_positions']}, hold_add_under20={strat['hold_add_under20']}")
    print(f"- 키 설정(존재 여부): dart_api_key={bool(local_cfg.get('dart_api_key'))}, "
          f"kiwoom_mock={bool(local_cfg.get('kiwoom_mock_appkey'))}, "
          f"kiwoom_live={bool(local_cfg.get('kiwoom_appkey'))}, "
          f"telegram={bool(local_cfg.get('telegram_bot_token') and local_cfg.get('telegram_chat_id'))}")
    print(f"- KILL_SWITCH: {kill or '없음(정상)'}")

    if not STATE.exists():
        print("- 장부(state/paper.json): 없음 (아직 실행 전)")
        return 2 if kill else 0

    s = load_state(cfg["capital"])
    snap_path = STATE.with_name(STATE.stem + "_snapshot.json")
    snap = json.loads(snap_path.read_text(encoding="utf-8")) if snap_path.exists() else {
        "asof": s.get("last_run", str(dt.date.today()))[:10],
        "rows": [],
        "kill": kill,
    }
    snap["kill"] = kill
    hl = health(s, snap)
    for grade, item, desc in hl:
        print(f"  [{grade.upper()}] {item}: {desc}")
    return 2 if any(g == "critical" for g, _, _ in hl) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=CFG["watchlist_top"])
    ap.add_argument("--capital", type=float, default=CFG["capital"])
    ap.add_argument("--check", action="store_true", help="장부/설정/키 존재 여부 읽기 전용 진단")
    ap.add_argument("--no-notify", action="store_true", help="텔레그램 알림 전송 생략")
    a = ap.parse_args()

    cfg = CFG
    if a.check:
        sys.exit(run_check(cfg))

    max_pos = int(cfg["max_positions"])
    units = int(cfg["units"])
    strat = cfg["strategy"]
    exit_mode = strat["exit_mode"]
    rank_by = strat["rank_by"]
    hold_add = bool(strat["hold_add_under20"])

    s = load_state(a.capital)
    wl = load_watchlist(a.top)
    px, log = {}, []
    k200 = prices("069500")
    mkt_on = bool(k200["Close"].iloc[-1] > k200["Close"].rolling(200).mean().iloc[-1])
    k200_ret60 = float(k200["Close"].iloc[-1] / k200["Close"].iloc[-61] - 1) if len(k200) >= 61 else 0.0
    asof = k200.index[-1]
    asof_str = str(asof.date())

    if cfg["mode"] == "paper":
        fill_pending(s, px, log)
    else:
        from broker import get_broker
        reconcile(s, get_broker(cfg, read_only=True), asof, px, log)

    # 같은 날 재실행 시 미전송 당일 대기주문 중복 방지 (멱등성 보장)
    s["pending"] = [
        o for o in s.get("pending", [])
        if not (o.get("status") is None and o.get("created") == asof_str)
    ]
    existing_pend = {(o["code"], o["side"]) for o in s["pending"] if o.get("status") is None}

    codes = dict(wl)
    codes.update({c: p["name"] for c, p in s["positions"].items()})
    rows, new_orders = [], []
    equity = s["cash"]
    for code, name in codes.items():
        pos = s["positions"].get(code)
        try:
            df = px.get(code)
            if df is None:
                df = px[code] = prices(code)
            if df is None or len(df) < 250:
                n_bars = 0 if df is None else len(df)
                raise ValueError(f"일봉 {n_bars}개(250개 미만)")
            f = strategy.prep(df)
            f["WOK"] = strategy.weekly_ok(f)
        except Exception as e:
            rows.append({"코드": code, "종목": name, "메모": f"데이터 오류 {e}"})
            if pos:
                fallback_px = float(pos.get("last_price") or (pos["cost"] / max(pos["qty"], 1)))
                equity += pos["qty"] * fallback_px
                log.append(f"- ⚠ 보유종목 시세 오류({name}): 직전가 {fallback_px:,.0f}원 기준 평가 유지")
            continue

        last = f.iloc[-1]
        stale = f.index[-1] < asof
        if pos:
            cur_px = float(last["Close"])
            pos["last_price"] = cur_px
            equity += pos["qty"] * cur_px

        ret60 = float(last["RET60"]) if pd.notna(last.get("RET60")) else None
        rs60 = round((1 + ret60) / max(1 + k200_ret60, 1e-6), 3) if ret60 is not None else None
        held_days = len(pd.bdate_range(pos["opened"], asof)) - 1 if pos else None
        pos_status = ""
        sig = ""

        if stale:
            sig = "데이터 지연"
        elif pos:
            ev = strategy.evaluate_position(
                pos, f, exit_mode=exit_mode, units=units, sell_cost=SELL_COST, hold_add_under20=hold_add
            )
            pos["hit80"] = ev["hit80"]
            pos["above20"] = ev["above20"]
            pos_status = ev["status"]
            sig = ev["sig"]
            if ev["action"] == "sell" and (code, "sell") not in existing_pend:
                new_orders.append({"side": "sell", "code": code, "name": name, "why": ev["why"]})
                existing_pend.add((code, "sell"))
            elif ev["action"] == "add" and (code, "buy") not in existing_pend:
                unit_amt = pos.get("unit_amt", (equity if equity > 0 else a.capital) / max_pos / units)
                new_orders.append({"side": "buy", "code": code, "name": name, "amount": unit_amt, "why": ev["why"]})
                existing_pend.add((code, "buy"))
        elif last["BUY"]:
            if not mkt_on:
                sig = "매수신호(시장필터 OFF)"
            elif not last["TREND"]:
                sig = "매수신호(종목필터 탈락)"
            elif not last["WOK"]:
                sig = "매수신호(주봉 하락 중)"
            else:
                sig = "신규매수"

        rows.append({
            "코드": code,
            "종목": name,
            "종가": int(last["Close"]),
            "%K": round(float(last["K"]), 1),
            "%D": round(float(last["D"]), 1),
            "Ret60%": round(ret60 * 100, 1) if ret60 is not None else None,
            "RS60": rs60,
            "주봉OK": bool(last["WOK"]),
            "추세OK": bool(last["TREND"]),
            "보유": "Y" if pos else "",
            "보유일": held_days,
            "청산상태": pos_status,
            "신호": sig,
        })

    # 신규 매수: 빈 슬롯만큼, strategy.rank_by 기준 (v5: RS60 높은 순)
    pend_new_buys = sum(
        1 for o in s["pending"] if o.get("status") is None and o["side"] == "buy" and o["code"] not in s["positions"]
    )
    slots = max_pos - len(s["positions"]) - pend_new_buys
    cands = strategy.rank_candidates([r for r in rows if r.get("신호") == "신규매수"], rank_by=rank_by)
    alloc = (equity if equity > 0 else a.capital) / max_pos
    placed = 0
    for r in cands:
        if placed >= max(slots, 0):
            r["신호"] += "(슬롯 없음)"
            continue
        if (r["코드"], "buy") in existing_pend:
            continue
        new_orders.append({"side": "buy", "code": r["코드"], "name": r["종목"], "amount": alloc / units, "why": f"BUY 1/{units}"})
        existing_pend.add((r["코드"], "buy"))
        placed += 1

    for o in new_orders:
        o["created"] = asof_str
    s["pending"] += new_orders
    s["last_run"] = dt.datetime.now().isoformat(timespec="seconds")
    s.setdefault("equity_log", [])
    s["equity_log"] = [e for e in s["equity_log"] if e["date"] != asof_str]
    s["equity_log"].append({
        "date": asof_str,
        "equity": round(equity),
        "cash": round(s["cash"]),
        "n_pos": len(s["positions"]),
        "mkt_on": mkt_on,
    })

    # 손실 한도 (mock·live 는 KILL_SWITCH 자동 생성 -> 아침 주문 중지)
    breach = settings.check_loss_limits(cfg, s["equity_log"])
    if breach:
        if cfg["mode"] != "paper":
            settings.trip_kill(breach)
        log.append(
            f"- ⚠ 손실 한도 초과: {breach}"
            + (" → KILL_SWITCH 생성, 내일 주문 중지" if cfg["mode"] != "paper" else " (paper 모드: 경고만)")
        )
    kill = settings.kill_reason()
    save_state(s)

    REPORTS.mkdir(exist_ok=True)
    tbl = pd.DataFrame(rows)
    hot = tbl[tbl["신호"].fillna("") != ""] if "신호" in tbl else tbl
    hist = pd.DataFrame(s["history"])
    md = [
        f"# 일일 신호 {asof:%Y-%m-%d}",
        f"- 모드: **{cfg['mode']}** · 전략: **{exit_mode} / {rank_by} / {units}분할**"
        + (f" / ⛔ KILL_SWITCH: {kill}" if kill else ""),
        f"- 시장 필터(KODEX200 > 200일선): **{'ON' if mkt_on else 'OFF'}**",
        f"- 평가자산 {equity:,.0f}원 (초기 {s['capital0']:,.0f}, {100*(equity/s['capital0']-1):+.2f}%) / 현금 {s['cash']:,.0f}원 / 보유 {len(s['positions'])}종목",
        "",
        "## 오늘 체결(모의)",
        *(log or ["- 없음"]),
        "",
        "## 내일 시가 주문(대기)",
        *([f"- {o['side']} {o['name']}({o['code']}) {o['why']}" for o in new_orders] or ["- 없음"]),
        "",
        "## 신호 종목",
        hot.to_markdown(index=False) if len(hot) else "- 없음",
        "",
        "## 보유",
        *([f"- {p['name']}({c}) {p['qty']}주, 매수 {p['units']}회, 평단 {p['cost']/p['qty']:,.0f}" for c, p in s["positions"].items()] or ["- 없음"]),
        "",
        f"## 청산 누적: {len(hist)}건" + (f", 승률 {100*(hist['ret']>0).mean():.0f}%, 평균 {100*hist['ret'].mean():+.2f}%" if len(hist) else ""),
    ]
    out = REPORTS / f"daily_{asof:%Y%m%d}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    snap = {
        "asof": asof_str,
        "mkt_on": mkt_on,
        "mode": cfg["mode"],
        "kill": kill,
        "max_positions": max_pos,
        "units": units,
        "exit_mode": exit_mode,
        "rank_by": rank_by,
        "hold_add_under20": hold_add,
        "expect": EXPECT_BY_MODE.get(exit_mode, EXPECT_BY_MODE["E3"]),
        "rows": rows,
        "new_orders": new_orders,
        "log": log,
        "last_run": s["last_run"],
    }
    settings.atomic_write_json(STATE.with_name(STATE.stem + "_snapshot.json"), snap, indent=None, default=str)
    from dashboard import build
    md.append(f"\n[대시보드] {build(s, snap, k200['Close'], DASH)}")

    if not a.no_notify and STATE.name == "paper.json":
        notify.send_telegram(notify.format_daily_summary(snap, equity, s["capital0"], len(s["positions"]), kill))

    print("\n".join(md))
    print(f"\n[저장] {out}")


if __name__ == "__main__":
    main()
