r"""v3 추가 검증 — ① 실전형 통합 포트폴리오 ② 파라미터 안정성 ③ 꼬리 손절

① 통합 포트폴리오: 자금 하나, 최대 10종목. 신규 매수 시 (평가자산/10)을 배정, 1회 매수 = 배정금 × 비중.
   같은 날 신호가 많으면 %K 낮은 순. 신호 종가 판단 -> 다음 날 시가 체결. 비용 반영.
② 파라미터: 스토캐스틱 기간 {9,14,20} × %K·%D 평활 {3,5,7} 의 수익팩터·거래당 평균 (주봉 필터는 14,5,5 고정)
③ 꼬리 손절: 평단 대비 -X% 종가 이탈 시 다음 날 시가 전량 매도 (X = 없음/10/15/20)
실행: python backtest_v3_extra.py   -> results/v3_*.csv, docs/img/v3_*.png
"""
from pathlib import Path
import numpy as np
import pandas as pd
import backtest as bt
import backtest_hyung2 as h2
from backtest_hyung import stoch
from backtest_hyung3 import weekly_ok

HERE = Path(__file__).parent
RES, IMG = HERE / "results", HERE / "docs" / "img"
MAX_POS = 10


def load(n=14, k=5, d=5):
    u = bt.universe()
    h2.stoch = lambda df: stoch(df, n, k, d)
    frames = {}
    for c, nm in zip(u["Code"], u["Name"]):
        df = h2.prep(bt.prices(c))
        df["WOK"] = weekly_ok(df)
        frames[c] = df
    h2.stoch = stoch
    return frames


def mkt_filter():
    k = pd.read_csv(bt.DATA / "069500.csv", index_col=0, parse_dates=True)["Close"]
    return k > k.rolling(200).mean(), k


# ---------- ② ③ 종목별 독립 시뮬레이션 (거래 통계) ----------
def trades_one(df, mkt_ok, stop=None, units=3):
    o, c = df["Open"].values, df["Close"].values
    buy = df["BUY"].values & mkt_ok.reindex(df.index).fillna(False).values & df["TREND"].values & df["WOK"].values
    add, sell = df["ADD"].values, df["SELL"].values
    tr, u, sh, basis, last, t0 = [], 0, 0.0, 0.0, -99, 0
    for i in range(1, len(df)):
        if u:
            hit = stop is not None and c[i - 1] * sh <= basis * (1 - stop)
            if sell[i - 1] or hit:
                tr.append((sh * o[i] * (1 - bt.SELL_COST) / basis - 1, i - t0))
                u = 0
            elif u < units and add[i - 1] and i - last >= 5:
                sh += (1 / units) / (o[i] * (1 + bt.BUY_COST))
                basis += 1 / units
                u, last = u + 1, i
        elif buy[i - 1]:
            sh, basis, u, last, t0 = (1 / units) / (o[i] * (1 + bt.BUY_COST)), 1 / units, 1, i, i
    return tr


def tstats(tr):
    r = np.array([x[0] for x in tr])
    if not len(r):
        return {"거래수": 0}
    return {"거래수": len(r), "승률%": round(100 * (r > 0).mean(), 1), "평균%": round(100 * r.mean(), 2),
            "PF": round(r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9), 2), "최악%": round(100 * r.min(), 1)}


