r"""스토캐스틱 / RSI / MACD 조합 백테스트 (국내주식 일봉)

규칙: 장 마감 종가로 신호 판단 -> 다음 날 시가에 체결. 롱 온리, 종목당 1포지션.
비용: 수수료 0.015%(양방향) + 슬리피지 0.05%(양방향) + 매도 거래세 0.20%.
실행: python backtest.py  (결과는 results\ 에 저장)
"""
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import FinanceDataReader as fdr

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = HERE / "results"
START, END = "2016-01-01", "2026-11-12"
TOP_N = 50
BUY_COST = 0.00015 + 0.0005
SELL_COST = 0.00015 + 0.0005 + 0.0020


# ---------- 데이터 ----------
def universe(n=TOP_N):
    f = DATA / "universe.csv"
    if f.exists():
        return pd.read_csv(f, dtype={"Code": str})
    lst = fdr.StockListing("KOSPI")
    lst = lst[lst["Code"].str.endswith("0")]  # 우선주 제외
    u = lst.sort_values("Marcap", ascending=False).head(n)[["Code", "Name"]]
    DATA.mkdir(exist_ok=True)
    u.to_csv(f, index=False, encoding="utf-8-sig")
    return u


def prices(code):
    f = DATA / f"{code}.csv"
    if f.exists():
        return pd.read_csv(f, index_col=0, parse_dates=True)
    df = fdr.DataReader(code, START, END)[["Open", "High", "Low", "Close", "Volume"]]
    df = df[df["Volume"] > 0]
    df.to_csv(f)
    return df


# ---------- 지표 ----------
def add_indicators(df, k=14, ks=3, ds=3):
    lo, hi = df["Low"].rolling(k).min(), df["High"].rolling(k).max()
    fast = 100 * (df["Close"] - lo) / (hi - lo).replace(0, np.nan)
    df["K"] = fast.rolling(ks).mean()          # 슬로우 %K
    df["D"] = df["K"].rolling(ds).mean()       # 슬로우 %D
    d = df["Close"].diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = 100 - 100 / (1 + up / dn)
    macd = df["Close"].ewm(span=12, adjust=False).mean() - df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"], df["SIG"] = macd, macd.ewm(span=9, adjust=False).mean()
    df["HIST"] = df["MACD"] - df["SIG"]
    return df


# ---------- 전략 (매수신호, 매도신호) ----------
def signals(df, name):
    K, pK = df["K"], df["K"].shift()
    cross_up20 = (pK < 20) & (K >= 20)
    cross_dn80 = (pK > 80) & (K <= 80)
    hist_up = df["HIST"] > df["HIST"].shift()
    rsi_low = df["RSI"].rolling(5).min() < 40
    if name == "A0_스토캐스틱_단순":      # 20 아래면 매수, 80 위면 매도
        return K < 20, K > 80
    if name == "A_스토캐스틱_돌파":       # 20 상향돌파 매수, 80 하향돌파 매도
        return cross_up20, cross_dn80
    if name == "B_스토+MACD":             # A + MACD 히스토그램 상승
        return cross_up20 & hist_up, cross_dn80
    if name == "C_스토+MACD0위":          # A + MACD선 0 위(상승추세)
        return cross_up20 & (df["MACD"] > 0), cross_dn80
    if name == "D_스토+RSI+MACD":         # A + 최근5일 RSI<40 + MACD 히스토그램 상승
        return cross_up20 & rsi_low & hist_up, cross_dn80
    raise ValueError(name)


STRATS = ["A0_스토캐스틱_단순", "A_스토캐스틱_돌파", "B_스토+MACD", "C_스토+MACD0위", "D_스토+RSI+MACD"]


