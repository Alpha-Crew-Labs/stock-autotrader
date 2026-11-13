r"""형님 스토캐스틱 기법 백테스트

세팅: 스토캐스틱 기간 14, %K 5, %D 5 (fast %K 14 -> 5일 평균 = %K, %K 5일 평균 = %D)
매수: %K가 20 아래로 내려간 뒤, %K가 %D를 상향 교차(골든크로스)하는 봉 -> 다음 봉 시가 매수
  - 엄격형: 교차 시점 %K < 20
  - 완화형: 최근 10봉 안에 %K < 20 이었고, 교차 시점 %K < 35
매도: %K가 %D를 하향 교차(데드크로스)하는 봉 -> 다음 봉 시가 매도 (80 도달 여부 무관)
일봉: 오버나이트 보유 / 5분봉: 당일 마지막 봉 종가 강제 청산, 14:30 이후 신규 진입 금지
비용: 수수료 0.015% + 슬리피지 0.05% (양방향), 매도 거래세 주식 0.20% / ETF 0%
실행: python backtest_hyung.py
"""
from pathlib import Path
import numpy as np
import pandas as pd
import FinanceDataReader as fdr
import backtest as bt
import backtest_5m as b5

HERE = Path(__file__).parent
OUT = HERE / "results"
ETFS = {"069500": "KODEX 200", "122630": "KODEX 레버리지",
        "229200": "KODEX 코스닥150", "233740": "KODEX 코스닥150레버리지"}


def stoch(df, n=14, k=5, d=5):
    lo, hi = df["Low"].rolling(n).min(), df["High"].rolling(n).max()
    df["K"] = (100 * (df["Close"] - lo) / (hi - lo).replace(0, np.nan)).rolling(k).mean()
    df["D"] = df["K"].rolling(d).mean()
    return df


def signals(df, mode):
    K, D = df["K"], df["D"]
    gc = (K > D) & (K.shift() <= D.shift())
    dc = (K < D) & (K.shift() >= D.shift())
    if mode == "엄격형":
        buy = gc & (K < 20)
    else:
        buy = gc & (K.rolling(10).min() < 20) & (K < 35)
    return buy.fillna(False), dc.fillna(False)


def sim(df, buy, sell, sell_cost, intraday):
    o, c = df["Open"].values, df["Close"].values
    b, s = buy.values, sell.values
    n = len(df)
    if intraday:
        day = df.index.normalize()
        last_bar = np.r_[day[1:] != day[:-1], True]
        late = (df.index.hour * 100 + df.index.minute) >= 1430
    else:
        last_bar = late = np.zeros(n, bool)
    ret = np.zeros(n)
    tr, pos, entry, ei = [], False, 0.0, 0
    for i in range(1, n):
        if pos:
            if s[i - 1]:
                px = o[i] * (1 - sell_cost)
                ret[i] = px / c[i - 1] - 1
                tr.append((df.index[ei], df.index[i], px / entry - 1, i - ei))
                pos = False
            else:
                ret[i] = c[i] / c[i - 1] - 1
                if last_bar[i]:
                    px = c[i] * (1 - sell_cost)
                    ret[i] = px / c[i - 1] - 1
                    tr.append((df.index[ei], df.index[i], px / entry - 1, i - ei))
                    pos = False
                    continue
        if not pos and b[i - 1] and not last_bar[i - 1] and not late[i - 1]:
            entry = o[i] * (1 + bt.BUY_COST)
            ret[i] = c[i] / entry - 1
            pos, ei = True, i
    if pos:
        tr.append((df.index[ei], df.index[-1], c[-1] * (1 - sell_cost) / entry - 1, n - 1 - ei))
    return tr, pd.Series(ret, index=df.index)