# ---------- ① 통합 포트폴리오 ----------
def portfolio(frames, mkt_ok, frac=1 / 3, units=3, stop=None, start="2016-01-01"):
    idx = pd.DatetimeIndex(sorted(set().union(*[d.index for d in frames.values()])))
    idx = idx[idx >= start]
    F = {c: d.reindex(idx) for c, d in frames.items()}
    O = pd.DataFrame({c: d["Open"] for c, d in F.items()})
    C = pd.DataFrame({c: d["Close"] for c, d in F.items()}).ffill()
    K = pd.DataFrame({c: d["K"] for c, d in F.items()})
    mk = mkt_ok.reindex(idx).fillna(False).values
    sig = {x: pd.DataFrame({c: d[x] for c, d in F.items()}).fillna(False).astype(bool)
           for x in ("BUY", "ADD", "SELL", "TREND", "WOK")}
    cash, pos, eq, trades, invested = 1.0, {}, [], [], []
    codes = list(F)
    for i in range(1, len(idx)):
        o_row, c_prev = O.iloc[i], C.iloc[i - 1]
        # 매도 / 추가매수 (어제 신호 -> 오늘 시가)
        for c in list(pos):
            p = pos[c]
            if np.isnan(o_row[c]):
                continue
            hit = stop is not None and c_prev[c] * p["sh"] <= p["basis"] * (1 - stop)
            if sig["SELL"].iat[i - 1, codes.index(c)] or hit:
                pr = p["sh"] * o_row[c] * (1 - bt.SELL_COST)
                cash += pr
                trades.append(pr / p["basis"] - 1)
                del pos[c]
            elif p["u"] < units and sig["ADD"].iat[i - 1, codes.index(c)] and i - p["last"] >= 5:
                amt = min(p["alloc"] * frac, cash)
                if amt > 0:
                    p["sh"] += amt / (o_row[c] * (1 + bt.BUY_COST))
                    p["basis"] += amt
                    cash -= amt
                    p["u"] += 1
                    p["last"] = i
        # 신규 매수
        if mk[i - 1] and len(pos) < MAX_POS:
            ok = sig["BUY"].iloc[i - 1] & sig["TREND"].iloc[i - 1] & sig["WOK"].iloc[i - 1]
            cands = [c for c in ok[ok].index if c not in pos and not np.isnan(o_row[c])]
            cands.sort(key=lambda c: K[c].iat[i - 1])
            equity = cash + sum(p["sh"] * c_prev[c] for c, p in pos.items())
            for c in cands[:MAX_POS - len(pos)]:
                alloc = equity / MAX_POS
                amt = min(alloc * frac, cash)
                if amt <= 0:
                    break
                pos[c] = {"sh": amt / (o_row[c] * (1 + bt.BUY_COST)), "basis": amt, "u": 1, "last": i, "alloc": alloc}
                cash -= amt
        c_now = C.iloc[i]
        hold = sum(p["sh"] * c_now[c] for c, p in pos.items())
        eq.append(cash + hold)
        invested.append(hold / (cash + hold))
    eq = pd.Series([1.0] + eq, index=idx)
    yrs = (idx[-1] - idx[0]).days / 365.25
    r = np.array(trades)
    return eq, {"CAGR%": round(100 * (eq.iloc[-1] ** (1 / yrs) - 1), 2),
                "MDD%": round(100 * (eq / eq.cummax() - 1).min(), 1),
                "평균 투자비중%": round(100 * np.mean(invested), 1), "거래수": len(r),
                "승률%": round(100 * (r > 0).mean(), 1) if len(r) else None,
                "PF": round(r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9), 2) if len(r) else None}


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import make_figures as mf  # 같은 스타일·팔레트

    mkt_ok, k200 = mkt_filter()
    base = load()

    # ① 통합 포트폴리오
    rows, curves = [], {}
    for name, frac, units, stop in (("1/3씩 (현행)", 1 / 3, 3, None), ("1/2씩", 1 / 2, 2, None),
                                    ("전액 1회", 1.0, 1, None), ("전액 1회 + 손절 -15%", 1.0, 1, 0.15)):
        eq, st = portfolio(base, mkt_ok, frac, units, stop)
        rows.append({"구분": name, **st})
        curves[name] = eq
    kk = k200.reindex(curves["1/3씩 (현행)"].index).ffill()
    yrs = (kk.index[-1] - kk.index[0]).days / 365.25
    rows.append({"구분": "KODEX 200 보유", "CAGR%": round(100 * ((kk.iloc[-1] / kk.iloc[0]) ** (1 / yrs) - 1), 2),
                 "MDD%": round(100 * (kk / kk.cummax() - 1).min(), 1), "평균 투자비중%": 100.0})
    p1 = pd.DataFrame(rows)
    p1.to_csv(RES / "v3_portfolio.csv", index=False, encoding="utf-8-sig")
    print("① 통합 포트폴리오 (최대 10종목, 2016~)\n", p1.to_string(index=False))

    # ② 파라미터 안정성
    grid = []
    for n in (9, 14, 20):
        for s in (3, 5, 7):
            fr = base if (n, s) == (14, 5) else load(n, s, s)
            tr = sum((trades_one(d, mkt_ok) for d in fr.values()), [])
            grid.append({"기간": n, "평활": s, **tstats(tr)})
    p2 = pd.DataFrame(grid)
    p2.to_csv(RES / "v3_params.csv", index=False, encoding="utf-8-sig")
    print("\n② 파라미터 안정성\n", p2.to_string(index=False))

    # ③ 꼬리 손절
    st_rows = []
    for stop in (None, 0.10, 0.15, 0.20):
        tr = sum((trades_one(d, mkt_ok, stop) for d in base.values()), [])
        st_rows.append({"손절": "없음" if stop is None else f"-{int(stop*100)}%", **tstats(tr)})
    p3 = pd.DataFrame(st_rows)
    p3.to_csv(RES / "v3_stop.csv", index=False, encoding="utf-8-sig")
    print("\n③ 꼬리 손절\n", p3.to_string(index=False))

    # 그림 1: 통합 포트폴리오 곡선 vs KODEX200
    fig, ax = plt.subplots(figsize=(11, 4.4))
    kn = 100 * kk / kk.iloc[0]
    ax.plot(kn.index, kn, color=mf.ORANGE, lw=1.6)
    ax.text(kn.index[-1], kn.iloc[-1], f"  KODEX 200 {kn.iloc[-1]-100:+.0f}%", color=mf.INK, va="center", fontsize=10)
    for nm, colr in (("전액 1회", mf.BLUE), ("1/3씩 (현행)", "#86b6ef")):
        e = 100 * curves[nm]
        ax.plot(e.index, e, color=colr, lw=2)
        ax.text(e.index[-1], e.iloc[-1], f"  v3 {nm} {e.iloc[-1]-100:+.0f}%", color=mf.INK, va="center", fontsize=10)
    ax.axhline(100, color=mf.MUTED, lw=1, ls="--")
    ax.set_xlim(kn.index[0], kn.index[-1] + pd.Timedelta(days=900))
    ax.set_ylabel("시작 = 100")
    mf.title(ax, "실전형 포트폴리오 — 자금 하나, 최대 10종목 (2016~2026)",
             "시총 상위 50종목에서 v3 신호가 나는 종목을 채워 넣는 방식 · 비용 반영 · 생존 편향 있음")
    fig.tight_layout()
    fig.savefig(IMG / "v3_portfolio.png", dpi=150)
    plt.close(fig)

    # 그림 2: 파라미터 히트맵 (수익팩터)
    m = p2.pivot(index="기간", columns="평활", values="PF")
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.grid(False)
    vmax = max(abs(m.values - 1).max(), 0.05)
    im = ax.imshow(m.values - 1, cmap="RdBu", vmin=-vmax, vmax=vmax)
    for (r, c), v in np.ndenumerate(m.values):
        cnt = int(p2[(p2["기간"] == m.index[r]) & (p2["평활"] == m.columns[c])]["거래수"].iloc[0])
        ax.text(c, r, f"{v:.2f}\n({cnt}건)", ha="center", va="center", fontsize=10.5,
                color=mf.INK, fontweight="bold" if (m.index[r], m.columns[c]) == (14, 5) else "normal")
    ax.set_xticks(range(len(m.columns)), [f"%K·%D {c}" for c in m.columns])
    ax.set_yticks(range(len(m.index)), [f"기간 {i}" for i in m.index])
    mf.title(ax, "설정을 바꿔도 버티나 — 수익팩터", "파랑 = 1보다 큼(이익), 빨강 = 손실 · 굵은 글씨 = 형님 설정 (14,5,5)")
    fig.tight_layout()
    fig.savefig(IMG / "v3_params.png", dpi=150)
    plt.close(fig)
    print("\n그림 저장: v3_portfolio.png, v3_params.png")


if __name__ == "__main__":
    main()
