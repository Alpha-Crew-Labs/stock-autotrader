r"""v4 후보 — 청산 규칙 비교 + 종목 범위 확대 (커뮤니티 조언 반영)

진입은 v3 그대로 (스토캐스틱 14,5,5 예상/골든크로스 + 시장·종목·주봉 필터).
청산 규칙:
  E0 데드크로스 (v3 현행)
  E1 Connors식: 종가 > 5일 이동평균이면 매도 (r/algotrading·Connors 계열에서 일관되게 좋다는 청산)
  E2 형님 해석: 이익 중이면 %K가 80 위에서 꺾일 때까지 보유, 손실 중이면 데드크로스에 바로 매도(원금 보호)
  E3 추세 청산: 이익 중이면 종가가 20일선 아래로 내려올 때까지 보유, 손실 중이면 데드크로스 매도
범위: 코스피 상위 50 (기존) vs 코스피+코스닥 시총 상위 200
통합 포트폴리오(자금 하나, 최대 10종목, 1회 = 배정금 1/3, 물타기 최대 3회)로 비교.
실행: python backtest_exits.py  -> results/v4_exits.csv, docs/img/v4_exits.png
"""
from pathlib import Path
import numpy as np
import pandas as pd
import FinanceDataReader as fdr
import backtest as bt
import backtest_hyung2 as h2
from backtest_hyung3 import weekly_ok

HERE = Path(__file__).parent
RES, IMG = HERE / "results", HERE / "docs" / "img"
MAX_POS, UNITS = 10, 3


def universe200():
    f = bt.DATA / "universe200.csv"
    if f.exists():
        return pd.read_csv(f, dtype={"Code": str})
    l = fdr.StockListing("KRX")
    l = l[l["Market"].isin(["KOSPI", "KOSDAQ"]) & l["Code"].str.endswith("0")]
    l = l[~l["Name"].str.contains("스팩|리츠|인프라") | l["Name"].str.contains("메리츠")]
    u = l.sort_values("Marcap", ascending=False).head(200)[["Code", "Name"]]
    u.to_csv(f, index=False, encoding="utf-8-sig")
    return u


def frames_for(u):
    out = {}
    for c in u["Code"]:
        try:
            p = bt.prices(c)
        except Exception:
            continue
        if len(p) < 300:
            continue
        df = h2.prep(p)
        df["WOK"] = weekly_ok(df)
        df["MA5"] = df["Close"].rolling(5).mean()
        df["MA20"] = df["Close"].rolling(20).mean()
        out[c] = df
    return out


def exit_now(mode, p, i, a):
    """어제(i-1) 종가 기준 청산 여부. a = 종목 배열 dict, p = 포지션"""
    j = i - 1
    in_profit = a["C"][j] * p["sh"] > p["basis"]
    if mode == "E0":
        return a["SELL"][j]
    if mode == "E1":
        return a["C"][j] > a["MA5"][j]
    if mode == "E2":
        if not in_profit:
            return a["SELL"][j]
        p["hit80"] = p.get("hit80", False) or a["K"][j] > 80
        return p["hit80"] and a["K"][j] < a["K"][j - 1]
    if mode == "E3":
        if not in_profit:
            return a["SELL"][j]
        p["above20"] = p.get("above20", False) or a["C"][j] > a["MA20"][j]
        return p["above20"] and a["C"][j] < a["MA20"][j]


