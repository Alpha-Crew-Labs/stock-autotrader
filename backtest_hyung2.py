r"""형님 기법 v2 — 일봉, 물타기(최대 3회), 손절 없음, 종목·시장 필터

스토캐스틱(14,5,5)
매수(1차): 최근 5일 안에 %K<20 이었고 %K<30 이면서
   (a) 예상형: %K<%D, 두 선 간격이 3일 연속 좁아지고 오늘 %K 상승  또는
   (b) 확인형: %K가 %D를 상향 교차
추가매수: 보유 중 %K<20 이고 %D-%K 간격이 2일 연속 벌어짐, 직전 매수 후 5일 경과, 최대 3회(1회 = 종목 배정금의 1/3)
매도(전량): (a) 예상형: %K>%D, 간격 3일 연속 좁아지고 오늘 %K 하락  또는  (b) %K가 %D를 하향 교차
신호는 종가로 판단, 다음 날 시가 체결. 손절 없음.
필터(형님의 "유망 종목 / 하락장·비주목 산업 제외"를 기계적으로 대신):
   시장: KODEX 200 종가 > 200일선 일 때만 신규 매수
   종목: 종가 > 200일선 이고 최근 120일 수익률 > 0 일 때만 신규 매수
비용: 매수 0.065%, 매도 0.265%(거래세 0.20% 포함)
실행: python backtest_hyung2.py
"""
from pathlib import Path
import numpy as np
import pandas as pd
import backtest as bt
from backtest_hyung import stoch

HERE = Path(__file__).parent
OUT = HERE / "results"
UNITS = 3


def prep(df):
    df = stoch(df.copy())
    K, D = df["K"], df["D"]
    gap = (K - D).abs()
    narrow3 = (gap < gap.shift()) & (gap.shift() < gap.shift(2)) & (gap.shift(2) < gap.shift(3))
    near_os = (K.rolling(5).min() < 20) & (K < 30)
    gc = (K > D) & (K.shift() <= D.shift())
    dc = (K < D) & (K.shift() >= D.shift())
    df["BUY"] = near_os & (((K < D) & narrow3 & (K > K.shift())) | gc)
    wid2 = ((D - K) > (D - K).shift()) & ((D - K).shift() > (D - K).shift(2))
    df["ADD"] = (K < 20) & (K < D) & wid2
    df["SELL"] = ((K > D) & narrow3 & (K < K.shift())) | dc
    df["TREND"] = (df["Close"] > df["Close"].rolling(200).mean()) & (df["Close"].pct_change(120) > 0)
    return df


def sim(df, mkt_ok, use_mkt, use_stock):
    o, c = df["Open"].values, df["Close"].values
    buy = df["BUY"].values.copy()
    if use_mkt:
        buy &= mkt_ok.reindex(df.index).fillna(False).values
    if use_stock:
        buy &= df["TREND"].values
    add, sell = df["ADD"].values, df["SELL"].values
    n = len(df)
    cash, sh, units, last_buy, unit_amt, cost_basis, t0 = 1.0, 0.0, 0, -99, 0.0, 0.0, 0
    eq, trades = np.ones(n), []
    for i in range(1, n):
        if units and sell[i - 1]:
            proceeds = sh * o[i] * (1 - bt.SELL_COST)
            trades.append((df.index[t0], df.index[i], proceeds / cost_basis - 1, i - t0, units))
            cash += proceeds
            sh, units = 0.0, 0
        elif units == 0 and buy[i - 1]:
            unit_amt = cash / UNITS
            sh = unit_amt / (o[i] * (1 + bt.BUY_COST))
            cash -= unit_amt
            units, last_buy, cost_basis, t0 = 1, i, unit_amt, i
        elif 0 < units < UNITS and add[i - 1] and i - last_buy >= 5:
            amt = min(unit_amt, cash)
            sh += amt / (o[i] * (1 + bt.BUY_COST))
            cash -= amt
            units, last_buy, cost_basis = units + 1, i, cost_basis + amt
        eq[i] = cash + sh * c[i]
    if units:
        trades.append((df.index[t0], df.index[-1], sh * c[-1] * (1 - bt.SELL_COST) / cost_basis - 1, n - 1 - t0, units))
    return trades, pd.Series(eq, index=df.index)


def main():
    u = bt.universe()
    kodex = pd.read_csv(bt.DATA / "069500.csv", index_col=0, parse_dates=True)
    mkt_ok = kodex["Close"] > kodex["Close"].rolling(200).mean()
    frames = {c: (nm, prep(bt.prices(c))) for c, nm in zip(u["Code"], u["Name"])}
    idx = pd.DatetimeIndex(sorted(set().union(*[d.index for _, d in frames.values()])))
    rows, log = [], []

    def perf(eqs):
        port = pd.concat([e.reindex(idx).ffill().fillna(1.0) for e in eqs], axis=1).mean(axis=1)
        yrs = (idx[-1] - idx[0]).days / 365.25
        return round(100 * (port.iloc[-1] ** (1 / yrs) - 1), 2), round(100 * (port / port.cummax() - 1).min(), 1), port

    for label, um, us in (("필터 없음", False, False), ("시장 필터", True, False),
                          ("종목 필터", False, True), ("시장+종목 필터", True, True)):
        tr_all, eqs = [], []
        for code, (nm, df) in frames.items():
            tr, e = sim(df, mkt_ok, um, us)
            tr_all += tr
            eqs.append(e)
            log += [(label, code, nm, *x) for x in tr]
        cagr, mdd, port = perf(eqs)
        t = np.array([x[2] for x in tr_all])
        un = np.array([x[4] for x in tr_all])
        rows.append({"구분": label, "CAGR%": cagr, "MDD%": mdd, "거래수": len(t),
                     "승률%": round(100 * (t > 0).mean(), 1), "평균%": round(100 * t.mean(), 2),
                     "중앙값%": round(100 * np.median(t), 2), "최악%": round(100 * t.min(), 1),
                     "PF": round(t[t > 0].sum() / -t[t < 0].sum(), 2),
                     "평균보유일": round(np.mean([x[3] for x in tr_all]), 1),
                     "물타기비율%": round(100 * (un > 1).mean(), 1)})
        port.to_csv(OUT / f"equity_hyung2_{label}.csv", encoding="utf-8-sig")
    bh = [d["Close"] / d["Close"].iloc[0] for _, d in frames.values()]
    cagr, mdd, _ = perf(bh)
    rows.append({"구분": "그냥 보유(기준)", "CAGR%": cagr, "MDD%": mdd})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "summary_hyung2.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(log, columns=["구분", "코드", "종목", "진입", "청산", "수익률", "보유일", "매수횟수"]) \
        .to_csv(OUT / "trades_hyung2.csv", index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(res.to_string(index=False))


if __name__ == "__main__":
    main()
