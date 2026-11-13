r"""형님 기법 v3 후보 — 스토캐스틱만으로 개선 (v2 + 시장·종목 필터 기준)

L 매도 늦추기 : 평가이익 중이면 %K >= 50 에서 나온 매도 신호만 따름 (낮은 구간 데드크로스는 버팀).
               평가손실이면 기존대로 바로 매도(원금 보호).
W 주봉 필터   : 직전 완성 주봉의 스토캐스틱(14,5,5) %K > %D (주봉 상승 흐름) 일 때만 신규 매수
S 매수 금액   : 1회 매수 = 배정금의 1/3(기존) / 1/2 / 전액
실행: python backtest_hyung3.py
"""
from pathlib import Path
import numpy as np
import pandas as pd
import backtest as bt
from backtest_hyung import stoch
from backtest_hyung2 import prep

HERE = Path(__file__).parent


def weekly_ok(df):
    w = df.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    w = stoch(w)
    ok = (w["K"] > w["D"]).shift(1)          # 직전 완성 주봉 기준 (미래 데이터 차단)
    return ok.reindex(df.index, method="ffill").fillna(False).astype(bool)


def sim(df, mkt_ok, units, late_sell, use_weekly):
    o, c, K = df["Open"].values, df["Close"].values, df["K"].values
    buy = df["BUY"].values & mkt_ok.reindex(df.index).fillna(False).values & df["TREND"].values
    if use_weekly:
        buy &= df["WOK"].values
    add, sell = df["ADD"].values, df["SELL"].values
    n = len(df)
    cash, sh, u, last_buy, unit_amt, basis, t0 = 1.0, 0.0, 0, -99, 0.0, 0.0, 0
    eq, trades = np.ones(n), []
    for i in range(1, n):
        if u:
            s = sell[i - 1]
            if s and late_sell and sh * c[i - 1] > basis and K[i - 1] < 50:
                s = False                       # 이익 중 + 낮은 구간 데드크로스 -> 버팀
            if s:
                pr = sh * o[i] * (1 - bt.SELL_COST)
                trades.append((pr / basis - 1, i - t0, u))
                cash += pr
                sh, u = 0.0, 0
            elif u < units and add[i - 1] and i - last_buy >= 5:
                amt = min(unit_amt, cash)
                sh += amt / (o[i] * (1 + bt.BUY_COST))
                cash -= amt
                u, last_buy, basis = u + 1, i, basis + amt
        elif buy[i - 1]:
            unit_amt = cash / units
            sh = unit_amt / (o[i] * (1 + bt.BUY_COST))
            cash -= unit_amt
            u, last_buy, basis, t0 = 1, i, unit_amt, i
        eq[i] = cash + sh * c[i]
    if u:
        trades.append((sh * c[-1] * (1 - bt.SELL_COST) / basis - 1, n - 1 - t0, u))
    return trades, pd.Series(eq, index=df.index)


def main():
    u = bt.universe()
    k = pd.read_csv(bt.DATA / "069500.csv", index_col=0, parse_dates=True)["Close"]
    mkt_ok = k > k.rolling(200).mean()
    frames = {}
    for c in u["Code"]:
        df = prep(bt.prices(c))
        df["WOK"] = weekly_ok(df)
        frames[c] = df
    idx = pd.DatetimeIndex(sorted(set().union(*[d.index for d in frames.values()])))
    yrs = (idx[-1] - idx[0]).days / 365.25
    cases = [("v2 기준 (1/3씩)", 3, False, False),
             ("S 1/2씩", 2, False, False), ("S 전액", 1, False, False),
             ("L 매도 늦추기", 3, True, False), ("W 주봉 필터", 3, False, True),
             ("L+W", 3, True, True), ("L+W+S 1/2", 2, True, True), ("L+W+S 전액", 1, True, True)]
    rows = []
    for name, units, ls, wk in cases:
        tr, eqs = [], []
        for df in frames.values():
            t, e = sim(df, mkt_ok, units, ls, wk)
            tr += t
            eqs.append(e.reindex(idx).ffill().fillna(1.0))
        port = pd.concat(eqs, axis=1).mean(axis=1)
        r = np.array([x[0] for x in tr])
        rows.append({"구분": name, "CAGR%": round(100 * (port.iloc[-1] ** (1 / yrs) - 1), 2),
                     "MDD%": round(100 * (port / port.cummax() - 1).min(), 1), "거래수": len(r),
                     "승률%": round(100 * (r > 0).mean(), 1), "평균%": round(100 * r.mean(), 2),
                     "PF": round(r[r > 0].sum() / -r[r < 0].sum(), 2),
                     "평균보유일": round(np.mean([x[1] for x in tr]), 1),
                     "물타기%": round(100 * np.mean([x[2] > 1 for x in tr]), 1)})
    bh = pd.concat([d["Close"].reindex(idx).ffill().bfill() / d["Close"].iloc[0] for d in frames.values()], axis=1).mean(axis=1)
    rows.append({"구분": "그냥 보유(기준)", "CAGR%": round(100 * (bh.iloc[-1] ** (1 / yrs) - 1), 2),
                 "MDD%": round(100 * (bh / bh.cummax() - 1).min(), 1)})
    res = pd.DataFrame(rows)
    res.to_csv(HERE / "results" / "summary_hyung3.csv", index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(res.to_string(index=False))


if __name__ == "__main__":
    main()
