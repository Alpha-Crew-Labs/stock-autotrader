r"""'승률 99%' 스토캐스틱+RSI+MACD 5분봉 기법 (Data Trader 원형) 백테스트 — 국내주식 롱 온리

진입(롱): ① 최근 L봉 안에 슬로우 스토캐스틱(14,3,3) %K·%D가 둘 다 20 아래 (그 뒤 %K가 80을 넘지 않았을 것)
          ② RSI(14) > 50
          ③ MACD(12,26,9)가 시그널선을 상향 돌파한 봉   -> 다음 봉 시가 매수
손절: 진입 직전 10봉 최저가(직전 저점)   익절: 진입가 + 1.5 × 손절폭
당일 청산: 장 마지막 봉 종가에 강제 청산(오버나이트 없음), 14:30 이후 신규 진입 금지
같은 봉에서 손절·익절 둘 다 닿으면 손절로 처리(보수적)
데이터: yfinance 5분봉(최근 60일 한도), 대상 = data\universe.csv(코스피 시총 상위 50)
실행: python backtest_5m.py
"""
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).parent
DATA5 = HERE / "data_5m"
OUT = HERE / "results"
BUY_COST = 0.00015 + 0.0005
SELL_COST = 0.00015 + 0.0005 + 0.0020
RR, SWING = 1.5, 10


def load(code):
    f = DATA5 / f"{code}.csv"
    if f.exists():
        return pd.read_csv(f, index_col=0, parse_dates=True)
    d = yf.download(f"{code}.KS", period="60d", interval="5m", progress=False, auto_adjust=False)
    if d.empty:
        return d
    d.columns = d.columns.get_level_values(0)
    d = d[["Open", "High", "Low", "Close", "Volume"]]
    d.index = d.index.tz_localize(None)
    DATA5.mkdir(exist_ok=True)
    d.to_csv(f)
    return d


def indicators(df):
    lo, hi = df["Low"].rolling(14).min(), df["High"].rolling(14).max()
    df["K"] = (100 * (df["Close"] - lo) / (hi - lo).replace(0, np.nan)).rolling(3).mean()
    df["D"] = df["K"].rolling(3).mean()
    d = df["Close"].diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["RSI"] = 100 - 100 / (1 + up / dn)
    m = df["Close"].ewm(span=12, adjust=False).mean() - df["Close"].ewm(span=26, adjust=False).mean()
    s = m.ewm(span=9, adjust=False).mean()
    df["MACD_X"] = (m > s) & (m.shift() <= s.shift())
    return df


def entry_signal(df, L):
    os_ = (df["K"] < 20) & (df["D"] < 20)
    # 마지막 과매도 이후 %K가 80을 넘었으면 무효
    last_os = pd.Series(np.where(os_, np.arange(len(df)), np.nan), index=df.index).ffill()
    last_ob = pd.Series(np.where(df["K"] > 80, np.arange(len(df)), np.nan), index=df.index).ffill()
    pos = np.arange(len(df))
    armed = (pos - last_os <= L) & ~(last_ob > last_os)
    return armed & (df["RSI"] > 50) & df["MACD_X"]


def simulate(df, sig):
    o, h, l, c = (df[x].values for x in ("Open", "High", "Low", "Close"))
    day = df.index.normalize()
    last_bar = np.r_[day[1:] != day[:-1], True]
    late = (df.index.hour * 100 + df.index.minute) >= 1430
    s = sig.values
    trades, i, n = [], SWING, len(df)
    while i < n - 1:
        if not (s[i] and not late[i] and not last_bar[i]):
            i += 1
            continue
        j = i + 1
        entry = o[j]
        stop = l[j - SWING:j].min()
        if stop >= entry:
            i += 1
            continue
        tp = entry + RR * (entry - stop)
        while True:
            if l[j] <= stop:
                px, why = min(o[j], stop) if j > i + 1 else stop, "손절"
                break
            if h[j] >= tp:
                px, why = max(o[j], tp) if j > i + 1 else tp, "익절"
                break
            if last_bar[j]:
                px, why = c[j], "장마감"
                break
            j += 1
        gross = px / entry - 1
        net = px * (1 - SELL_COST) / (entry * (1 + BUY_COST)) - 1
        trades.append((df.index[i + 1], df.index[j], why, gross, net, (entry - stop) / entry))
        i = j + 1
    return trades


def summarize(name, tr):
    if not tr:
        return {"설정": name, "거래수": 0}
    t = pd.DataFrame(tr, columns=["진입", "청산", "사유", "gross", "net", "손절폭"])
    w = t["net"]
    return {"설정": name, "거래수": len(t),
            "승률%(비용전)": round(100 * (t["gross"] > 0).mean(), 1),
            "승률%(비용후)": round(100 * (w > 0).mean(), 1),
            "평균%(비용전)": round(100 * t["gross"].mean(), 3),
            "평균%(비용후)": round(100 * w.mean(), 3),
            "PF(비용후)": round(w[w > 0].sum() / -w[w < 0].sum(), 2) if (w < 0).any() else np.inf,
            "익절/손절/장마감": "/".join(str((t["사유"] == k).sum()) for k in ("익절", "손절", "장마감")),
            "평균손절폭%": round(100 * t["손절폭"].mean(), 2)}


def main():
    OUT.mkdir(exist_ok=True)
    u = pd.read_csv(HERE / "data" / "universe.csv", dtype={"Code": str})
    data = {}
    for code, name in zip(u["Code"], u["Name"]):
        d = load(code)
        if len(d) > 500:
            data[code] = (name, indicators(d.copy()))
    span = min(d.index[0] for _, d in data.values()), max(d.index[-1] for _, d in data.values())
    print(f"종목 {len(data)}개, 5분봉 {span[0]:%Y-%m-%d} ~ {span[1]:%Y-%m-%d}")
    rows, log = [], []
    for L in (5, 10, 20):
        tr_all = []
        for code, (name, df) in data.items():
            tr = simulate(df, entry_signal(df, L))
            tr_all += tr
            log += [(L, code, name, *x) for x in tr]
        rows.append(summarize(f"과매도 후 {L}봉 이내", tr_all))
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "summary_5m.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(log, columns=["L", "코드", "종목", "진입", "청산", "사유", "비용전", "비용후", "손절폭"]) \
        .to_csv(OUT / "trades_5m.csv", index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 220, "display.max_columns", 20):
        print(res.to_string(index=False))


if __name__ == "__main__":
    main()
