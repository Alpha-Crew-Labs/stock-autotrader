r"""매일 장 마감 후 실행(16:10~): v6 멀티마켓(KR·US) 신호 -> 장부 갱신 -> 내일 아침 주문(대기) 생성

모드(config.json mode): paper = 모의 장부 시가 체결 / mock·live = 증권사 잔고 맞춤(reconcile, 읽기 전용)
아침 주문 전송은 run_orders.py. 손실 한도 초과 시 KILL_SWITCH 자동 생성(mock·live).

흐름 (v6 기본값: pullback_v6 + E3_atr + clenow + inverse_vol)
 1) 지난 실행에서 만든 '대기 주문'을 오늘 시가로 체결 처리 (모의 장부)
 2) 관심종목 + 보유종목의 일봉으로 신호 계산 (strategy.py 단일 진실 공급원)
    - 신규 매수(v6): 정배열(Close>SMA200 & SMA50>SMA200) + 단기 눌림목(RSI2<10 | (IBS<0.20 & Close<SMA5))
      * config.json 에서 entry_mode='stoch_v5' 로 기존 스토캐스틱+주봉 규칙 전환 가능
    - 후보 순위(v6): Clenow90(90일 회귀추세*R^2) 높은 순
    - 포지션 사이징(v6): 역변동성(target_risk=0.016, 단건 상한 10%)
    - 매도(v6 E3_atr): 이익 중 20일선 이탈 / 손실 중 -2.5*ATR14 또는 200일선 이탈
 3) 내일 시가에 낼 대기 주문 생성(같은 날 재실행 중복 방지) -> 원자적 저장
 4) 리포트 + dashboard/index.html (+ 선택적 Telegram 요약)

실행: python run_daily.py [--market KR|US] [--top N] [--capital 금액] [--check] [--no-notify]
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
BUY_COST, SELL_COST = 0.00015, 0.00015 + 0.0020   # KR 실매매 기본 비용(슬리피지 제외)
_ACTIVE_MARKET = CFG["strategy"].get("market", "KR")

MARKET_CFG = {
    "KR": {"bench": "069500", "bench_name": "KODEX 200", "buy_cost": 0.00015, "sell_cost": 0.00215, "currency": "원", "capital": 10_000_000},
    "US": {"bench": "SPY", "bench_name": "SPY", "buy_cost": 0.0005, "sell_cost": 0.0005, "currency": "$", "capital": 100_000},
}

# 시장·전략별 백테스트 기대치 (universe200 / US 우량주 65, 2016~2026)
EXPECT_BY_MODE = {
    ("KR", "E3_atr"): {"승률%": 59.9, "거래당 평균%": 2.96, "PF": 1.56, "평균 보유일": 21.2},
    ("US", "E3_atr"): {"승률%": 61.2, "거래당 평균%": 1.48, "PF": 1.69, "평균 보유일": 19.3},
    ("KR", "E3"): {"승률%": 38.1, "거래당 평균%": 1.27, "PF": 1.42, "평균 보유일": 11.1},
    ("US", "E3"): {"승률%": 42.1, "거래당 평균%": 0.18, "PF": 1.11, "평균 보유일": 11.0},
    ("KR", "E2"): {"승률%": 30.5, "거래당 평균%": 0.28, "PF": 1.08, "평균 보유일": 11.5},
    ("KR", "E4"): {"승률%": 38.1, "거래당 평균%": 1.27, "PF": 1.42, "평균 보유일": 11.1},
    ("KR", "E0"): {"승률%": 35.3, "거래당 평균%": -0.05, "PF": 0.98, "평균 보유일": 8.0},
}


def load_watchlist(top: int) -> dict[str, str]:
    if _ACTIVE_MARKET == "US":
        for name in ("watchlist_us.csv", "us_bluechips.csv"):
            f = HERE / name
            if f.exists():
                w = pd.read_csv(f, dtype={"Code": str}).head(top)
                return dict(zip(w["Code"].str.strip(), w["Name"].str.strip()))
        raise SystemExit("미국 관심종목 없음: us_bluechips.csv 또는 watchlist_us.csv 를 확인하세요.")

    f = HERE / "watchlist.csv"
    if f.exists():
        w = pd.read_csv(f, dtype={"Code": str})
        return dict(zip(w["Code"].str.zfill(6), w["Name"]))
    c = sorted((HERE / "watchlist").glob("candidates_*.csv"))
    if not c:
        raise SystemExit("관심종목 없음: watchlist.csv 를 만들거나 screener.py 를 먼저 실행하세요.")
    w = pd.read_csv(c[-1], dtype={"Code": str}).head(top)
    return dict(zip(w["Code"].str.zfill(6), w["Name"]))


def load_state(capital: float, path: Path | None = None) -> dict:
    p = path or STATE
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"capital0": capital, "cash": capital, "positions": {}, "pending": [], "history": []}


def save_state(s: dict, path: Path | None = None) -> None:
    settings.atomic_write_json(path or STATE, s, indent=1)


def prices(code: str, days: int = 420) -> pd.DataFrame:
    return market_data.load_prices(code, days=days)


def fill_pending(
    s: dict,
    px_cache: dict,
    log: list[str],
    buy_cost: float | None = None,
    sell_cost: float | None = None,
) -> None:
    bc = BUY_COST if buy_cost is None else buy_cost
    sc = SELL_COST if sell_cost is None else sell_cost
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
            qty = math.floor(o["amount"] / (op * (1 + bc)))
            if qty <= 0 or qty * op * (1 + bc) > s["cash"]:
                log.append(f"- 체결 실패(현금/수량 부족): {o['name']} {o['why']}")
                continue
            cost = qty * op * (1 + bc)
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
                    "entry_px": op,
                    "entry_atr": float(o.get("entry_atr") or 0.0),
                    "max_high": op,
                }
            pos["qty"] += qty
            pos["cost"] += cost
            pos["units"] += 1
            pos["last_buy"] = str(day.date())
            pos["last_price"] = op
            log.append(f"- 매수 체결 {day.date()} {o['name']} {qty}주 @{op:,.2f} ({o['why']})")
        elif pos:
            proceeds = pos["qty"] * op * (1 - sc)
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
            log.append(f"- 매도 체결 {day.date()} {o['name']} @{op:,.2f} 수익률 {100*r:+.2f}%")
    s["pending"] = keep


def reconcile(
    s: dict,
    broker,
    asof: pd.Timestamp,
    px_cache: dict,
    log: list[str],
    buy_cost: float | None = None,
    sell_cost: float | None = None,
) -> None:
    """mock·live: 증권사 잔고를 진실로 장부를 맞춘다. 전략 메타(매수횟수·진입일)는 장부 것을 유지."""
    bc = BUY_COST if buy_cost is None else buy_cost
    sc = SELL_COST if sell_cost is None else sell_cost
    h = broker.holdings()
    day = str(asof.date())
    sub = {(o["code"], o["side"]): o for o in s["pending"] if o.get("status") == "submitted"}
    for code, b in h.items():
        pos = s["positions"].get(code)
        cost = b["qty"] * b["avg_price"] * (1 + bc)
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
                "entry_px": b["avg_price"],
                "entry_atr": float((o or {}).get("entry_atr") or 0.0),
                "max_high": b["avg_price"],
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
        r = (pos["qty"] * op * (1 - sc) / pos["cost"] - 1) if pos["cost"] > 0 else 0.0
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


def run_check(cfg: dict, market: str = "KR") -> int:
    """상태 변경 없이 설정·키 존재 여부·KILL_SWITCH·장부 상태를 진단한다."""
    from dashboard import health

    local_cfg = settings.load_local_config(HERE / "config.local.json")
    strat = cfg["strategy"]
    kill = settings.kill_reason()
    state_path = (HERE / "state" / "paper_us.json") if (market == "US" and STATE == HERE / "state" / "paper.json") else STATE
    print("=== 주식 자동매매 상태 점검 (--check) ===")
    print(f"- 시장/모드: {market} / {cfg['mode']} (live.enabled={bool(cfg['live'].get('enabled'))})")
    print(f"- 전략(v6): entry={strat['entry_mode']}, exit={strat['exit_mode']}, rank={strat['rank_by']}, "
          f"sizing={strat['sizing']}(risk={strat['target_risk']}, cap={strat['max_pos_weight']}), units={cfg['units']}, max_pos={cfg['max_positions']}")
    print(f"- 키 설정(존재 여부): dart_api_key={bool(local_cfg.get('dart_api_key'))}, "
          f"kiwoom_mock={bool(local_cfg.get('kiwoom_mock_appkey'))}, "
          f"kiwoom_live={bool(local_cfg.get('kiwoom_appkey'))}, "
          f"telegram={bool(local_cfg.get('telegram_bot_token') and local_cfg.get('telegram_chat_id'))}")
    print(f"- KILL_SWITCH: {kill or '없음(정상)'}")

    if not state_path.exists():
        print(f"- 장부({state_path.name}): 없음 (아직 실행 전)")
        return 2 if kill else 0

    s = load_state(cfg["capital"], path=state_path)
    snap_path = state_path.with_name(state_path.stem + "_snapshot.json")
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
    global _ACTIVE_MARKET
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=("KR", "US"), default=None, help="실행 시장(KR 또는 US)")
    ap.add_argument("--top", type=int, default=CFG["watchlist_top"])
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--check", action="store_true", help="장부/설정/키 존재 여부 읽기 전용 진단")
    ap.add_argument("--no-notify", action="store_true", help="텔레그램 알림 전송 생략")
    a = ap.parse_args()

    cfg = CFG
    strat = cfg["strategy"]
    market = (a.market or strat.get("market", "KR")).upper()
    _ACTIVE_MARKET = market

    if a.check:
        sys.exit(run_check(cfg, market=market))

    m_info = MARKET_CFG[market]
    cap0 = float(a.capital if a.capital is not None else (cfg["capital"] if market == "KR" else m_info["capital"]))
    buy_cost, sell_cost = m_info["buy_cost"], m_info["sell_cost"]
    cur = m_info["currency"]

    state_path = (HERE / "state" / "paper_us.json") if (market == "US" and STATE == HERE / "state" / "paper.json") else STATE
    reports_dir = (HERE / "reports_us") if (market == "US" and REPORTS == HERE / "reports") else REPORTS
    dash_dir = (HERE / "dashboard_us") if (market == "US" and DASH == HERE / "dashboard") else DASH

    max_pos = int(cfg["max_positions"])
    units = int(cfg["units"])
    entry_mode = strat.get("entry_mode", "pullback_v6")
    exit_mode = strat.get("exit_mode", "E3_atr")
    rank_by = strat.get("rank_by", "clenow")
    sizing = strat.get("sizing", "inverse_vol")
    target_risk = float(strat.get("target_risk", 0.016))
    max_pos_weight = float(strat.get("max_pos_weight", 0.10))
    atr_stop_mult = float(strat.get("atr_stop_mult", 2.5))
    hold_add = bool(strat.get("hold_add_under20", False))
    eval_units = 1 if (entry_mode == "pullback_v6" or sizing == "inverse_vol") else units

    s = load_state(cap0, path=state_path)
    wl = load_watchlist(a.top)
    px, log = {}, []
    bench_df = prices(m_info["bench"])
    mkt_on = bool(bench_df["Close"].iloc[-1] > bench_df["Close"].rolling(200).mean().iloc[-1])
    bench_ret60 = float(bench_df["Close"].iloc[-1] / bench_df["Close"].iloc[-61] - 1) if len(bench_df) >= 61 else 0.0
    asof = bench_df.index[-1]
    asof_str = str(asof.date())

    if cfg["mode"] == "paper" or market == "US":
        fill_pending(s, px, log, buy_cost=buy_cost, sell_cost=sell_cost)
    else:
        from broker import get_broker
        reconcile(s, get_broker(cfg, read_only=True), asof, px, log, buy_cost=buy_cost, sell_cost=sell_cost)

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
                log.append(f"- ⚠ 보유종목 시세 오류({name}): 직전가 {fallback_px:,.2f}{cur} 기준 평가 유지")
            continue

        last = f.iloc[-1]
        stale = f.index[-1] < asof
        if pos:
            cur_px = float(last["Close"])
            pos["last_price"] = cur_px
            pos["max_high"] = max(float(pos.get("max_high") or 0.0), float(last["High"]))
            if not pos.get("entry_atr") and pd.notna(last.get("ATR14")):
                pos["entry_atr"] = float(last["ATR14"])
            equity += pos["qty"] * cur_px

        ret60 = float(last["RET60"]) if pd.notna(last.get("RET60")) else None
        rs60 = round((1 + ret60) / max(1 + bench_ret60, 1e-6), 3) if ret60 is not None else None
        clenow90 = round(float(last["CLENOW90"]), 3) if pd.notna(last.get("CLENOW90")) else None
        vol20 = float(last["VOL20"]) if pd.notna(last.get("VOL20")) else None
        rsi2 = round(float(last["RSI2"]), 1) if pd.notna(last.get("RSI2")) else None
        ibs = round(float(last["IBS"]), 2) if pd.notna(last.get("IBS")) else None
        atr14 = float(last["ATR14"]) if pd.notna(last.get("ATR14")) else 0.0
        held_days = len(pd.bdate_range(pos["opened"], asof)) - 1 if pos else None
        pos_status = ""
        sig = ""

        if stale:
            sig = "데이터 지연"
        elif pos:
            ev = strategy.evaluate_position(
                pos,
                f,
                exit_mode=exit_mode,
                units=eval_units,
                sell_cost=sell_cost,
                hold_add_under20=hold_add,
                atr_stop_mult=atr_stop_mult,
            )
            pos["hit80"] = ev["hit80"]
            pos["above20"] = ev["above20"]
            pos["max_high"] = ev["max_high"]
            pos_status = ev["status"]
            sig = ev["sig"]
            if ev["action"] == "sell" and (code, "sell") not in existing_pend:
                new_orders.append({"side": "sell", "code": code, "name": name, "why": ev["why"]})
                existing_pend.add((code, "sell"))
            elif ev["action"] == "add" and (code, "buy") not in existing_pend:
                unit_amt = pos.get("unit_amt", (equity if equity > 0 else cap0) / max_pos / units)
                new_orders.append({"side": "buy", "code": code, "name": name, "amount": unit_amt, "why": ev["why"]})
                existing_pend.add((code, "buy"))
        else:
            _, sig = strategy.check_entry(last, mkt_on=mkt_on, entry_mode=entry_mode)

        close_disp = int(round(float(last["Close"]))) if market == "KR" else round(float(last["Close"]), 2)
        rows.append({
            "코드": code,
            "종목": name,
            "종가": close_disp,
            "%K": round(float(last["K"]), 1),
            "%D": round(float(last["D"]), 1),
            "RSI2": rsi2,
            "IBS": ibs,
            "Ret60%": round(ret60 * 100, 1) if ret60 is not None else None,
            "RS60": rs60,
            "Clenow90": clenow90,
            "VOL20": vol20,
            "ATR14": atr14,
            "주봉OK": bool(last["WOK"]),
            "추세OK": bool(last["TREND"]),
            "정배열": bool(pd.notna(last.get("SMA50")) and pd.notna(last.get("SMA200")) and last["Close"] > last["SMA200"] and last["SMA50"] > last["SMA200"]),
            "보유": "Y" if pos else "",
            "보유일": held_days,
            "청산상태": pos_status,
            "신호": sig,
        })

    # 신규 매수: 빈 슬롯만큼, strategy.rank_by 기준 (v6: Clenow90 높은 순)
    pend_new_buys = sum(
        1 for o in s["pending"] if o.get("status") is None and o["side"] == "buy" and o["code"] not in s["positions"]
    )
    slots = max_pos - len(s["positions"]) - pend_new_buys
    cands = strategy.rank_candidates([r for r in rows if r.get("신호") == "신규매수"], rank_by=rank_by)
    base_eq = equity if equity > 0 else cap0
    placed = 0
    for r in cands:
        if placed >= max(slots, 0):
            r["신호"] += "(슬롯 없음)"
            continue
        if (r["코드"], "buy") in existing_pend:
            continue
        if sizing == "inverse_vol":
            amt = strategy.inverse_vol_amount(base_eq, r.get("VOL20"), target_risk=target_risk, max_pos_weight=max_pos_weight)
            why_lbl = f"BUY invVol"
        else:
            amt = (base_eq / max_pos) / units
            why_lbl = f"BUY 1/{units}"
        new_orders.append({
            "side": "buy",
            "code": r["코드"],
            "name": r["종목"],
            "amount": round(amt, 2),
            "entry_atr": round(float(r.get("ATR14") or 0.0), 4),
            "why": why_lbl,
        })
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
        "equity": round(equity, 2 if market == "US" else 0),
        "cash": round(s["cash"], 2 if market == "US" else 0),
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
    save_state(s, path=state_path)

    reports_dir.mkdir(exist_ok=True)
    tbl = pd.DataFrame(rows)
    hot = tbl[tbl["신호"].fillna("") != ""] if "신호" in tbl else tbl
    hist = pd.DataFrame(s["history"])
    md = [
        f"# 일일 신호 {asof:%Y-%m-%d} ({market})",
        f"- 모드: **{cfg['mode']}** · 전략: **{entry_mode} / {exit_mode} / {rank_by} / {sizing}**"
        + (f" / ⛔ KILL_SWITCH: {kill}" if kill else ""),
        f"- 시장 필터({m_info['bench_name']} > 200일선): **{'ON' if mkt_on else 'OFF'}**",
        f"- 평가자산 {equity:,.2f}{cur} (초기 {s['capital0']:,.2f}, {100*(equity/s['capital0']-1):+.2f}%) / 현금 {s['cash']:,.2f}{cur} / 보유 {len(s['positions'])}종목",
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
        *([f"- {p['name']}({c}) {p['qty']}주, 매수 {p['units']}회, 평단 {p['cost']/p['qty']:,.2f}" for c, p in s["positions"].items()] or ["- 없음"]),
        "",
        f"## 청산 누적: {len(hist)}건" + (f", 승률 {100*(hist['ret']>0).mean():.0f}%, 평균 {100*hist['ret'].mean():+.2f}%" if len(hist) else ""),
    ]
    out = reports_dir / f"daily_{asof:%Y%m%d}.md"
    out.write_text("\n".join(md), encoding="utf-8")

    expect = EXPECT_BY_MODE.get((market, exit_mode)) or EXPECT_BY_MODE.get(("KR", exit_mode)) or EXPECT_BY_MODE[("KR", "E3_atr")]
    snap = {
        "asof": asof_str,
        "market": market,
        "benchmark": m_info["bench_name"],
        "currency": cur,
        "mkt_on": mkt_on,
        "mode": cfg["mode"],
        "kill": kill,
        "max_positions": max_pos,
        "units": eval_units,
        "entry_mode": entry_mode,
        "exit_mode": exit_mode,
        "rank_by": rank_by,
        "sizing": sizing,
        "hold_add_under20": hold_add,
        "expect": expect,
        "rows": rows,
        "new_orders": new_orders,
        "log": log,
        "last_run": s["last_run"],
    }
    settings.atomic_write_json(state_path.with_name(state_path.stem + "_snapshot.json"), snap, indent=None, default=str)
    from dashboard import build
    md.append(f"\n[대시보드] {build(s, snap, bench_df['Close'], dash_dir)}")

    if not a.no_notify and state_path.name in ("paper.json", "paper_us.json"):
        notify.send_telegram(notify.format_daily_summary(snap, equity, s["capital0"], len(s["positions"]), kill))

    print("\n".join(md))
    print(f"\n[저장] {out}")


if __name__ == "__main__":
    main()