def run_one(df, buy, sell, stop):
    """종목 1개 시뮬레이션 -> (거래목록, 일별 수익률 시리즈)"""
    o, c = df["Open"].values, df["Close"].values
    b, s = buy.values, sell.values
    n = len(df)
    ret = np.zeros(n)
    trades, pos, entry, ei = [], False, 0.0, 0
    for i in range(1, n):
        if pos:
            # 손절: 장중 대신 종가 기준 판단 -> 다음 날 시가 청산
            exit_sig = s[i - 1] or (stop and c[i - 1] <= entry * (1 - stop))
            if exit_sig:
                px = o[i] * (1 - SELL_COST)
                ret[i] = px / c[i - 1] - 1
                trades.append((df.index[ei], df.index[i], px / entry - 1, i - ei))
                pos = False
                continue
            ret[i] = c[i] / c[i - 1] - 1
        elif b[i - 1]:
            entry = o[i] * (1 + BUY_COST)
            ret[i] = c[i] / entry - 1
            pos, ei = True, i
    if pos:  # 기간 끝 미청산은 종가 평가
        trades.append((df.index[ei], df.index[-1], c[-1] * (1 - SELL_COST) / entry - 1, n - 1 - ei))
    return trades, pd.Series(ret, index=df.index)


def stats(rets, trades, expo=None):
    eq = (1 + rets).cumprod()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    mdd = (eq / eq.cummax() - 1).min()
    if not trades:
        return {"CAGR%": round(100 * cagr, 2), "MDD%": round(100 * mdd, 1)}
    t = np.array([x[2] for x in trades])
    h = np.array([x[3] for x in trades])
    return {"거래수": len(trades), "승률%": round(100 * (t > 0).mean(), 1),
            "평균수익%": round(100 * np.nanmean(t), 2), "중앙값%": round(100 * np.nanmedian(t), 2),
            "최악%": round(100 * np.nanmin(t), 1), "평균보유일": round(np.nanmean(h), 1),
            "CAGR%": round(100 * cagr, 2), "MDD%": round(100 * mdd, 1),
            "투자비중%": "" if expo is None else round(100 * expo, 1)}


def main():
    OUT.mkdir(exist_ok=True)
    u = universe()
    data = {}
    for code, name in zip(u["Code"], u["Name"]):
        try:
            df = prices(code)
            if len(df) > 300:
                data[code] = (name, add_indicators(df.copy()))
        except Exception as e:
            print("skip", code, name, e, file=sys.stderr)
    idx = pd.DatetimeIndex(sorted(set().union(*[d.index for _, d in data.values()])))
    print(f"종목 {len(data)}개, 기간 {idx[0].date()} ~ {idx[-1].date()}")

    rows, trade_log = [], []
    # 기준: 동일비중 보유(바이앤홀드)
    bh = pd.concat([d["Close"].pct_change() for _, d in data.values()], axis=1, sort=True).reindex(idx).fillna(0).mean(axis=1)
    rows.append({"전략": "동일비중 보유(기준)", "손절": "-", **stats(bh, [])})
    for st in STRATS:
        for stop in (None, 0.10):
            all_tr, rets, held = [], [], []
            for code, (name, df) in data.items():
                b, s = signals(df, st)
                tr, r = run_one(df, b.fillna(False), s.fillna(False), stop)
                all_tr += tr
                trade_log += [(st, stop, code, name, *x) for x in tr]
                rets.append(r.reindex(idx).fillna(0))
                held.append((r != 0).reindex(idx, fill_value=False))
            port = pd.concat(rets, axis=1).mean(axis=1)  # 종목별 1/N 자금 고정 배분
            rows.append({"전략": st, "손절": "없음" if stop is None else f"-{int(stop*100)}%", **stats(port, all_tr, pd.concat(held, axis=1).mean(axis=1).mean())})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(trade_log, columns=["전략", "손절", "코드", "종목", "진입일", "청산일", "수익률", "보유일"]) \
        .to_csv(OUT / "trades.csv", index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(res.to_string(index=False))


if __name__ == "__main__":
    main()
