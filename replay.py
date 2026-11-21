r"""run_daily.py 를 과거 날짜로 하루씩 다시 돌려 모의 장부 로직 검증 (미래 데이터 차단)

실행:
  python replay.py [시작일=2026-01-02] [--top 50] [--market KR|US]
결과: state/replay.json (또는 state/replay_us.json), dashboard_replay(_us)/index.html
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import pandas as pd

import market_data
import run_daily as r

HERE = Path(__file__).parent
start = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "2026-01-02"
market = sys.argv[sys.argv.index("--market") + 1].upper() if "--market" in sys.argv else "KR"
top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else (65 if market == "US" else 50)
univ_arg = sys.argv[sys.argv.index("--universe") + 1] if "--universe" in sys.argv else ""

full = {}


def cut_prices(code, days=420):
    if code not in full:
        if market == "US":
            local = HERE / "data_us" / f"{code}.csv"
        else:
            local = HERE / "data" / f"{code}.csv"
        if local.exists():
            df = pd.read_csv(local, index_col=0, parse_dates=True)[["Open", "High", "Low", "Close", "Volume"]]
        else:
            df = market_data.fetch_ohlcv(code, "2024-01-01")
        full[code] = df[df["Volume"] > 0]
    return full[code][full[code].index <= TODAY].copy()


r.prices = cut_prices
if market == "US":
    r.STATE = HERE / "state" / "replay_us.json"
    r.REPORTS = HERE / "reports_replay_us"
    r.DASH = HERE / "dashboard_replay_us"
    u_path = Path(univ_arg) if univ_arg else HERE / "us_bluechips.csv"
    bench_code = "SPY"
else:
    r.STATE = HERE / "state" / "replay.json"
    r.REPORTS = HERE / "reports_replay"
    r.DASH = HERE / "dashboard_replay"
    u_path = Path(univ_arg) if univ_arg else (
        HERE / "data" / "universe200.csv" if (HERE / "data" / "universe200.csv").exists() else HERE / "data" / "universe.csv"
    )
    bench_code = "069500"

if r.STATE.exists():
    r.STATE.unlink()

u = pd.read_csv(u_path, dtype={"Code": str}).head(top)
r.load_watchlist = lambda t: dict(zip(u["Code"].str.strip(), u["Name"].str.strip()))

TODAY = pd.Timestamp.today()
cal = cut_prices(bench_code).index
cal = cal[cal >= pd.Timestamp(start)]
sys.argv = ["run_daily.py", "--market", market, "--top", str(top), "--no-notify"]
for d in cal:
    TODAY = d
    with contextlib.redirect_stdout(io.StringIO()):
        r.main()

s = r.load_state(0)
h = pd.DataFrame(s["history"])
cp = s.get("cash_park") or {}
cp_qty = int(cp.get("qty") or 0)
cp_code = cp.get("code")
cp_val = 0.0
if cp_qty > 0 and cp_code:
    TODAY = cal[-1]
    cp_df = cut_prices(cp_code)
    if len(cp_df):
        cp_val = cp_qty * float(cp_df["Close"].iloc[-1])
eq = s["cash"] + cp_val + sum(p["qty"] * float(full[c][full[c].index <= cal[-1]]["Close"].iloc[-1]) for c, p in s["positions"].items())
cur = "$" if market == "US" else "원"
cp_txt = f", 현금파킹({cp_code} {cp_qty}주, {cp_val:,.2f}{cur})" if cp_qty > 0 else ""
print(f"리플레이({market}) {cal[0].date()} ~ {cal[-1].date()} ({len(cal)}거래일), 유니버스 {u_path.name} {len(u)}개")
print(f"평가자산 {eq:,.2f}{cur} ({100*(eq/s['capital0']-1):+.2f}%), 현금 {s['cash']:,.2f}{cp_txt}, 보유 {len(s['positions'])}종목, 대기주문 {len(s['pending'])}건")
if len(h):
    print(f"청산 {len(h)}건, 승률 {100*(h['ret']>0).mean():.0f}%, 평균 {100*h['ret'].mean():+.2f}%")
    print(h.tail(10).to_string(index=False))
for c, p in s["positions"].items():
    print("보유:", p["name"], p["qty"], "주")