def portfolio(frames, mkt_ok, mode, start="2016-01-01"):
    idx = pd.DatetimeIndex(sorted(set().union(*[d.index for d in frames.values()])))
    idx = idx[idx >= start]
    A = {}
    for c, d in frames.items():
        d = d.reindex(idx)
        A[c] = {"O": d["Open"].values, "C": d["Close"].ffill().values, "K": d["K"].values,
                "MA5": d["MA5"].values, "MA20": d["MA20"].values,
                "SELL": d["SELL"].fillna(False).values.astype(bool), "ADD": d["ADD"].fillna(False).values.astype(bool),
                "NEW": (d["BUY"] & d["TREND"] & d["WOK"]).fillna(False).values.astype(bool)}
    mk = mkt_ok.reindex(idx).fillna(False).values
    cash, pos, eq, trades, inv, hold_days = 1.0, {}, [1.0], [], [], []
    for i in range(1, len(idx)):
        for c in list(pos):
            a, p = A[c], pos[c]
            if np.isnan(a["O"][i]):
                continue
            if exit_now(mode, p, i, a):
                pr = p["sh"] * a["O"][i] * (1 - bt.SELL_COST)
                cash += pr
                trades.append(pr / p["basis"] - 1)
                hold_days.append(i - p["t0"])
                del pos[c]
            elif p["u"] < UNITS and a["ADD"][i - 1] and i - p["last"] >= 5:
                amt = min(p["alloc"] / UNITS, cash)
                if amt > 0:
                    p["sh"] += amt / (a["O"][i] * (1 + bt.BUY_COST))
                    p["basis"] += amt
                    cash -= amt
                    p["u"] += 1
                    p["last"] = i
        if mk[i - 1] and len(pos) < MAX_POS:
            cands = [c for c, a in A.items() if a["NEW"][i - 1] and c not in pos and not np.isnan(a["O"][i])]
            cands.sort(key=lambda c: A[c]["K"][i - 1])
            equity = cash + sum(p["sh"] * A[c]["C"][i - 1] for c, p in pos.items())
            for c in cands[:MAX_POS - len(pos)]:
                alloc = equity / MAX_POS
                amt = min(alloc / UNITS, cash)
                if amt <= 0:
                    break
                pos[c] = {"sh": amt / (A[c]["O"][i] * (1 + bt.BUY_COST)), "basis": amt, "u": 1, "last": i,
                          "alloc": alloc, "t0": i}
                cash -= amt
        hold = sum(p["sh"] * A[c]["C"][i] for c, p in pos.items())
        eq.append(cash + hold)
        inv.append(hold / (cash + hold))
    eq = pd.Series(eq, index=idx)
    yrs = (idx[-1] - idx[0]).days / 365.25
    r = np.array(trades)
    return eq, {"CAGR%": round(100 * (eq.iloc[-1] ** (1 / yrs) - 1), 2), "MDD%": round(100 * (eq / eq.cummax() - 1).min(), 1),
                "투자비중%": round(100 * np.mean(inv), 1), "거래수": len(r), "승률%": round(100 * (r > 0).mean(), 1),
                "거래당%": round(100 * r.mean(), 2), "PF": round(r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9), 2),
                "보유일": round(np.mean(hold_days), 1)}


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import make_figures as mf

    k = pd.read_csv(bt.DATA / "069500.csv", index_col=0, parse_dates=True)["Close"]
    mkt_ok = k > k.rolling(200).mean()
    sets = {"상위50": frames_for(bt.universe()), "상위200": frames_for(universe200())}
    print({n: len(f) for n, f in sets.items()})
    names = {"E0": "데드크로스(v3)", "E1": "종가>5일선", "E2": "이익이면 80까지·손실이면 데드크로스", "E3": "이익이면 20일선 이탈까지"}
    rows, curves = [], {}
    for un, fr in sets.items():
        for m in ("E0", "E1", "E2", "E3"):
            eq, st = portfolio(fr, mkt_ok, m)
            rows.append({"범위": un, "청산": f"{m} {names[m]}", **st})
            curves[(un, m)] = eq
            print(rows[-1])
    res = pd.DataFrame(rows)
    res.to_csv(RES / "v4_exits.csv", index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(res.to_string(index=False))

    # 그림: 범위·청산별 CAGR / MDD
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)
    lab = [f"{r['범위']} · {names[r['청산'][:2]]}" for _, r in res.iterrows()]
    y = np.arange(len(res))[::-1]
    best = res["CAGR%"].idxmax()
    col = [mf.BLUE if i == best else "#c9c8c2" for i in range(len(res))]
    a1.barh(y, res["CAGR%"], color=col, height=0.6, zorder=3)
    for yi, v in zip(y, res["CAGR%"]):
        a1.text(v + 0.05, yi, f"{v:+.2f}%", va="center", fontsize=10, color=mf.INK)
    a1.set_yticks(y, lab)
    a1.set_xlabel("연수익률(CAGR, %)")
    a1.set_title("① 연수익", loc="left", fontsize=12.5, fontweight="bold", color=mf.INK)
    a2.barh(y, res["MDD%"], color=col, height=0.6, zorder=3)
    for yi, v in zip(y, res["MDD%"]):
        a2.text(v - 0.3, yi, f"{v:.1f}%", va="center", ha="right", fontsize=10, color=mf.INK)
    a2.set_xlim(min(res["MDD%"]) * 1.4, 0)
    a2.set_xlabel("최대낙폭(MDD, %)")
    a2.set_title("② 최대낙폭", loc="left", fontsize=12.5, fontweight="bold", color=mf.INK)
    for ax in (a1, a2):
        ax.grid(axis="y", visible=False)
    fig.suptitle("파는 방법과 종목 범위를 바꾸면", x=0.01, ha="left", fontsize=15, fontweight="bold", color=mf.INK)
    fig.text(0.01, 0.905, "통합 포트폴리오(최대 10종목), 2016~2026, 비용 반영 · 진입은 v3 동일", fontsize=10.5, color=mf.INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(IMG / "v4_exits.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
