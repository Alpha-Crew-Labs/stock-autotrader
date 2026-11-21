r"""v5 전략 고도화 검증 — 실제 시총 상위 200종목 기준 In-Sample / Out-of-Sample 백테스트

배경:
  기존 data/universe.csv 는 알파벳 A~H순 50종목 편향이 있었고, 실제 시총 상위 200(data/universe200.csv)에서는
  E2(80 꺾임)보다 추세 추종 청산·모멘텀 순위·자금 효율 개선 여지가 크다.

비교 항목 (통합 포트폴리오, 최대 10종목, 비용: 매수 0.065% / 매도 0.265%, 종가 판단 -> 다음 날 시가 체결):
  1) 청산 규칙: E2(v4 현행) / E3(이익 중 20일선 이탈) / E4(이익 중 80 도달 또는 20일선 진입 후 20일선 이탈)
  2) 후보 순위: k(%K 낮은 순, 현행) vs rs60(60일 수익률 높은 순)
  3) 분할 매수: units=3(1/3씩) / units=2(1/2씩) / units=1(전액 1회)
  4) 과매도(%K<20) 물타기 우선권: hold_add_under20=False(현행) vs True(%K<20이고 추가매수 한도 남으면 손실 DC 보류)

구간: 전체(2016~2026), In-Sample(2016~2021), Out-of-Sample(2022~2026)
실행: python backtest_upgrade.py  -> results/v5_upgrade.csv, docs/img/v5_upgrade.png
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

import backtest as bt
import strategy as st

HERE = Path(__file__).parent
RES = HERE / "results"
IMG = HERE / "docs" / "img"
MAX_POS = 10

CASES = [
    ("v4 현행 (E2 · %K순 · 3분할)", "E2", "k", 3, False),
    ("E3 청산 (20일선이탈 · %K순 · 3분할)", "E3", "k", 3, False),
    ("E4 하이브리드 (80/20일선 · %K순 · 3분할)", "E4", "k", 3, False),
    ("E3 + 모멘텀순위(RS60) · 3분할", "E3", "rs60", 3, False),
    ("E4 + 모멘텀순위(RS60) · 3분할", "E4", "rs60", 3, False),
    ("E3 + RS60 · 2분할(1/2씩)", "E3", "rs60", 2, False),
    ("E3 + RS60 · 1회(전액)", "E3", "rs60", 1, False),
    ("E4 + RS60 · 2분할(1/2씩)", "E4", "rs60", 2, False),
    ("E4 + RS60 · 1회(전액)", "E4", "rs60", 1, False),
    ("E3 + %K순 · 3분할 + %K<20물타기우선", "E3", "k", 3, True),
    ("E3 + RS60 · 3분할 + %K<20물타기우선", "E3", "rs60", 3, True),
    ("E3 + RS60 · 2분할 + %K<20물타기우선", "E3", "rs60", 2, True),
    ("E2 + RS60 · 3분할 + %K<20물타기우선", "E2", "rs60", 3, True),
]

PERIODS = [
    ("전체(2016~2026)", "2016-01-01", None),
    ("IS(2016~2021)", "2016-01-01", "2021-12-31"),
    ("OOS(2022~2026)", "2022-01-01", None),
]


def load_frames(universe_path: Path) -> dict[str, pd.DataFrame]:
    u = pd.read_csv(universe_path, dtype={"Code": str})
    out = {}
    for code in u["Code"]:
        f = bt.DATA / f"{code}.csv"
        if not f.exists():
            continue
        p = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(p) < 300:
            continue
        df = st.prep(p)
        df["WOK"] = st.weekly_ok(df)
        out[code] = df
    return out


def sim_portfolio(
    frames: dict[str, pd.DataFrame],
    mkt_ok: pd.Series,
    exit_mode: str = "E2",
    rank_by: str = "k",
    units: int = 3,
    hold_add_under20: bool = False,
    start: str = "2016-01-01",
    end: str | None = None,
) -> tuple[pd.Series, dict]:
    idx = pd.DatetimeIndex(sorted(set().union(*[d.index for d in frames.values()])))
    idx = idx[idx >= start]
    if end is not None:
        idx = idx[idx <= end]
    arrays = {}
    for code, df in frames.items():
        d = df.reindex(idx)
        arrays[code] = {
            "O": d["Open"].values,
            "C": d["Close"].ffill().values,
            "K": d["K"].values,
            "MA20": d["MA20"].values,
            "RET60": d["RET60"].values,
            "SELL": d["SELL"].fillna(False).values.astype(bool),
            "ADD": d["ADD"].fillna(False).values.astype(bool),
            "NEW": (d["BUY"] & d["TREND"] & d["WOK"]).fillna(False).values.astype(bool),
        }
    mk = mkt_ok.reindex(idx).fillna(False).values
    cash, pos = 1.0, {}
    eq, inv = [1.0], [0.0]
    trades, hold_days, trade_units = [], [], []

    for i in range(1, len(idx)):
        j = i - 1
        for code in list(pos):
            a, p = arrays[code], pos[code]
            op = a["O"][i]
            if np.isnan(op):
                continue
            c_prev = a["C"][j]
            k_prev = a["K"][j]
            k_prev2 = a["K"][j - 1] if j >= 1 else np.nan
            ma20_prev = a["MA20"][j]

            p["hit80"] = p["hit80"] or (not np.isnan(k_prev) and k_prev > 80)
            p["above20"] = p["above20"] or (
                not np.isnan(c_prev) and not np.isnan(ma20_prev) and c_prev > ma20_prev
            )
            in_profit = c_prev * p["sh"] * (1 - bt.SELL_COST) > p["basis"]

            should_sell = False
            if exit_mode == "E0":
                should_sell = bool(a["SELL"][j])
            elif exit_mode == "E2":
                if in_profit:
                    should_sell = bool(p["hit80"] and not np.isnan(k_prev2) and k_prev < k_prev2)
                else:
                    should_sell = bool(a["SELL"][j])
            elif exit_mode == "E3":
                if in_profit:
                    should_sell = bool(p["above20"] and not np.isnan(ma20_prev) and c_prev < ma20_prev)
                else:
                    should_sell = bool(a["SELL"][j])
            elif exit_mode == "E4":
                if in_profit:
                    should_sell = bool((p["hit80"] or p["above20"]) and not np.isnan(ma20_prev) and c_prev < ma20_prev)
                else:
                    should_sell = bool(a["SELL"][j])
            else:
                raise ValueError(exit_mode)

            if hold_add_under20 and not in_profit and should_sell and not np.isnan(k_prev) and k_prev < 20 and p["u"] < units:
                should_sell = False

            if should_sell:
                pr = p["sh"] * op * (1 - bt.SELL_COST)
                cash += pr
                trades.append(pr / p["basis"] - 1)
                hold_days.append(i - p["t0"])
                trade_units.append(p["u"])
                del pos[code]
            elif p["u"] < units and a["ADD"][j] and i - p["last"] >= 5:
                amt = min(p["alloc"] / units, cash)
                if amt > 0:
                    p["sh"] += amt / (op * (1 + bt.BUY_COST))
                    p["basis"] += amt
                    cash -= amt
                    p["u"] += 1
                    p["last"] = i

        if mk[j] and len(pos) < MAX_POS:
            cands = [c for c, a in arrays.items() if a["NEW"][j] and c not in pos and not np.isnan(a["O"][i])]
            if rank_by == "rs60":
                cands.sort(
                    key=lambda c: (
                        -(arrays[c]["RET60"][j] if not np.isnan(arrays[c]["RET60"][j]) else -1e9),
                        arrays[c]["K"][j] if not np.isnan(arrays[c]["K"][j]) else 999.0,
                    )
                )
            else:
                cands.sort(key=lambda c: arrays[c]["K"][j] if not np.isnan(arrays[c]["K"][j]) else 999.0)

            equity = cash + sum(p["sh"] * arrays[c]["C"][j] for c, p in pos.items())
            for c in cands[: MAX_POS - len(pos)]:
                alloc = equity / MAX_POS
                amt = min(alloc / units, cash)
                if amt <= 0:
                    break
                pos[c] = {
                    "sh": amt / (arrays[c]["O"][i] * (1 + bt.BUY_COST)),
                    "basis": amt,
                    "u": 1,
                    "last": i,
                    "alloc": alloc,
                    "t0": i,
                    "hit80": False,
                    "above20": False,
                }
                cash -= amt

        hold = sum(p["sh"] * arrays[c]["C"][i] for c, p in pos.items())
        tot = cash + hold
        eq.append(tot)
        inv.append(hold / tot if tot > 0 else 0.0)

    eq_s = pd.Series(eq, index=idx)
    yrs = max((idx[-1] - idx[0]).days / 365.25, 1e-6)
    cagr = 100 * (eq_s.iloc[-1] ** (1 / yrs) - 1)
    mdd = 100 * (eq_s / eq_s.cummax() - 1).min()
    calmar = round(cagr / abs(mdd), 3) if mdd < 0 else np.nan
    r = np.array(trades, dtype=float)
    pf = round(float(r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)), 2) if len(r) else 0.0
    return eq_s, {
        "CAGR%": round(cagr, 2),
        "MDD%": round(mdd, 1),
        "Calmar": calmar,
        "투자비중%": round(100 * float(np.mean(inv)), 1),
        "거래수": int(len(r)),
        "승률%": round(100 * float((r > 0).mean()), 1) if len(r) else 0.0,
        "거래당%": round(100 * float(r.mean()), 2) if len(r) else 0.0,
        "PF": pf,
        "평균보유일": round(float(np.mean(hold_days)), 1) if hold_days else 0.0,
        "물타기비율%": round(100 * float(np.mean([u > 1 for u in trade_units])), 1) if trade_units else 0.0,
    }


def _pick_font():
    import matplotlib.font_manager as fm

    avail = {f.name for f in fm.fontManager.ttflist}
    for name in ("Malgun Gothic", "Apple SD Gothic Neo", "AppleGothic", "Nanum Gothic", "NanumGothic"):
        if name in avail:
            return name
    return "DejaVu Sans"


def save_chart(df: pd.DataFrame, out_png: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    BLUE, ORANGE, INK, INK2, GRID, SURF = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
    plt.rcParams.update({
        "font.family": _pick_font(), "axes.unicode_minus": False,
        "figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": GRID,
        "axes.labelcolor": INK2, "axes.grid": True, "grid.color": GRID,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    })

    u200 = df[df["유니버스"] == "시총상위200"].copy()
    is_df = u200[u200["구간"] == "IS(2016~2021)"].set_index("케이스")
    oos_df = u200[u200["구간"] == "OOS(2022~2026)"].set_index("케이스")
    full_df = u200[u200["구간"] == "전체(2016~2026)"].copy()

    labels = list(full_df["케이스"])
    y = np.arange(len(labels))[::-1]

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.8, 6.2), sharey=True)
    h = 0.36
    b1 = a1.barh(y + h / 2, [is_df.loc[k, "PF"] for k in labels], height=h, color="#86b6ef", label="IS (2016~2021)")
    b2 = a1.barh(y - h / 2, [oos_df.loc[k, "PF"] for k in labels], height=h, color=BLUE, label="OOS (2022~2026)")
    a1.axvline(1.0, color=INK, lw=1.1, ls="--")
    for yi, k in zip(y, labels):
        a1.text(oos_df.loc[k, "PF"] + 0.03, yi - h / 2, f"{oos_df.loc[k, 'PF']:.2f}", va="center", fontsize=8.5, color=INK)
        a1.text(is_df.loc[k, "PF"] + 0.03, yi + h / 2, f"{is_df.loc[k, 'PF']:.2f}", va="center", fontsize=8.5, color=INK2)
    a1.set_yticks(y, labels)
    a1.set_xlim(0, max(oos_df["PF"].max(), is_df["PF"].max()) * 1.18)
    a1.set_xlabel("수익팩터 (PF)")
    a1.set_title("① 수익팩터 (IS vs OOS)", loc="left", fontweight="bold")
    a1.legend(frameon=False, loc="upper right", bbox_to_anchor=(1.0, 1.07), ncol=2, fontsize=8.5)
    a1.grid(axis="y", visible=False)

    a2.barh(y + h / 2, [is_df.loc[k, "CAGR%"] for k in labels], height=h, color="#f4ad8f", label="IS (2016~2021)")
    a2.barh(y - h / 2, [oos_df.loc[k, "CAGR%"] for k in labels], height=h, color=ORANGE, label="OOS (2022~2026)")
    a2.axvline(0.0, color=INK, lw=1.0)
    for yi, k in zip(y, labels):
        v = oos_df.loc[k, "CAGR%"]
        a2.text(v + 0.1 if v >= 0 else v - 0.1, yi - h / 2, f"{v:+.2f}%", va="center", ha="left" if v >= 0 else "right", fontsize=8.5, color=INK)
    a2.set_xlim(min(-1.8, oos_df["CAGR%"].min() - 1.2), max(oos_df["CAGR%"].max(), is_df["CAGR%"].max()) * 1.2)
    a2.set_xlabel("연수익률 (CAGR %)")
    a2.set_title("② 연수익률 (IS vs OOS)", loc="left", fontweight="bold")
    a2.legend(frameon=False, loc="upper right", bbox_to_anchor=(1.0, 1.07), ncol=2, fontsize=8.5)
    a2.grid(axis="y", visible=False)

    fig.suptitle("시총 상위 200종목 고도화 검증 — In-Sample vs Out-of-Sample", x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.01, 0.925, "비용 반영(매수 0.065%, 매도 0.265%), 최대 10종목, 미래 데이터 차단", fontsize=10, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def main():
    RES.mkdir(parents=True, exist_ok=True)
    kodex = pd.read_csv(bt.DATA / "069500.csv", index_col=0, parse_dates=True)["Close"]
    mkt_ok = kodex > kodex.rolling(200).mean()

    u200_frames = load_frames(bt.DATA / "universe200.csv")
    u50_frames = load_frames(bt.DATA / "universe.csv")

    rows = []
    for uname, frames in (("시총상위200", u200_frames), ("기존50(A~H)", u50_frames)):
        for pname, start, end in PERIODS:
            for cname, emode, rank_by, units, hold_add in CASES:
                if uname == "기존50(A~H)" and pname != "전체(2016~2026)":
                    continue
                _, st_res = sim_portfolio(
                    frames, mkt_ok,
                    exit_mode=emode, rank_by=rank_by, units=units, hold_add_under20=hold_add,
                    start=start, end=end,
                )
                rows.append({
                    "유니버스": uname, "구간": pname, "케이스": cname,
                    "exit_mode": emode, "rank_by": rank_by, "units": units, "hold_add_under20": hold_add,
                    **st_res,
                })

    res = pd.DataFrame(rows)
    res.to_csv(RES / "v5_upgrade.csv", index=False, encoding="utf-8-sig")
    save_chart(res, IMG / "v5_upgrade.png")
    with pd.option_context("display.width", 260, "display.max_columns", 25, "display.max_rows", 100):
        print(res.to_string(index=False))


if __name__ == "__main__":
    main()
