r"""v6 리서치 백테스트 — 레딧(r/algotrading) 스윙·모멘텀 방법론 통합 검증 (한국 시총상위 200 + 미국 우량주 65)

비교 전략군:
  - M0_v5_현행                    : Stoch(14,5,5) + Weekly + RS60 + E3 + 3분할
  - M1_v5_역변동성1회             : v5 진입/E3 청산 + 역변동성 1회 투자
  - M2a_정배열_Stoch_역변동성     : Close>SMA200 & SMA50>SMA200 + Stoch BUY + Clenow90 순위 + 역변동성 + E3
  - M2b_FastPullback_RSI2_IBS_MR  : (RSI2<10 | (IBS<0.20 & Close<SMA5)) + Clenow90 + 역변동성 + 단기 평균회귀 청산
  - M2c_FastPullback_RSI2_IBS_E3  : M2b 진입 + E3(20일선이탈) & ATR/SMA200 방어
  - M2d_Hybrid_Chandelier         : (Stoch BUY | (RSI2<10 & IBS<0.25)) + Clenow90 + 역변동성 + Chandelier 2.5*ATR
  - M2e_Hybrid_E3_ATRStop         : M2d 진입 + E3(20일선이탈) & 3.0*ATR/SMA200 방어
  - M3_Clenow_Momentum            : 주간 리밸런싱 Clenow90 상위10 역변동성 (하락장 현금화)
  - M4_DualEngine_50_50_*         : 50% Clenow 코어 + 50% 위성 눌림목 (월간 리밸런싱)
  - M5_DualEngine_DynamicIdle     : 단일 계좌 내 위성 우선 + 유휴현금 최대 60% Clenow 코어 운용
  - Sanity_Random_Entry           : 동일 정배열·역변동성·보유구조에서 20시드 무작위 진입 평균

실행: python backtest_research_v6.py
산출: results/v6_research_kr.csv, results/v6_research_us.csv
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

import strategy as st

HERE = Path(__file__).parent
DATA_KR = HERE / "data"
DATA_US = HERE / "data_us"
RES = HERE / "results"
MAX_POS = 10

US_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "ORCL",
    "CRM", "ADBE", "QCOM", "TXN", "AMD", "INTC", "NFLX", "DIS",
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "PFE", "BMY",
    "JPM", "BAC", "WFC", "GS", "MS", "BLK", "V", "MA", "AXP",
    "WMT", "COST", "HD", "PG", "KO", "PEP", "MCD", "SBUX", "NKE", "LOW", "TGT",
    "XOM", "CVX", "COP", "CAT", "HON", "GE", "UNP", "UPS", "RTX", "LMT", "BA", "LIN",
    "NEE", "DUK", "AMT", "PLD", "VZ", "T", "CVS", "PYPL",
]

PERIODS = [
    ("전체(2016~2026)", "2016-01-01", None),
    ("IS(2016~2021)", "2016-01-01", "2021-12-31"),
    ("OOS(2022~2026)", "2022-01-01", None),
]


def add_research_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = st.prep(df)
    out["WOK"] = st.weekly_ok(out)
    c, h, l = out["Close"], out["High"], out["Low"]

    # Wilder RSI(2)
    diff = c.diff()
    up = diff.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-diff.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    rsi2 = 100.0 - 100.0 / (1.0 + up / dn.replace(0, np.nan))
    rsi2 = rsi2.where(dn != 0, np.where(up > 0, 100.0, 50.0))
    out["RSI2"] = rsi2

    # IBS
    rng = (h - l)
    out["IBS"] = np.where(rng > 0, (c - l) / rng, 0.5)

    out["SMA5"] = c.rolling(5).mean()
    out["SMA50"] = c.rolling(50).mean()
    out["SMA100"] = c.rolling(100).mean()
    out["SMA200"] = c.rolling(200).mean()
    out["VOL20"] = (c.pct_change().rolling(20).std() * np.sqrt(252)).clip(lower=0.10)
    out["SKIP_MOM"] = c.shift(20) / c.shift(120) - 1.0

    # Clenow 90-day log-price regression annualized * R^2 (causal)
    w = 90
    log_c = np.log(c.values.astype(float))
    clenow = np.full(len(out), np.nan, dtype=float)
    if len(log_c) >= w:
        x = np.arange(w, dtype=float)
        xc = x - x.mean()
        var_x = float(np.sum(xc ** 2))
        wins = np.lib.stride_tricks.sliding_window_view(log_c, w)
        dy = wins - wins.mean(axis=1, keepdims=True)
        cov = dy @ xc
        slope = cov / var_x
        ss_tot = np.sum(dy ** 2, axis=1)
        r2 = np.where(ss_tot > 1e-12, (cov ** 2) / (var_x * ss_tot), 0.0)
        ann = np.exp(np.clip(slope * 252.0, -5.0, 5.0)) - 1.0
        clenow[w - 1 :] = ann * r2
    out["CLENOW90"] = clenow
    return out


def load_kr():
    u = pd.read_csv(DATA_KR / "universe200.csv", dtype={"Code": str})
    frames = {}
    for code in u["Code"]:
        f = DATA_KR / f"{code}.csv"
        if not f.exists():
            continue
        p = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(p) >= 300:
            frames[code] = add_research_indicators(p)
    idx_df = pd.read_csv(DATA_KR / "069500.csv", index_col=0, parse_dates=True)
    return frames, idx_df["Close"]


def load_us():
    frames = {}
    for tk in US_TICKERS:
        f = DATA_US / f"{tk}.csv"
        if not f.exists():
            continue
        p = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(p) >= 300:
            frames[tk] = add_research_indicators(p)
    idx_df = pd.read_csv(DATA_US / "SPY.csv", index_col=0, parse_dates=True)
    return frames, idx_df["Close"]


def prepare_arrays(frames: dict[str, pd.DataFrame], idx_close: pd.Series, start: str, end: str | None):
    idx = pd.DatetimeIndex(sorted(set().union(*[d.index for d in frames.values()])))
    idx = idx[idx >= start]
    if end is not None:
        idx = idx[idx <= end]
    mkt_ok_s = (idx_close > idx_close.rolling(200).mean()).reindex(idx).fillna(False).values

    arrays = {}
    for code, df in frames.items():
        d = df.reindex(idx)
        arrays[code] = {
            "O": d["Open"].values,
            "H": d["High"].values,
            "C": d["Close"].ffill().values,
            "RAW_C": d["Close"].values,
            "K": d["K"].values,
            "MA20": d["MA20"].values,
            "SMA5": d["SMA5"].values,
            "SMA50": d["SMA50"].values,
            "SMA100": d["SMA100"].values,
            "SMA200": d["SMA200"].values,
            "RET60": d["RET60"].values,
            "ATR14": d["ATR14"].values,
            "RSI2": d["RSI2"].values,
            "IBS": d["IBS"].values,
            "VOL20": d["VOL20"].values,
            "CLENOW90": d["CLENOW90"].values,
            "BUY": d["BUY"].fillna(False).values.astype(bool),
            "ADD": d["ADD"].fillna(False).values.astype(bool),
            "SELL": d["SELL"].fillna(False).values.astype(bool),
            "TREND": d["TREND"].fillna(False).values.astype(bool),
            "WOK": d["WOK"].fillna(False).values.astype(bool),
        }
    return idx, mkt_ok_s, arrays


def calc_metrics(eq: pd.Series, inv: list[float], trades: list[float], hold_days: list[int]) -> dict:
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-6)
    cagr = 100.0 * (eq.iloc[-1] ** (1.0 / yrs) - 1.0)
    mdd = 100.0 * (eq / eq.cummax() - 1.0).min()
    rets = eq.pct_change().dropna().values
    std = float(np.std(rets))
    sharpe = float(np.mean(rets) / std * np.sqrt(252.0)) if std > 1e-12 else 0.0
    dn = float(np.sqrt(np.mean(np.minimum(rets, 0.0) ** 2)))
    sortino = float(np.mean(rets) / dn * np.sqrt(252.0)) if dn > 1e-12 else 0.0
    calmar = round(cagr / abs(mdd), 3) if mdd < -1e-6 else np.nan
    r = np.array(trades, dtype=float)
    pf = round(float(r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)), 2) if len(r) else 0.0
    return {
        "CAGR%": round(cagr, 2),
        "MDD%": round(mdd, 1),
        "Sharpe": round(sharpe, 2),
        "Sortino": round(sortino, 2),
        "Calmar": calmar,
        "투자비중%": round(100.0 * float(np.mean(inv)), 1) if inv else 0.0,
        "거래수": int(len(r)),
        "승률%": round(100.0 * float((r > 0).mean()), 1) if len(r) else 0.0,
        "거래당%": round(100.0 * float(r.mean()), 2) if len(r) else 0.0,
        "PF": pf,
        "평균보유일": round(float(np.mean(hold_days)), 1) if hold_days else 0.0,
    }


def _inv_vol_amt(equity: float, vol20: float, max_w: float = 0.15, target_risk: float = 0.025) -> float:
    v = vol20 if (not np.isnan(vol20) and vol20 > 0) else 0.25
    w = min(max_w, target_risk / max(v, 0.10))
    return equity * w


def sim_swing(
    idx, mkt_ok, arrays, buy_cost, sell_cost, variant: str,
    rng: np.random.Generator | None = None,
    max_w: float = 0.15, target_risk: float = 0.025,
):
    cash, pos = 1.0, {}
    eq, inv = [1.0], [0.0]
    trades, hold_days = [], []

    for i in range(1, len(idx)):
        j = i - 1
        for code in list(pos):
            a, p = arrays[code], pos[code]
            op = a["O"][i]
            if np.isnan(op) or np.isnan(a["RAW_C"][j]):
                continue
            c_prev = a["RAW_C"][j]
            ma20_prev = a["MA20"][j]
            sma5_prev = a["SMA5"][j]
            sma200_prev = a["SMA200"][j]
            atr_prev = a["ATR14"][j]

            p["above20"] = p["above20"] or (not np.isnan(ma20_prev) and c_prev > ma20_prev)
            if not np.isnan(a["H"][j]):
                p["max_h"] = max(p["max_h"], a["H"][j])
            in_profit = c_prev * p["sh"] * (1.0 - sell_cost) > p["basis"]
            days_held = i - p["t0"]

            should_sell = False
            if variant in ("M0", "M1", "M2a"):
                if in_profit:
                    should_sell = bool(p["above20"] and not np.isnan(ma20_prev) and c_prev < ma20_prev)
                else:
                    should_sell = bool(a["SELL"][j])
            elif variant == "M2b":
                prev_high = a["H"][j - 1] if j >= 1 else np.nan
                mr_hit = (not np.isnan(sma5_prev) and c_prev > sma5_prev) or (
                    not np.isnan(prev_high) and c_prev > prev_high
                )
                stop_hit = (not in_profit and days_held >= 10) or (not np.isnan(sma200_prev) and c_prev < sma200_prev)
                should_sell = bool(mr_hit or stop_hit)
            elif variant == "M2c":
                if in_profit:
                    should_sell = bool(p["above20"] and not np.isnan(ma20_prev) and c_prev < ma20_prev)
                else:
                    atr_stop = c_prev < (p["entry_px"] - 2.5 * p["entry_atr"]) if p["entry_atr"] > 0 else False
                    sma_stop = (not np.isnan(sma200_prev) and c_prev < sma200_prev)
                    should_sell = bool(atr_stop or sma_stop)
            elif variant == "M2d":
                if in_profit:
                    trail = p["max_h"] - 2.5 * atr_prev if not np.isnan(atr_prev) else np.nan
                    should_sell = bool(not np.isnan(trail) and c_prev < trail)
                else:
                    atr_stop = c_prev < (p["entry_px"] - 2.5 * p["entry_atr"]) if p["entry_atr"] > 0 else False
                    sma_stop = (not np.isnan(sma200_prev) and c_prev < sma200_prev)
                    should_sell = bool(atr_stop or sma_stop)
            elif variant == "M2e":
                if in_profit:
                    should_sell = bool(p["above20"] and not np.isnan(ma20_prev) and c_prev < ma20_prev)
                else:
                    atr_stop = c_prev < (p["entry_px"] - 3.0 * atr_prev) if not np.isnan(atr_prev) else False
                    sma_stop = (not np.isnan(sma200_prev) and c_prev < sma200_prev)
                    should_sell = bool(atr_stop or sma_stop)
            elif variant == "RAND":
                should_sell = bool(days_held >= 10)

            if should_sell:
                pr = p["sh"] * op * (1.0 - sell_cost)
                cash += pr
                trades.append(pr / p["basis"] - 1.0)
                hold_days.append(days_held)
                del pos[code]
            elif variant == "M0" and p["u"] < 3 and a["ADD"][j] and (i - p["last"] >= 5):
                amt = min(p["alloc"] / 3.0, cash)
                if amt > 0:
                    p["sh"] += amt / (op * (1.0 + buy_cost))
                    p["basis"] += amt
                    cash -= amt
                    p["u"] += 1
                    p["last"] = i

        if mkt_ok[j] and len(pos) < MAX_POS:
            cands = []
            for code, a in arrays.items():
                if code in pos or np.isnan(a["O"][i]) or np.isnan(a["RAW_C"][j]):
                    continue
                c_prev = a["RAW_C"][j]
                aligned = (
                    not np.isnan(a["SMA200"][j]) and not np.isnan(a["SMA50"][j])
                    and c_prev > a["SMA200"][j] and a["SMA50"][j] > a["SMA200"][j]
                )
                if variant in ("M0", "M1"):
                    ok = bool(a["BUY"][j] and a["TREND"][j] and a["WOK"][j])
                elif variant == "M2a":
                    ok = bool(aligned and a["BUY"][j])
                elif variant in ("M2b", "M2c"):
                    rsi2, ibs, sma5 = a["RSI2"][j], a["IBS"][j], a["SMA5"][j]
                    fast = (not np.isnan(rsi2) and rsi2 < 10.0) or (
                        not np.isnan(ibs) and ibs < 0.20 and not np.isnan(sma5) and c_prev < sma5
                    )
                    ok = bool(aligned and fast)
                elif variant in ("M2d", "M2e"):
                    rsi2, ibs = a["RSI2"][j], a["IBS"][j]
                    fast = (not np.isnan(rsi2) and rsi2 < 10.0 and not np.isnan(ibs) and ibs < 0.25)
                    ok = bool(aligned and (a["BUY"][j] or fast))
                elif variant == "RAND":
                    ok = bool(aligned and rng.random() < 0.025)
                else:
                    ok = False
                if ok:
                    cands.append(code)

            if variant in ("M0", "M1"):
                cands.sort(key=lambda c: -(arrays[c]["RET60"][j] if not np.isnan(arrays[c]["RET60"][j]) else -1e9))
            elif variant == "RAND":
                rng.shuffle(cands)
            else:
                cands.sort(key=lambda c: -(arrays[c]["CLENOW90"][j] if not np.isnan(arrays[c]["CLENOW90"][j]) else -1e9))

            equity = cash + sum(p["sh"] * arrays[c]["C"][j] for c, p in pos.items())
            for c in cands[: MAX_POS - len(pos)]:
                op = arrays[c]["O"][i]
                if variant == "M0":
                    alloc = equity / MAX_POS
                    amt = min(alloc / 3.0, cash)
                else:
                    alloc = equity / MAX_POS
                    amt = min(_inv_vol_amt(equity, arrays[c]["VOL20"][j], max_w=max_w, target_risk=target_risk), cash)
                if amt <= 1e-9:
                    break
                atr_j = arrays[c]["ATR14"][j]
                pos[c] = {
                    "sh": amt / (op * (1.0 + buy_cost)),
                    "basis": amt,
                    "u": 1,
                    "last": i,
                    "alloc": alloc,
                    "t0": i,
                    "above20": False,
                    "entry_px": op,
                    "entry_atr": float(atr_j) if not np.isnan(atr_j) else 0.0,
                    "max_h": float(op),
                }
                cash -= amt

        hold = sum(p["sh"] * arrays[c]["C"][i] for c, p in pos.items())
        tot = cash + hold
        eq.append(tot)
        inv.append(hold / tot if tot > 0 else 0.0)

    eq_s = pd.Series(eq, index=idx)
    return eq_s, calc_metrics(eq_s, inv, trades, hold_days)


def sim_clenow_momentum(idx, mkt_ok, arrays, buy_cost, sell_cost, max_pos: int = 10, max_exposure: float = 1.0):
    cash, pos = 1.0, {}
    eq, inv = [1.0], [0.0]
    trades, hold_days = [], []

    for i in range(1, len(idx)):
        j = i - 1
        is_rebal = (i == 1) or (idx[i].isocalendar()[:2] != idx[i - 1].isocalendar()[:2])
        if is_rebal:
            if not mkt_ok[j]:
                for c in list(pos):
                    op = arrays[c]["O"][i]
                    if np.isnan(op):
                        continue
                    pr = pos[c]["sh"] * op * (1.0 - sell_cost)
                    cash += pr
                    trades.append(pr / pos[c]["basis"] - 1.0)
                    hold_days.append(i - pos[c]["t0"])
                    del pos[c]
            else:
                ranked = []
                for c, a in arrays.items():
                    if np.isnan(a["O"][i]) or np.isnan(a["RAW_C"][j]) or np.isnan(a["CLENOW90"][j]):
                        continue
                    if (
                        not np.isnan(a["SMA100"][j]) and not np.isnan(a["SMA200"][j])
                        and a["RAW_C"][j] > a["SMA100"][j] and a["RAW_C"][j] > a["SMA200"][j]
                        and a["CLENOW90"][j] > 0
                    ):
                        ranked.append((a["CLENOW90"][j], c))
                ranked.sort(reverse=True)
                top20 = {c for _, c in ranked[:20]}
                top10 = [c for _, c in ranked[:max_pos]]

                for c in list(pos):
                    a = arrays[c]
                    op = a["O"][i]
                    if np.isnan(op):
                        continue
                    below100 = not np.isnan(a["SMA100"][j]) and not np.isnan(a["RAW_C"][j]) and a["RAW_C"][j] < a["SMA100"][j]
                    if (c not in top20) or below100:
                        pr = pos[c]["sh"] * op * (1.0 - sell_cost)
                        cash += pr
                        trades.append(pr / pos[c]["basis"] - 1.0)
                        hold_days.append(i - pos[c]["t0"])
                        del pos[c]

                equity = cash + sum(p["sh"] * arrays[c]["C"][j] for c, p in pos.items())
                cur_hold = sum(p["sh"] * arrays[c]["C"][j] for c, p in pos.items())
                budget = max(0.0, equity * max_exposure - cur_hold)
                for c in top10:
                    if len(pos) >= max_pos or budget <= 1e-9:
                        break
                    if c in pos:
                        continue
                    amt = min(_inv_vol_amt(equity, arrays[c]["VOL20"][j]), cash, budget)
                    if amt <= 1e-9:
                        break
                    pos[c] = {"sh": amt / (arrays[c]["O"][i] * (1.0 + buy_cost)), "basis": amt, "t0": i}
                    cash -= amt
                    budget -= amt

        hold = sum(p["sh"] * arrays[c]["C"][i] for c, p in pos.items())
        tot = cash + hold
        eq.append(tot)
        inv.append(hold / tot if tot > 0 else 0.0)

    eq_s = pd.Series(eq, index=idx)
    return eq_s, calc_metrics(eq_s, inv, trades, hold_days)


def sim_dual_dynamic_idle(idx, mkt_ok, arrays, buy_cost, sell_cost, sat_variant: str = "M2e"):
    """단일 계좌 내 위성 눌림목(최대 10) + 유휴 현금 최대 60% Clenow 모멘텀 코어(최대 5)."""
    cash = 1.0
    sat_pos, core_pos = {}, {}
    eq, inv = [1.0], [0.0]
    trades, hold_days = [], []

    for i in range(1, len(idx)):
        j = i - 1
        # 1) 위성 포지션 청산 (sat_variant = M2e or M2d or M2b)
        for c in list(sat_pos):
            a, p = arrays[c], sat_pos[c]
            op = a["O"][i]
            if np.isnan(op) or np.isnan(a["RAW_C"][j]):
                continue
            c_prev = a["RAW_C"][j]
            ma20_prev, atr_prev, sma200_prev = a["MA20"][j], a["ATR14"][j], a["SMA200"][j]
            p["above20"] = p["above20"] or (not np.isnan(ma20_prev) and c_prev > ma20_prev)
            if not np.isnan(a["H"][j]):
                p["max_h"] = max(p["max_h"], a["H"][j])
            in_profit = c_prev * p["sh"] * (1.0 - sell_cost) > p["basis"]
            if sat_variant == "M2d":
                if in_profit:
                    trail = p["max_h"] - 2.5 * atr_prev if not np.isnan(atr_prev) else np.nan
                    exit_hit = bool(not np.isnan(trail) and c_prev < trail)
                else:
                    exit_hit = bool(
                        (p["entry_atr"] > 0 and c_prev < p["entry_px"] - 2.5 * p["entry_atr"])
                        or (not np.isnan(sma200_prev) and c_prev < sma200_prev)
                    )
            else:  # M2e
                if in_profit:
                    exit_hit = bool(p["above20"] and not np.isnan(ma20_prev) and c_prev < ma20_prev)
                else:
                    exit_hit = bool(
                        (not np.isnan(atr_prev) and c_prev < p["entry_px"] - 3.0 * atr_prev)
                        or (not np.isnan(sma200_prev) and c_prev < sma200_prev)
                    )
            if exit_hit:
                pr = p["sh"] * op * (1.0 - sell_cost)
                cash += pr
                trades.append(pr / p["basis"] - 1.0)
                hold_days.append(i - p["t0"])
                del sat_pos[c]

        # 2) 위성 신규 진입
        equity = cash + sum(p["sh"] * arrays[c]["C"][j] for c, p in sat_pos.items()) + sum(
            p["sh"] * arrays[c]["C"][j] for c, p in core_pos.items()
        )
        if mkt_ok[j] and len(sat_pos) < 10:
            cands = []
            for c, a in arrays.items():
                if c in sat_pos or c in core_pos or np.isnan(a["O"][i]) or np.isnan(a["RAW_C"][j]):
                    continue
                c_prev = a["RAW_C"][j]
                aligned = (
                    not np.isnan(a["SMA200"][j]) and not np.isnan(a["SMA50"][j])
                    and c_prev > a["SMA200"][j] and a["SMA50"][j] > a["SMA200"][j]
                )
                rsi2, ibs = a["RSI2"][j], a["IBS"][j]
                fast = not np.isnan(rsi2) and rsi2 < 10.0 and not np.isnan(ibs) and ibs < 0.25
                if aligned and (a["BUY"][j] or fast):
                    cands.append(c)
            cands.sort(key=lambda c: -(arrays[c]["CLENOW90"][j] if not np.isnan(arrays[c]["CLENOW90"][j]) else -1e9))
            for c in cands[: 10 - len(sat_pos)]:
                amt = min(_inv_vol_amt(equity, arrays[c]["VOL20"][j]), cash)
                if amt <= 1e-9:
                    break
                op = arrays[c]["O"][i]
                atr_j = arrays[c]["ATR14"][j]
                sat_pos[c] = {
                    "sh": amt / (op * (1.0 + buy_cost)),
                    "basis": amt,
                    "t0": i,
                    "above20": False,
                    "entry_px": op,
                    "entry_atr": float(atr_j) if not np.isnan(atr_j) else 0.0,
                    "max_h": float(op),
                }
                cash -= amt

        # 3) 주간 리밸런싱: 유휴 현금 최대 60%를 Clenow 코어 5종목에 배분
        is_rebal = (i == 1) or (idx[i].isocalendar()[:2] != idx[i - 1].isocalendar()[:2])
        if is_rebal:
            if not mkt_ok[j]:
                for c in list(core_pos):
                    op = arrays[c]["O"][i]
                    if np.isnan(op):
                        continue
                    pr = core_pos[c]["sh"] * op * (1.0 - sell_cost)
                    cash += pr
                    trades.append(pr / core_pos[c]["basis"] - 1.0)
                    hold_days.append(i - core_pos[c]["t0"])
                    del core_pos[c]
            else:
                ranked = []
                for c, a in arrays.items():
                    if np.isnan(a["O"][i]) or np.isnan(a["RAW_C"][j]) or np.isnan(a["CLENOW90"][j]):
                        continue
                    if (
                        not np.isnan(a["SMA100"][j]) and not np.isnan(a["SMA200"][j])
                        and a["RAW_C"][j] > a["SMA100"][j] and a["RAW_C"][j] > a["SMA200"][j]
                        and a["CLENOW90"][j] > 0
                    ):
                        ranked.append((a["CLENOW90"][j], c))
                ranked.sort(reverse=True)
                top15 = {c for _, c in ranked[:15]}
                top5 = [c for _, c in ranked[:8] if c not in sat_pos][:5]

                for c in list(core_pos):
                    a = arrays[c]
                    op = a["O"][i]
                    if np.isnan(op):
                        continue
                    below100 = not np.isnan(a["SMA100"][j]) and not np.isnan(a["RAW_C"][j]) and a["RAW_C"][j] < a["SMA100"][j]
                    if (c not in top15) or below100 or (c in sat_pos):
                        pr = core_pos[c]["sh"] * op * (1.0 - sell_cost)
                        cash += pr
                        trades.append(pr / core_pos[c]["basis"] - 1.0)
                        hold_days.append(i - core_pos[c]["t0"])
                        del core_pos[c]

                equity2 = cash + sum(p["sh"] * arrays[c]["C"][j] for c, p in sat_pos.items()) + sum(
                    p["sh"] * arrays[c]["C"][j] for c, p in core_pos.items()
                )
                core_val = sum(p["sh"] * arrays[c]["C"][j] for c, p in core_pos.items())
                budget = max(0.0, equity2 * 0.60 - core_val)
                for c in top5:
                    if len(core_pos) >= 5 or budget <= 1e-9:
                        break
                    if c in core_pos:
                        continue
                    amt = min(_inv_vol_amt(equity2, arrays[c]["VOL20"][j], max_w=0.12, target_risk=0.025), cash, budget)
                    if amt <= 1e-9:
                        break
                    core_pos[c] = {"sh": amt / (arrays[c]["O"][i] * (1.0 + buy_cost)), "basis": amt, "t0": i}
                    cash -= amt
                    budget -= amt

        hold = sum(p["sh"] * arrays[c]["C"][i] for c, p in sat_pos.items()) + sum(
            p["sh"] * arrays[c]["C"][i] for c, p in core_pos.items()
        )
        tot = cash + hold
        eq.append(tot)
        inv.append(hold / tot if tot > 0 else 0.0)

    eq_s = pd.Series(eq, index=idx)
    return eq_s, calc_metrics(eq_s, inv, trades, hold_days)


def blend_monthly(eq1: pd.Series, eq2: pd.Series, m1: dict, m2: dict, w1: float = 0.5) -> dict:
    r1 = eq1.pct_change().fillna(0.0)
    r2 = eq2.pct_change().fillna(0.0)
    w2 = 1.0 - w1
    v1, v2 = w1, w2
    eq = [1.0]
    prev_ym = (eq1.index[0].year, eq1.index[0].month)
    for dt_i, d1, d2 in zip(eq1.index[1:], r1.values[1:], r2.values[1:]):
        ym = (dt_i.year, dt_i.month)
        if ym != prev_ym:
            tot = v1 + v2
            v1, v2 = tot * w1, tot * w2
            prev_ym = ym
        v1 *= 1.0 + d1
        v2 *= 1.0 + d2
        eq.append(v1 + v2)
    eq_s = pd.Series(eq, index=eq1.index)
    yrs = max((eq_s.index[-1] - eq_s.index[0]).days / 365.25, 1e-6)
    cagr = 100.0 * (eq_s.iloc[-1] ** (1.0 / yrs) - 1.0)
    mdd = 100.0 * (eq_s / eq_s.cummax() - 1.0).min()
    rets = eq_s.pct_change().dropna().values
    std = float(np.std(rets))
    sharpe = float(np.mean(rets) / std * np.sqrt(252.0)) if std > 1e-12 else 0.0
    dn = float(np.sqrt(np.mean(np.minimum(rets, 0.0) ** 2)))
    sortino = float(np.mean(rets) / dn * np.sqrt(252.0)) if dn > 1e-12 else 0.0
    calmar = round(cagr / abs(mdd), 3) if mdd < -1e-6 else np.nan
    return {
        "CAGR%": round(cagr, 2),
        "MDD%": round(mdd, 1),
        "Sharpe": round(sharpe, 2),
        "Sortino": round(sortino, 2),
        "Calmar": calmar,
        "투자비중%": round(w1 * m1["투자비중%"] + w2 * m2["투자비중%"], 1),
        "거래수": m1["거래수"] + m2["거래수"],
        "승률%": round((m1["승률%"] + m2["승률%"]) / 2.0, 1),
        "거래당%": round((m1["거래당%"] + m2["거래당%"]) / 2.0, 2),
        "PF": round((m1["PF"] + m2["PF"]) / 2.0, 2),
        "평균보유일": round((m1["평균보유일"] + m2["평균보유일"]) / 2.0, 1),
    }


def run_market(name: str, frames: dict, idx_close: pd.Series, buy_cost: float, sell_cost: float):
    rows = []
    full_curves = {}
    for pname, start, end in PERIODS:
        idx, mkt_ok, arrays = prepare_arrays(frames, idx_close, start, end)

        # Benchmarks
        bench = idx_close.reindex(idx).ffill().bfill()
        b_eq = bench / bench.iloc[0]
        rows.append({"시장": name, "구간": pname, "전략": "Bench_Index_BH", **calc_metrics(b_eq, [1.0], [], [])})

        univ_ret = pd.concat([d["Close"].pct_change().reindex(idx) for d in frames.values()], axis=1).fillna(0.0).mean(axis=1)
        u_eq = (1.0 + univ_ret).cumprod()
        u_eq = u_eq / u_eq.iloc[0]
        rows.append({"시장": name, "구간": pname, "전략": "Bench_Univ_EW", **calc_metrics(u_eq, [1.0], [], [])})

        curves, stats = {"Bench": b_eq}, {}
        for label, var, mw, tr in (
            ("M0_v5_현행", "M0", 0.15, 0.025),
            ("M1_v5_역변동성1회", "M1", 0.15, 0.025),
            ("M2a_정배열_Stoch_역변동성", "M2a", 0.15, 0.025),
            ("M2b_FastPullback_RSI2_IBS_MR", "M2b", 0.15, 0.025),
            ("M2c_FastPullback_RSI2_IBS_E3", "M2c", 0.15, 0.025),
            ("M2c_v6_보수형", "M2c", 0.10, 0.016),
            ("M2d_Hybrid_Chandelier", "M2d", 0.15, 0.025),
            ("M2e_Hybrid_E3_ATRStop", "M2e", 0.15, 0.025),
        ):
            eq_s, m = sim_swing(idx, mkt_ok, arrays, buy_cost, sell_cost, var, max_w=mw, target_risk=tr)
            curves[label], stats[label] = eq_s, m
            rows.append({"시장": name, "구간": pname, "전략": label, **m})

        eq_m3, m_m3 = sim_clenow_momentum(idx, mkt_ok, arrays, buy_cost, sell_cost, max_pos=10, max_exposure=1.0)
        curves["M3_Clenow_Momentum"], stats["M3_Clenow_Momentum"] = eq_m3, m_m3
        rows.append({"시장": name, "구간": pname, "전략": "M3_Clenow_Momentum", **m_m3})

        for sat in ("M2b_FastPullback_RSI2_IBS_MR", "M2c_FastPullback_RSI2_IBS_E3", "M2d_Hybrid_Chandelier", "M2e_Hybrid_E3_ATRStop"):
            short_sat = sat.split("_")[0]
            bm = blend_monthly(eq_m3, curves[sat], m_m3, stats[sat], w1=0.5)
            rows.append({"시장": name, "구간": pname, "전략": f"M4_Dual50_{short_sat}", **bm})
            if sat == "M2c_FastPullback_RSI2_IBS_E3":
                r1 = eq_m3.pct_change().fillna(0.0)
                r2 = curves[sat].pct_change().fillna(0.0)
                v1, v2, beq, pym = 0.5, 0.5, [1.0], (eq_m3.index[0].year, eq_m3.index[0].month)
                for dti, d1, d2 in zip(eq_m3.index[1:], r1.values[1:], r2.values[1:]):
                    ym = (dti.year, dti.month)
                    if ym != pym:
                        tot = v1 + v2
                        v1, v2, pym = tot * 0.5, tot * 0.5, ym
                    v1 *= 1.0 + d1
                    v2 *= 1.0 + d2
                    beq.append(v1 + v2)
                curves["M4_Dual50_M2c"] = pd.Series(beq, index=eq_m3.index)

        _, m_m5_e = sim_dual_dynamic_idle(idx, mkt_ok, arrays, buy_cost, sell_cost, sat_variant="M2e")
        rows.append({"시장": name, "구간": pname, "전략": "M5_DualDynamic_M2e", **m_m5_e})
        _, m_m5_d = sim_dual_dynamic_idle(idx, mkt_ok, arrays, buy_cost, sell_cost, sat_variant="M2d")
        rows.append({"시장": name, "구간": pname, "전략": "M5_DualDynamic_M2d", **m_m5_d})

        # Sanity: 20 seeds random entry
        rm = []
        for seed in range(20):
            _, m_r = sim_swing(idx, mkt_ok, arrays, buy_cost, sell_cost, "RAND", rng=np.random.default_rng(seed))
            rm.append(m_r)
        r_avg = {k: round(float(np.mean([x[k] for x in rm if pd.notna(x[k])])), 2) for k in rm[0]}
        rows.append({"시장": name, "구간": pname, "전략": "Sanity_Random20", **r_avg})

        if pname == "전체(2016~2026)":
            full_curves = curves

    return pd.DataFrame(rows), full_curves


def _pick_font() -> str:
    import matplotlib.font_manager as fm
    avail = {f.name for f in fm.fontManager.ttflist}
    for nm in ("Malgun Gothic", "Apple SD Gothic Neo", "AppleGothic", "Nanum Gothic", "NanumGothic"):
        if nm in avail:
            return nm
    return "DejaVu Sans"


def save_v6_charts(df_kr: pd.DataFrame, df_us: pd.DataFrame, kr_curves: dict, us_curves: dict, img_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    BLUE, ORANGE, TEAL, INK, INK2, MUTED, GRID, SURF = (
        "#2a78d6", "#eb6834", "#0d9488", "#0b0b0b", "#52514e", "#8a8984", "#e4e3df", "#fcfcfb"
    )
    plt.rcParams.update({
        "font.family": _pick_font(), "axes.unicode_minus": False,
        "figure.facecolor": SURF, "axes.facecolor": SURF, "axes.edgecolor": GRID,
        "axes.labelcolor": INK2, "axes.grid": True, "grid.color": GRID,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    })

    keys = [
        ("Bench_Index_BH", "지수 보유(SPY / KODEX200)"),
        ("Sanity_Random20", "무작위 진입 (20시드)"),
        ("M0_v5_현행", "v5 현행 (Stoch+E3)"),
        ("M2c_v6_보수형", "v6 M2c 보수형 (채택)"),
        ("M2c_FastPullback_RSI2_IBS_E3", "v6 M2c 표준형"),
        ("M3_Clenow_Momentum", "Clenow 모멘텀 (M3)"),
        ("M4_Dual50_M2c", "듀얼 50/50 (M3+M2c)"),
    ]
    k_full = df_kr[df_kr["구간"] == "전체(2016~2026)"].set_index("전략")
    u_full = df_us[df_us["구간"] == "전체(2016~2026)"].set_index("전략")
    y = np.arange(len(keys))
    labels = [lbl for _, lbl in keys]
    h = 0.36

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13.8, 5.4), sharey=True)
    a1.barh(y + h / 2, [u_full.loc[k, "Sharpe"] for k, _ in keys], height=h, color=BLUE, label="US 우량주 65")
    a1.barh(y - h / 2, [k_full.loc[k, "Sharpe"] for k, _ in keys], height=h, color=ORANGE, label="KR 시총상위 200")
    for yi, (k, _) in zip(y, keys):
        a1.text(u_full.loc[k, "Sharpe"] + 0.02, yi + h / 2, f"{u_full.loc[k, 'Sharpe']:.2f}", va="center", fontsize=8.5, color=INK)
        a1.text(k_full.loc[k, "Sharpe"] + 0.02, yi - h / 2, f"{k_full.loc[k, 'Sharpe']:.2f}", va="center", fontsize=8.5, color=INK2)
    a1.set_yticks(y, labels)
    a1.set_xlim(0, max(u_full["Sharpe"].max(), k_full["Sharpe"].max()) * 1.18)
    a1.set_xlabel("Sharpe Ratio (연율화)")
    a1.set_title("① 위험대비 수익 (Sharpe)", loc="left", fontweight="bold")
    a1.legend(frameon=False, loc="upper right", bbox_to_anchor=(1.0, 1.08), ncol=2, fontsize=9)
    a1.grid(axis="y", visible=False)

    a2.barh(y + h / 2, [u_full.loc[k, "Calmar"] for k, _ in keys], height=h, color=BLUE, label="US 우량주 65")
    a2.barh(y - h / 2, [k_full.loc[k, "Calmar"] for k, _ in keys], height=h, color=ORANGE, label="KR 시총상위 200")
    for yi, (k, _) in zip(y, keys):
        a2.text(u_full.loc[k, "Calmar"] + 0.015, yi + h / 2, f"{u_full.loc[k, 'Calmar']:.3f}", va="center", fontsize=8.5, color=INK)
        a2.text(k_full.loc[k, "Calmar"] + 0.015, yi - h / 2, f"{k_full.loc[k, 'Calmar']:.3f}", va="center", fontsize=8.5, color=INK2)
    a2.set_xlim(0, max(u_full["Calmar"].max(), k_full["Calmar"].max()) * 1.18)
    a2.set_xlabel("Calmar Ratio (CAGR ÷ |MDD|)")
    a2.set_title("② 낙폭대비 수익 (Calmar)", loc="left", fontweight="bold")
    a2.legend(frameon=False, loc="upper right", bbox_to_anchor=(1.0, 1.08), ncol=2, fontsize=9)
    a2.grid(axis="y", visible=False)

    fig.suptitle("v6 리서치 검증 — 한국 시총 상위 200 vs 미국 우량주 65 (2016~2026)", x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.01, 0.92, "비용 반영(KR 매수 6.5bp/매도 26.5bp, US 5bp/5bp) · 미래 데이터 차단 · IS(16~21)+OOS(22~26) 통합", fontsize=10, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    img_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(img_dir / "v6_kr_vs_us.png", dpi=150)
    plt.close(fig)

    # 2) Cumulative equity curves (log/normalized to 100)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12.2, 7.6), sharex=False)
    plot_specs = [
        ("Bench", "벤치마크 (SPY / KODEX200)", MUTED, "--", 1.5),
        ("M0_v5_현행", "v5 현행 (Stoch+E3)", "#9a9993", ":", 1.6),
        ("M2c_v6_보수형", "v6 M2c 보수형 (채택)", BLUE, "-", 2.2),
        ("M2c_FastPullback_RSI2_IBS_E3", "v6 M2c 표준형", TEAL, "-", 1.9),
        ("M4_Dual50_M2c", "듀얼 50/50 (Clenow+M2c)", ORANGE, "-", 1.9),
    ]
    for ax, crv, ttl in (
        (ax1, us_curves, "① 미국 우량주 65종목 (2016~2026, 시작 = 100)"),
        (ax2, kr_curves, "② 한국 시총 상위 200종목 (2016~2026, 시작 = 100)"),
    ):
        for k, lbl, col, ls, lw in plot_specs:
            if k in crv:
                s = 100.0 * crv[k]
                ax.plot(s.index, s, color=col, ls=ls, lw=lw, label=f"{lbl} ({s.iloc[-1]-100:+.0f}%)")
        ax.axhline(100.0, color=GRID, lw=1.0)
        ax.set_ylabel("지수화 (100)")
        ax.set_title(ttl, loc="left", fontweight="bold", color=INK)
        ax.legend(frameon=False, loc="upper left", ncol=2, fontsize=9)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.suptitle("v6 누적 수익 곡선 비교 — v5 대비 기회·수익성 대폭 개선", x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(img_dir / "v6_equity_curves.png", dpi=150)
    plt.close(fig)


def main():
    RES.mkdir(parents=True, exist_ok=True)
    kr_frames, kr_idx = load_kr()
    us_frames, us_idx = load_us()
    print(f"로드 완료 — KR: {len(kr_frames)}종목, US: {len(us_frames)}종목")

    df_kr, kr_curves = run_market("KR_Top200", kr_frames, kr_idx, buy_cost=0.00065, sell_cost=0.00265)
    df_us, us_curves = run_market("US_BlueChip65", us_frames, us_idx, buy_cost=0.0005, sell_cost=0.0005)
    df_kr.to_csv(RES / "v6_research_kr.csv", index=False, encoding="utf-8-sig")
    df_us.to_csv(RES / "v6_research_us.csv", index=False, encoding="utf-8-sig")
    save_v6_charts(df_kr, df_us, kr_curves, us_curves, HERE / "docs" / "img")

    for title, d in (("KR 시총 상위 200", df_kr), ("US 우량주 65", df_us)):
        print(f"\n==================== {title} ====================")
        with pd.option_context("display.width", 260, "display.max_columns", 25, "display.max_rows", 100):
            print(d.to_string(index=False))


if __name__ == "__main__":
    main()
