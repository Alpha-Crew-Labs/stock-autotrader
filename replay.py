r"""run_daily.py 를 과거 날짜로 하루씩 다시 돌려 모의 장부 로직 검증 (미래 데이터 차단)

실행: python replay.py [시작일=2026-01-02] [--top 50]
결과: state/replay.json (장부), reports_replay/ (일별 리포트), dashboard_replay/index.html
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd

import run_daily as r

HERE = Path(__file__).parent
start = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "2026-01-02"
top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 50
univ_arg = sys.argv[sys.argv.index("--universe") + 1] if "--universe" in sys.argv else ""

full = {}


def cut_prices(code, days=420):
    if code not in full:
        local = HERE / "data" / f"{code}.csv"
        if local.exists():
            df = pd.read_csv(local, index_col=0, parse_dates=True)[["Open", "High", "Low", "Close", "Volume"]]
        else:
            df = fdr.DataReader(code, "2024-01-01")[["Open", "High", "Low", "Close", "Volume"]]
        full[code] = df[df["Volume"] > 0]
    return full[code][full[code].index <= TODAY].copy()


r.prices = cut_prices
r.STATE = HERE / "state" / "replay.json"
r.REPORTS = HERE / "reports_replay"
r.DASH = HERE / "dashboard_replay"
if r.STATE.exists():
    r.STATE.unlink()

u_path = Path(univ_arg) if univ_arg else (
    HERE / "data" / "universe200.csv" if (HERE / "data" / "universe200.csv").exists() else HERE / "data" / "universe.csv"
)
u = pd.read_csv(u_path, dtype={"Code": str}).head(top)
r.load_watchlist = lambda t: dict(zip(u["Code"].str.zfill(6), u["Name"]))

TODAY = pd.Timestamp.today()
cal = cut_prices("069500").index
cal = cal[cal >= pd.Timestamp(start)]
sys.argv = ["run_daily.py", "--top", str(top), "--no-notify"]
for d in cal:
    TODAY = d
    with contextlib.redirect_stdout(io.StringIO()):
        r.main()

s = r.load_state(0)
h = pd.DataFrame(s["history"])
eq = s["cash"] + sum(p["qty"] * float(full[c]["Close"].iloc[-1]) for c, p in s["positions"].items())
print(f"리플레이 {cal[0].date()} ~ {cal[-1].date()} ({len(cal)}거래일), 유니버스 {u_path.name} 상위 {len(u)}개")
print(f"평가자산 {eq:,.0f}원 ({100*(eq/s['capital0']-1):+.2f}%), 현금 {s['cash']:,.0f}, 보유 {len(s['positions'])}종목, 대기주문 {len(s['pending'])}건")
if len(h):
    print(f"청산 {len(h)}건, 승률 {100*(h['ret']>0).mean():.0f}%, 평균 {100*h['ret'].mean():+.2f}%, 물타기 포함 {int((h['units']>1).sum())}건")
    print(h.tail(10).to_string(index=False))
for c, p in s["positions"].items():
    print("보유:", p["name"], p["qty"], "주", p["units"], "회 매수")