def row(label, tr, port=None, expo=None):
    t = np.array([x[2] for x in tr]) if tr else np.array([np.nan])
    r = {"구분": label, "거래수": len(tr), "승률%": round(100 * np.nanmean(t > 0), 1),
         "평균%": round(100 * np.nanmean(t), 3), "중앙값%": round(100 * np.nanmedian(t), 3),
         "최악%": round(100 * np.nanmin(t), 1),
         "PF": round(t[t > 0].sum() / -t[t < 0].sum(), 2) if (t < 0).any() else np.inf,
         "평균보유봉": round(np.mean([x[3] for x in tr]), 1) if tr else 0}
    if port is not None:
        eq = (1 + port).cumprod()
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        r["CAGR%"] = round(100 * (eq.iloc[-1] ** (1 / yrs) - 1), 2)
        r["MDD%"] = round(100 * (eq / eq.cummax() - 1).min(), 1)
        r["투자비중%"] = round(100 * expo, 1)
    return r


def run_set(label, frames, sell_cost, intraday, rows, log):
    for mode in ("엄격형", "완화형"):
        tr_all, rets, held = [], [], []
        for code, (name, df) in frames.items():
            b, s = signals(df, mode)
            tr, r = sim(df, b, s, sell_cost, intraday)
            tr_all += tr
            log += [(label, mode, code, name, *x) for x in tr]
            rets.append(r)
            held.append(r != 0)
        if intraday:
            rows.append(row(f"{label} / {mode}", tr_all))
        else:
            idx = pd.DatetimeIndex(sorted(set().union(*[d.index for _, d in frames.values()])))
            port = pd.concat([x.reindex(idx).fillna(0) for x in rets], axis=1).mean(axis=1)
            expo = pd.concat([x.reindex(idx, fill_value=False) for x in held], axis=1).mean(axis=1).mean()
            rows.append(row(f"{label} / {mode}", tr_all, port, expo))
    if not intraday:  # 기준: 동일비중 보유
        idx = pd.DatetimeIndex(sorted(set().union(*[d.index for _, d in frames.values()])))
        bh = pd.concat([d["Close"].pct_change().reindex(idx) for _, d in frames.values()], axis=1).fillna(0).mean(axis=1)
        eq = (1 + bh).cumprod()
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        rows.append({"구분": f"{label} / 그냥 보유(기준)", "CAGR%": round(100 * (eq.iloc[-1] ** (1 / yrs) - 1), 2),
                     "MDD%": round(100 * (eq / eq.cummax() - 1).min(), 1), "투자비중%": 100.0})


def main():
    u = bt.universe()
    stocks_d = {c: (nm, stoch(bt.prices(c).copy())) for c, nm in zip(u["Code"], u["Name"])}
    etf_d = {}
    for c, nm in ETFS.items():
        f = bt.DATA / f"{c}.csv"
        if not f.exists():
            fdr.DataReader(c, bt.START, bt.END)[["Open", "High", "Low", "Close", "Volume"]].to_csv(f)
        etf_d[c] = (nm, stoch(pd.read_csv(f, index_col=0, parse_dates=True)))
    stocks_5 = {}
    for c, nm in zip(u["Code"], u["Name"]):
        d = b5.load(c)
        if len(d) > 500:
            stocks_5[c] = (nm, stoch(d.copy()))
    etf_5 = {c: (nm, stoch(b5.load(c).copy())) for c, nm in ETFS.items()}

    rows, log = [], []
    run_set("일봉·주식50 (2016~)", stocks_d, bt.SELL_COST, False, rows, log)
    run_set("일봉·ETF4", etf_d, bt.BUY_COST, False, rows, log)
    run_set("5분봉·주식49 (60일)", stocks_5, bt.SELL_COST, True, rows, log)
    run_set("5분봉·ETF4 (60일)", etf_5, bt.BUY_COST, True, rows, log)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "summary_hyung.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(log, columns=["구분", "모드", "코드", "종목", "진입", "청산", "수익률", "보유봉"]) \
        .to_csv(OUT / "trades_hyung.csv", index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(res.to_string(index=False))


if __name__ == "__main__":
    main()
