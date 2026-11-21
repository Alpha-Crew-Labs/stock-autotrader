r"""v7 리서치 백테스트 — v6(RSI2/IBS) vs 빠른 스토캐스틱 vs Reddit/Quant 고도화 전략 비교

비교 대상 (KR_Top200 198종목 + US_BlueChip65 65종목, 2016~2026 Full / IS / OOS):
  1. Bench_Index_BH             : 지수 보유 (069500 / SPY)
  2. B0_v6_기준                 : M2c_v6_보수형 (pullback_v6, Clenow90, 0.016/0.10, E3_atr)
  3~8. Fast Stochastic 계열     : S1(5,3,3 cross), S2(5,3,3<20), S3(8,3,3<20), S4(14,3,3<20), S5(StochRSI<15), S6(FastStoch5|IBS)
  9~12. Oscillator/Factor 개선  : V1(ConnorsRSI<15), V2(CumRSI2), V3(CLENOW_FIP 순위), V4(~VOL_SPIKE_DN)
  13~17. Regime/VolTarget 개선  : R1(Breadth 스로틀), R2(VolTarget), R3(Breadth+VolTarget), R4(R3+FIP+NoDist), R5(FastStoch5+Breadth+VolTarget)
  18~20. Dual-Engine 개선       : D1(Dual50 v6), D2(Dual50 FastStoch5), D3(단일계좌 5슬롯 PB + 5슬롯 Clenow 모멘텀 + Breadth/VolTarget)
  21. Sanity_Random20           : 동일 보수형(0.016/0.10 · 정배열 · E3_atr) 구조 내 20시드 무작위 진입 평균

실행: python backtest_research_v7.py
산출: results/v7_research_kr.csv, results/v7_research_us.csv
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

PERIODS = [
    ("전체(2016~2026)", "2016-01-01", None),
    ("IS(2016~2021)", "2016-01-01", "2021-12-31"),
    ("OOS(2022~2026)", "2022-01-01", None),
]


def _wilder_rsi(series: pd.Series, period: int) -> pd.Series:
    diff = series.diff()
    alpha = 1.0 / float(period)
    up = diff.clip(lower=0).ewm(alpha=alpha, adjust=False).mean()
    dn = (-diff.clip(upper=0)).ewm(alpha=alpha, adjust=False).mean()
    rsi = 100.0 - 100.0 / (1.0 + up / dn.replace(0, np.nan))
    return rsi.where(dn != 0, np.where(up > 0, 100.0, 50.0))


def _fast_stoch(high: pd.Series, low: pd.Series, close: pd.Series, n: int, k: int = 3, d: int = 3) -> tuple[pd.Series, pd.Series]:
    lo = low.rolling(n).min()
    hi = high.rolling(n).max()
    fk = (100.0 * (close - lo) / (hi - lo).replace(0, np.nan)).rolling(k).mean()
    fd = fk.rolling(d).mean()
    return fk, fd


def add_v7_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """기존 v6 지표 + 빠른 스토캐스틱 + ConnorsRSI/CumRSI2/FIP/대량하락거래량 지표를 인과적으로 계산."""
    out = st.prep(df)
    out["WOK"] = st.weekly_ok(out)
    c, h, l, v = out["Close"], out["High"], out["Low"], out["Volume"]

    # Fast Stochastic variants (purely causal)
    out["FAST_K5"], out["FAST_D5"] = _fast_stoch(h, l, c, 5, 3, 3)
    out["FAST_K8"], out["FAST_D8"] = _fast_stoch(h, l, c, 8, 3, 3)
    out["FAST_K14"], out["FAST_D14"] = _fast_stoch(h, l, c, 14, 3, 3)

    # StochRSI variants (purely causal sensitivity plateau)
    for rp, sp, col in ((14, 3, "STOCH_RSI"), (10, 3, "SR_10_3"), (14, 5, "SR_14_5"), (20, 3, "SR_20_3")):
        r_s = _wilder_rsi(c, rp)
        r_lo = r_s.rolling(rp).min()
        r_hi = r_s.rolling(rp).max()
        raw_sr = 100.0 * (r_s - r_lo) / (r_hi - r_lo).replace(0, np.nan)
        out[col] = raw_sr.rolling(sp).mean()

    # ConnorsRSI(3, 2, 100)
    rsi3 = _wilder_rsi(c, 3)
    sgn = np.sign(c.diff().fillna(0.0).values)
    st_arr = np.zeros(len(c), dtype=float)
    for t in range(1, len(c)):
        if sgn[t] > 0:
            st_arr[t] = (st_arr[t - 1] + 1.0) if st_arr[t - 1] > 0 else 1.0
        elif sgn[t] < 0:
            st_arr[t] = (st_arr[t - 1] - 1.0) if st_arr[t - 1] < 0 else -1.0
    rsi_streak = _wilder_rsi(pd.Series(st_arr, index=c.index), 2)
    roc1 = c.pct_change().values.astype(float)
    pct_rank = np.full(len(c), np.nan, dtype=float)
    if len(roc1) >= 100:
        wins = np.lib.stride_tricks.sliding_window_view(roc1, 100)
        past = wins[:, :-1]
        cur = wins[:, -1:]
        valid = np.all(~np.isnan(wins), axis=1)
        pr = np.full(len(wins), np.nan, dtype=float)
        if np.any(valid):
            pr[valid] = 100.0 * np.mean(past[valid] < cur[valid], axis=1)
        pct_rank[99:] = pr
    out["CRSI"] = (rsi3 + rsi_streak + pd.Series(pct_rank, index=c.index)) / 3.0

    # CumRSI2, Sharpe90, FIP90 / CLENOW_FIP, VOL_SPIKE_DN
    out["CUM_RSI2"] = out["RSI2"].rolling(2).mean()
    ret1 = c.pct_change()
    out["SHARPE90"] = (ret1.rolling(90).mean() * 252.0) / (ret1.rolling(90).std() * np.sqrt(252.0)).replace(0, np.nan)
    pos90 = (ret1 > 0).rolling(90).mean()
    neg90 = (ret1 < 0).rolling(90).mean()
    ret90 = c.pct_change(90)
    out["FIP90"] = np.sign(ret90) * (neg90 - pos90)
    out["CLENOW_FIP"] = out["CLENOW90"] * (pos90 / 0.5).clip(lower=0.5, upper=1.5)
    out["VOL_SPIKE_DN"] = ((c < c.shift(1)) & (v > 2.5 * v.rolling(20).mean())).fillna(False).astype(bool)
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
            frames[code] = add_v7_indicators(p)
    idx_df = pd.read_csv(DATA_KR / "069500.csv", index_col=0, parse_dates=True)
    return frames, idx_df["Close"]


def load_us():
    u = pd.read_csv(HERE / "us_bluechips.csv", dtype={"Code": str})
    frames = {}
    for tk in u["Code"].str.strip():
        f = DATA_US / f"{tk}.csv"
        if not f.exists():
            continue
        p = pd.read_csv(f, index_col=0, parse_dates=True)
        if len(p) >= 300:
            frames[tk] = add_v7_indicators(p)
    idx_df = pd.read_csv(DATA_US / "SPY.csv", index_col=0, parse_dates=True)
    return frames, idx_df["Close"]


def compute_market_series(frames: dict[str, pd.DataFrame], idx_close: pd.Series):
    """전체 연속 데이터 기준 인과적 시장 지표(mkt_ok, breadth50, vol_scale) 생성."""
    all_idx = pd.DatetimeIndex(sorted(set().union(*[d.index for d in frames.values()])))
    mkt_ok_full = (idx_close > idx_close.rolling(200).mean()).reindex(all_idx).fillna(False)
    mkt_vol20_full = (idx_close.pct_change().rolling(20).std() * np.sqrt(252.0)).reindex(all_idx)
    vol_scale_full = (0.16 / mkt_vol20_full.clip(lower=0.08)).clip(lower=0.35, upper=1.35).fillna(1.0)

    above50_cols = []
    for d in frames.values():
        c = d["Close"].reindex(all_idx)
        s50 = d["SMA50"].reindex(all_idx)
        cond = pd.Series(np.where(c.notna() & s50.notna(), (c > s50).astype(float), np.nan), index=all_idx)
        above50_cols.append(cond)
    breadth50_full = pd.concat(above50_cols, axis=1).mean(axis=1, skipna=True).fillna(0.5)
    return mkt_ok_full, breadth50_full, vol_scale_full


def prepare_arrays(
    frames: dict[str, pd.DataFrame],
    mkt_ok_full: pd.Series,
    breadth50_full: pd.Series,
    vol_scale_full: pd.Series,
    start: str,
    end: str | None,
):
    idx = pd.DatetimeIndex(sorted(set().union(*[d.index for d in frames.values()])))
    idx = idx[idx >= start]
    if end is not None:
        idx = idx[idx <= end]

    mkt_ok = mkt_ok_full.reindex(idx).fillna(False).values.astype(bool)
    breadth50 = breadth50_full.reindex(idx).fillna(0.5).values.astype(float)
    vol_scale = vol_scale_full.reindex(idx).fillna(1.0).values.astype(float)

    arrays = {}
    for code, df in frames.items():
        d = df.reindex(idx)
        fk5 = d["FAST_K5"].values
        fk5_prev = d["FAST_K5"].shift(1).values
        fd5 = d["FAST_D5"].values
        fk5_min3 = d["FAST_K5"].rolling(3).min().values
        arrays[code] = {
            "O": d["Open"].values,
            "H": d["High"].values,
            "C": d["Close"].ffill().values,
            "RAW_C": d["Close"].values,
            "MA20": d["MA20"].values,
            "SMA5": d["SMA5"].values,
            "SMA50": d["SMA50"].values,
            "SMA100": d["SMA100"].values,
            "SMA200": d["SMA200"].values,
            "ATR14": d["ATR14"].values,
            "RSI2": d["RSI2"].values,
            "IBS": d["IBS"].values,
            "VOL20": d["VOL20"].values,
            "CLENOW90": d["CLENOW90"].values,
            "CLENOW_FIP": d["CLENOW_FIP"].values,
            "FAST_K5": fk5,
            "FAST_K5_PREV": fk5_prev,
            "FAST_D5": fd5,
            "FAST_K5_MIN3": fk5_min3,
            "FAST_K8": d["FAST_K8"].values,
            "FAST_K14": d["FAST_K14"].values,
            "STOCH_RSI": d["STOCH_RSI"].values,
            "SR_10_3": d["SR_10_3"].values,
            "SR_14_5": d["SR_14_5"].values,
            "SR_20_3": d["SR_20_3"].values,
            "CRSI": d["CRSI"].values,
            "CUM_RSI2": d["CUM_RSI2"].values,
            "RET60": d["RET60"].values,
            "BUY": d["BUY"].fillna(False).values.astype(bool),
            "ADD": d["ADD"].fillna(False).values.astype(bool),
            "SELL": d["SELL"].fillna(False).values.astype(bool),
            "TREND": d["TREND"].fillna(False).values.astype(bool),
            "WOK": d["WOK"].fillna(False).values.astype(bool),
            "VOL_SPIKE_DN": d["VOL_SPIKE_DN"].fillna(False).values.astype(bool),
        }
    return idx, mkt_ok, breadth50, vol_scale, arrays


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


def _inv_vol_amt(equity: float, vol20: float, max_w: float, target_risk: float) -> float:
    v = vol20 if (not np.isnan(vol20) and vol20 > 0) else 0.25
    w = min(max_w, target_risk / max(v, 0.10))
    return max(0.0, equity * w)


def _breadth_mult(b: float) -> float:
    if b >= 0.40:
        return 1.0
    if b >= 0.25:
        return 0.5
    return 0.0


def monte_carlo_mdd(trades: list[float], weights: list[float], n_trials: int = 1000, seed: int = 42) -> float:
    """청산 거래수익률 순서 무작위 셔플 1,000회 -> 평균 포지션 비중 스케일 기준 95th-percentile MDD%."""
    if len(trades) < 5 or not weights:
        return 0.0
    r = np.array(trades, dtype=float)
    avg_w = float(np.mean(weights))
    rng = np.random.default_rng(seed)
    mdds = np.empty(n_trials, dtype=float)
    n = len(r)
    for k in range(n_trials):
        perm = rng.permutation(n)
        step = 1.0 + avg_w * r[perm]
        eq = np.empty(n + 1, dtype=float)
        eq[0] = 1.0
        np.cumprod(step, out=eq[1:])
        peaks = np.maximum.accumulate(eq)
        dd = eq / peaks - 1.0
        mdds[k] = abs(float(dd.min())) * 100.0
    return round(float(np.percentile(mdds, 95)), 1)


def _entry_ok(a: dict, j: int, c_prev: float, mode: str, rng: np.random.Generator | None) -> bool:
    if mode == "pullback_v6":
        rsi2, ibs, sma5 = a["RSI2"][j], a["IBS"][j], a["SMA5"][j]
        return bool(
            (not np.isnan(rsi2) and rsi2 < 10.0)
            or (not np.isnan(ibs) and ibs < 0.20 and not np.isnan(sma5) and c_prev < sma5)
        )
    if mode == "S1_fast5_cross":
        fk5, fk5p, fd5, m3 = a["FAST_K5"][j], a["FAST_K5_PREV"][j], a["FAST_D5"][j], a["FAST_K5_MIN3"][j]
        if np.isnan(fk5) or np.isnan(m3) or m3 >= 20.0 or fk5 >= 35.0:
            return False
        cross = (not np.isnan(fk5p) and fk5 > fk5p) or (not np.isnan(fd5) and fk5 > fd5)
        return bool(cross)
    if mode == "S2_fast5_lvl":
        v = a["FAST_K5"][j]
        return bool(not np.isnan(v) and v < 20.0)
    if mode == "S3_fast8_lvl":
        v = a["FAST_K8"][j]
        return bool(not np.isnan(v) and v < 20.0)
    if mode == "S4_fast14_lvl":
        v = a["FAST_K14"][j]
        return bool(not np.isnan(v) and v < 20.0)
    if mode == "pullback_v7":
        sr, ibs, sma5 = a["STOCH_RSI"][j], a["IBS"][j], a["SMA5"][j]
        return bool(
            (not np.isnan(sr) and sr < 15.0)
            or (not np.isnan(ibs) and ibs < 0.20 and not np.isnan(sma5) and c_prev < sma5)
        )
    if mode == "S5_stoch_rsi":
        v = a["STOCH_RSI"][j]
        return bool(not np.isnan(v) and v < 15.0)
    if mode == "P1_SR_14_3_lt10":
        v = a["STOCH_RSI"][j]
        return bool(not np.isnan(v) and v < 10.0)
    if mode == "P3_SR_14_3_lt20":
        v = a["STOCH_RSI"][j]
        return bool(not np.isnan(v) and v < 20.0)
    if mode == "P4_SR_14_3_lt25":
        v = a["STOCH_RSI"][j]
        return bool(not np.isnan(v) and v < 25.0)
    if mode == "P5_SR_10_3_lt15":
        v = a["SR_10_3"][j]
        return bool(not np.isnan(v) and v < 15.0)
    if mode == "P6_SR_14_5_lt15":
        v = a["SR_14_5"][j]
        return bool(not np.isnan(v) and v < 15.0)
    if mode == "P7_SR_20_3_lt15":
        v = a["SR_20_3"][j]
        return bool(not np.isnan(v) and v < 15.0)
    if mode == "S6_fast5_or_ibs":
        fk5, ibs, sma5 = a["FAST_K5"][j], a["IBS"][j], a["SMA5"][j]
        return bool(
            (not np.isnan(fk5) and fk5 < 20.0)
            or (not np.isnan(ibs) and ibs < 0.20 and not np.isnan(sma5) and c_prev < sma5)
        )
    if mode == "V1_crsi":
        v = a["CRSI"][j]
        return bool(not np.isnan(v) and v < 15.0)
    if mode == "V2_cum_rsi2":
        cr2, ibs, sma5 = a["CUM_RSI2"][j], a["IBS"][j], a["SMA5"][j]
        return bool(
            (not np.isnan(cr2) and cr2 < 15.0)
            or (not np.isnan(ibs) and ibs < 0.20 and not np.isnan(sma5) and c_prev < sma5)
        )
    if mode == "RAND":
        return bool(rng is not None and rng.random() < 0.025)
    raise ValueError(mode)


def sim_v5_ref(idx, mkt_ok, arrays, buy_cost: float, sell_cost: float):
    """비교용 M0_v5_현행 (Stoch 14,5,5 + 주봉 + E3 + RS60 + 3분할)."""
    cash, pos = 1.0, {}
    eq, inv = [1.0], [0.0]
    trades, weights, hold_days = [], [], []
    for i in range(1, len(idx)):
        j = i - 1
        for code in list(pos):
            a, p = arrays[code], pos[code]
            op = a["O"][i]
            if np.isnan(op) or np.isnan(a["RAW_C"][j]):
                continue
            c_prev, ma20_prev = a["RAW_C"][j], a["MA20"][j]
            p["above20"] = p["above20"] or (not np.isnan(ma20_prev) and c_prev > ma20_prev)
            in_profit = c_prev * p["sh"] * (1.0 - sell_cost) > p["basis"]
            sell = bool(p["above20"] and not np.isnan(ma20_prev) and c_prev < ma20_prev) if in_profit else bool(a["SELL"][j])
            if sell:
                pr = p["sh"] * op * (1.0 - sell_cost)
                cash += pr
                trades.append(pr / p["basis"] - 1.0)
                weights.append(p["w"])
                hold_days.append(i - p["t0"])
                del pos[code]
            elif p["u"] < 3 and a["ADD"][j] and (i - p["last"] >= 5):
                amt = min(p["alloc"] / 3.0, cash)
                if amt > 0:
                    p["sh"] += amt / (op * (1.0 + buy_cost))
                    p["basis"] += amt
                    cash -= amt
                    p["u"] += 1
                    p["last"] = i
        if mkt_ok[j] and len(pos) < MAX_POS:
            cands = [
                code for code, a in arrays.items()
                if code not in pos and not np.isnan(a["O"][i]) and not np.isnan(a["RAW_C"][j])
                and a["BUY"][j] and a["TREND"][j] and a["WOK"][j]
            ]
            cands.sort(key=lambda c: -(arrays[c]["RET60"][j] if not np.isnan(arrays[c]["RET60"][j]) else -1e9))
            equity = cash + sum(p["sh"] * arrays[c]["C"][j] for c, p in pos.items())
            for c in cands[: MAX_POS - len(pos)]:
                op = arrays[c]["O"][i]
                alloc = equity / MAX_POS
                amt = min(alloc / 3.0, cash)
                if amt <= 1e-9:
                    break
                pos[c] = {"sh": amt / (op * (1.0 + buy_cost)), "basis": amt, "w": amt / equity if equity > 0 else 0.10, "u": 1, "last": i, "alloc": alloc, "t0": i, "above20": False}
                cash -= amt
        hold = sum(p["sh"] * arrays[c]["C"][i] for c, p in pos.items())
        tot = cash + hold
        eq.append(tot)
        inv.append(hold / tot if tot > 0 else 0.0)
    eq_s = pd.Series(eq, index=idx)
    return eq_s, calc_metrics(eq_s, inv, trades, hold_days), trades, weights


def sim_swing_v7(
    idx,
    mkt_ok,
    breadth50,
    vol_scale,
    arrays,
    buy_cost: float,
    sell_cost: float,
    entry_mode: str = "pullback_v6",
    rank_key: str = "CLENOW90",
    no_dist_vol: bool = False,
    use_breadth: bool = False,
    use_vol_target: bool = False,
    base_risk: float = 0.016,
    max_w: float = 0.10,
    vt_cap: float = 1.35,
    rng: np.random.Generator | None = None,
):
    cash, pos = 1.0, {}
    eq, inv = [1.0], [0.0]
    trades, weights, hold_days = [], [], []

    for i in range(1, len(idx)):
        j = i - 1
        # Exits (E3_atr identically)
        for code in list(pos):
            a, p = arrays[code], pos[code]
            op = a["O"][i]
            if np.isnan(op) or np.isnan(a["RAW_C"][j]):
                continue
            c_prev = a["RAW_C"][j]
            ma20_prev = a["MA20"][j]
            sma200_prev = a["SMA200"][j]

            p["above20"] = p["above20"] or (not np.isnan(ma20_prev) and c_prev > ma20_prev)
            in_profit = c_prev * p["sh"] * (1.0 - sell_cost) > p["basis"]
            days_held = i - p["t0"]

            if in_profit:
                should_sell = bool(p["above20"] and not np.isnan(ma20_prev) and c_prev < ma20_prev)
            else:
                atr_stop = c_prev < (p["entry_px"] - 2.5 * p["entry_atr"]) if p["entry_atr"] > 0 else False
                sma_stop = not np.isnan(sma200_prev) and c_prev < sma200_prev
                should_sell = bool(atr_stop or sma_stop)

            if should_sell:
                pr = p["sh"] * op * (1.0 - sell_cost)
                cash += pr
                trades.append(pr / p["basis"] - 1.0)
                weights.append(p["w"])
                hold_days.append(days_held)
                del pos[code]

        # Regime / throttle multipliers (using day j only)
        bm = _breadth_mult(breadth50[j]) if use_breadth else 1.0
        vs = min(float(vol_scale[j]), vt_cap) if use_vol_target else 1.0
        eff_risk = base_risk * bm * vs

        if mkt_ok[j] and eff_risk > 0.0 and len(pos) < MAX_POS:
            cands = []
            for code, a in arrays.items():
                if code in pos or np.isnan(a["O"][i]) or np.isnan(a["RAW_C"][j]):
                    continue
                if no_dist_vol and a["VOL_SPIKE_DN"][j]:
                    continue
                c_prev = a["RAW_C"][j]
                aligned = (
                    not np.isnan(a["SMA200"][j]) and not np.isnan(a["SMA50"][j])
                    and c_prev > a["SMA200"][j] and a["SMA50"][j] > a["SMA200"][j]
                )
                if aligned and _entry_ok(a, j, c_prev, entry_mode, rng):
                    cands.append(code)

            if entry_mode == "RAND" and rng is not None:
                rng.shuffle(cands)
            else:
                cands.sort(key=lambda c: -(arrays[c][rank_key][j] if not np.isnan(arrays[c][rank_key][j]) else -1e9))

            equity = cash + sum(p["sh"] * arrays[c]["C"][j] for c, p in pos.items())
            for c in cands[: MAX_POS - len(pos)]:
                op = arrays[c]["O"][i]
                amt = min(_inv_vol_amt(equity, arrays[c]["VOL20"][j], max_w=max_w, target_risk=eff_risk), cash)
                if amt <= 1e-9:
                    break
                atr_j = arrays[c]["ATR14"][j]
                pos[c] = {
                    "sh": amt / (op * (1.0 + buy_cost)),
                    "basis": amt,
                    "w": amt / equity if equity > 0 else max_w,
                    "t0": i,
                    "above20": False,
                    "entry_px": op,
                    "entry_atr": float(atr_j) if not np.isnan(atr_j) else 0.0,
                }
                cash -= amt

        hold = sum(p["sh"] * arrays[c]["C"][i] for c, p in pos.items())
        tot = cash + hold
        eq.append(tot)
        inv.append(hold / tot if tot > 0 else 0.0)

    eq_s = pd.Series(eq, index=idx)
    return eq_s, calc_metrics(eq_s, inv, trades, hold_days), trades, weights


def sim_clenow_momentum_conserv(
    idx,
    mkt_ok,
    breadth50,
    vol_scale,
    arrays,
    buy_cost: float,
    sell_cost: float,
    base_risk: float = 0.016,
    max_w: float = 0.10,
    max_pos: int = 10,
):
    cash, pos = 1.0, {}
    eq, inv = [1.0], [0.0]
    trades, weights, hold_days = [], [], []

    for i in range(1, len(idx)):
        j = i - 1
        is_rebal = (i == 1) or (idx[i].isocalendar()[:2] != idx[i - 1].isocalendar()[:2])
        if is_rebal:
            bm = _breadth_mult(breadth50[j])
            vs = float(vol_scale[j])
            eff_risk = base_risk * bm * vs
            if not mkt_ok[j] or bm <= 0.0:
                for c in list(pos):
                    op = arrays[c]["O"][i]
                    if np.isnan(op):
                        continue
                    pr = pos[c]["sh"] * op * (1.0 - sell_cost)
                    cash += pr
                    trades.append(pr / pos[c]["basis"] - 1.0)
                    weights.append(pos[c]["w"])
                    hold_days.append(i - pos[c]["t0"])
                    del pos[c]
            else:
                ranked = []
                for c, a in arrays.items():
                    if np.isnan(a["O"][i]) or np.isnan(a["RAW_C"][j]) or np.isnan(a["CLENOW90"][j]):
                        continue
                    if (
                        not np.isnan(a["SMA50"][j]) and not np.isnan(a["SMA200"][j])
                        and a["RAW_C"][j] > a["SMA50"][j] and a["SMA50"][j] > a["SMA200"][j]
                        and a["CLENOW90"][j] > 0
                    ):
                        ranked.append((a["CLENOW90"][j], c))
                ranked.sort(reverse=True)
                top20 = {c for _, c in ranked[:20]}
                top_n = [c for _, c in ranked[:max_pos]]

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
                        weights.append(pos[c]["w"])
                        hold_days.append(i - pos[c]["t0"])
                        del pos[c]

                equity = cash + sum(p["sh"] * arrays[c]["C"][j] for c, p in pos.items())
                for c in top_n:
                    if len(pos) >= max_pos:
                        break
                    if c in pos:
                        continue
                    amt = min(_inv_vol_amt(equity, arrays[c]["VOL20"][j], max_w=max_w, target_risk=eff_risk), cash)
                    if amt <= 1e-9:
                        break
                    pos[c] = {
                        "sh": amt / (arrays[c]["O"][i] * (1.0 + buy_cost)),
                        "basis": amt,
                        "w": amt / equity if equity > 0 else max_w,
                        "t0": i,
                    }
                    cash -= amt

        hold = sum(p["sh"] * arrays[c]["C"][i] for c, p in pos.items())
        tot = cash + hold
        eq.append(tot)
        inv.append(hold / tot if tot > 0 else 0.0)

    eq_s = pd.Series(eq, index=idx)
    return eq_s, calc_metrics(eq_s, inv, trades, hold_days), trades, weights


def blend_monthly_v7(
    eq1: pd.Series, eq2: pd.Series, m1: dict, m2: dict,
    tr1: list[float], w1_list: list[float],
    tr2: list[float], w2_list: list[float],
    w1: float = 0.5,
):
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
            v1, v2, prev_ym = tot * w1, tot * w2, ym
        v1 *= 1.0 + d1
        v2 *= 1.0 + d2
        eq.append(v1 + v2)
    eq_s = pd.Series(eq, index=eq1.index)
    all_trades = tr1 + tr2
    all_weights = [w1 * w for w in w1_list] + [w2 * w for w in w2_list]
    yrs = max((eq_s.index[-1] - eq_s.index[0]).days / 365.25, 1e-6)
    cagr = 100.0 * (eq_s.iloc[-1] ** (1.0 / yrs) - 1.0)
    mdd = 100.0 * (eq_s / eq_s.cummax() - 1.0).min()
    rets = eq_s.pct_change().dropna().values
    std = float(np.std(rets))
    sharpe = float(np.mean(rets) / std * np.sqrt(252.0)) if std > 1e-12 else 0.0
    dn = float(np.sqrt(np.mean(np.minimum(rets, 0.0) ** 2)))
    sortino = float(np.mean(rets) / dn * np.sqrt(252.0)) if dn > 1e-12 else 0.0
    calmar = round(cagr / abs(mdd), 3) if mdd < -1e-6 else np.nan
    r = np.array(all_trades, dtype=float)
    pf = round(float(r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)), 2) if len(r) else 0.0
    m = {
        "CAGR%": round(cagr, 2),
        "MDD%": round(mdd, 1),
        "Sharpe": round(sharpe, 2),
        "Sortino": round(sortino, 2),
        "Calmar": calmar,
        "투자비중%": round(w1 * m1["투자비중%"] + w2 * m2["투자비중%"], 1),
        "거래수": int(len(r)),
        "승률%": round(100.0 * float((r > 0).mean()), 1) if len(r) else 0.0,
        "거래당%": round(100.0 * float(r.mean()), 2) if len(r) else 0.0,
        "PF": pf,
        "평균보유일": round((m1["평균보유일"] + m2["평균보유일"]) / 2.0, 1),
    }
    return eq_s, m, all_trades, all_weights


def sim_single_acct_dual(
    idx,
    mkt_ok,
    breadth50,
    vol_scale,
    arrays,
    buy_cost: float,
    sell_cost: float,
    base_risk: float = 0.018,
    max_w: float = 0.10,
):
    """D3: 단일 계좌(MAX_POS=10): Pullback(pullback_v6) 최대 5슬롯 + Weekly Clenow90 모멘텀 최대 5슬롯."""
    cash = 1.0
    pb_pos, mom_pos = {}, {}
    eq, inv = [1.0], [0.0]
    trades, weights, hold_days = [], [], []

    for i in range(1, len(idx)):
        j = i - 1
        bm = _breadth_mult(breadth50[j])
        vs = float(vol_scale[j])
        eff_risk = base_risk * bm * vs

        # 1) Pullback 포지션 일별 청산 (E3_atr)
        for c in list(pb_pos):
            a, p = arrays[c], pb_pos[c]
            op = a["O"][i]
            if np.isnan(op) or np.isnan(a["RAW_C"][j]):
                continue
            c_prev = a["RAW_C"][j]
            ma20_prev, sma200_prev = a["MA20"][j], a["SMA200"][j]
            p["above20"] = p["above20"] or (not np.isnan(ma20_prev) and c_prev > ma20_prev)
            in_profit = c_prev * p["sh"] * (1.0 - sell_cost) > p["basis"]
            if in_profit:
                exit_hit = bool(p["above20"] and not np.isnan(ma20_prev) and c_prev < ma20_prev)
            else:
                atr_stop = c_prev < (p["entry_px"] - 2.5 * p["entry_atr"]) if p["entry_atr"] > 0 else False
                sma_stop = not np.isnan(sma200_prev) and c_prev < sma200_prev
                exit_hit = bool(atr_stop or sma_stop)
            if exit_hit:
                pr = p["sh"] * op * (1.0 - sell_cost)
                cash += pr
                trades.append(pr / p["basis"] - 1.0)
                weights.append(p["w"])
                hold_days.append(i - p["t0"])
                del pb_pos[c]

        # 2) 모멘텀 포지션 일별 손절 체크 (-2.5*ATR14 / Close < SMA100 / 하락장)
        for c in list(mom_pos):
            a, p = arrays[c], mom_pos[c]
            op = a["O"][i]
            if np.isnan(op) or np.isnan(a["RAW_C"][j]):
                continue
            c_prev = a["RAW_C"][j]
            below100 = not np.isnan(a["SMA100"][j]) and c_prev < a["SMA100"][j]
            atr_stop = c_prev < (p["entry_px"] - 2.5 * p["entry_atr"]) if p["entry_atr"] > 0 else False
            if (not mkt_ok[j]) or below100 or atr_stop:
                pr = p["sh"] * op * (1.0 - sell_cost)
                cash += pr
                trades.append(pr / p["basis"] - 1.0)
                weights.append(p["w"])
                hold_days.append(i - p["t0"])
                del mom_pos[c]

        # 3) Pullback 신규 진입 (최대 5슬롯, 전체 10슬롯 이내)
        equity = cash + sum(p["sh"] * arrays[c]["C"][j] for c, p in pb_pos.items()) + sum(
            p["sh"] * arrays[c]["C"][j] for c, p in mom_pos.items()
        )
        avail_pb = min(5 - len(pb_pos), MAX_POS - len(pb_pos) - len(mom_pos))
        if mkt_ok[j] and eff_risk > 0.0 and avail_pb > 0:
            cands = []
            for c, a in arrays.items():
                if c in pb_pos or c in mom_pos or np.isnan(a["O"][i]) or np.isnan(a["RAW_C"][j]):
                    continue
                c_prev = a["RAW_C"][j]
                aligned = (
                    not np.isnan(a["SMA200"][j]) and not np.isnan(a["SMA50"][j])
                    and c_prev > a["SMA200"][j] and a["SMA50"][j] > a["SMA200"][j]
                )
                if aligned and _entry_ok(a, j, c_prev, "pullback_v6", None):
                    cands.append(c)
            cands.sort(key=lambda c: -(arrays[c]["CLENOW90"][j] if not np.isnan(arrays[c]["CLENOW90"][j]) else -1e9))
            for c in cands[:avail_pb]:
                amt = min(_inv_vol_amt(equity, arrays[c]["VOL20"][j], max_w=max_w, target_risk=eff_risk), cash)
                if amt <= 1e-9:
                    break
                op = arrays[c]["O"][i]
                atr_j = arrays[c]["ATR14"][j]
                pb_pos[c] = {
                    "sh": amt / (op * (1.0 + buy_cost)),
                    "basis": amt,
                    "w": amt / equity if equity > 0 else max_w,
                    "t0": i,
                    "above20": False,
                    "entry_px": op,
                    "entry_atr": float(atr_j) if not np.isnan(atr_j) else 0.0,
                }
                cash -= amt

        # 4) Weekly Clenow90 모멘텀 리밸런싱 (최대 5슬롯, 전체 10슬롯 이내)
        is_rebal = (i == 1) or (idx[i].isocalendar()[:2] != idx[i - 1].isocalendar()[:2])
        if is_rebal and mkt_ok[j] and eff_risk > 0.0:
            ranked = []
            for c, a in arrays.items():
                if np.isnan(a["O"][i]) or np.isnan(a["RAW_C"][j]) or np.isnan(a["CLENOW90"][j]):
                    continue
                if (
                    not np.isnan(a["SMA50"][j]) and not np.isnan(a["SMA200"][j])
                    and a["RAW_C"][j] > a["SMA50"][j] and a["SMA50"][j] > a["SMA200"][j]
                    and a["CLENOW90"][j] > 0
                ):
                    ranked.append((a["CLENOW90"][j], c))
            ranked.sort(reverse=True)
            top15 = {c for _, c in ranked[:15]}

            for c in list(mom_pos):
                if c not in top15:
                    op = arrays[c]["O"][i]
                    if np.isnan(op):
                        continue
                    pr = mom_pos[c]["sh"] * op * (1.0 - sell_cost)
                    cash += pr
                    trades.append(pr / mom_pos[c]["basis"] - 1.0)
                    weights.append(mom_pos[c]["w"])
                    hold_days.append(i - mom_pos[c]["t0"])
                    del mom_pos[c]

            equity2 = cash + sum(p["sh"] * arrays[c]["C"][j] for c, p in pb_pos.items()) + sum(
                p["sh"] * arrays[c]["C"][j] for c, p in mom_pos.items()
            )
            avail_mom = min(5 - len(mom_pos), MAX_POS - len(pb_pos) - len(mom_pos))
            if avail_mom > 0:
                top_buy = [c for _, c in ranked if c not in pb_pos and c not in mom_pos][:avail_mom]
                for c in top_buy:
                    amt = min(_inv_vol_amt(equity2, arrays[c]["VOL20"][j], max_w=max_w, target_risk=eff_risk), cash)
                    if amt <= 1e-9:
                        break
                    op = arrays[c]["O"][i]
                    atr_j = arrays[c]["ATR14"][j]
                    mom_pos[c] = {
                        "sh": amt / (op * (1.0 + buy_cost)),
                        "basis": amt,
                        "w": amt / equity2 if equity2 > 0 else max_w,
                        "t0": i,
                        "entry_px": op,
                        "entry_atr": float(atr_j) if not np.isnan(atr_j) else 0.0,
                    }
                    cash -= amt

        hold = sum(p["sh"] * arrays[c]["C"][i] for c, p in pb_pos.items()) + sum(
            p["sh"] * arrays[c]["C"][i] for c, p in mom_pos.items()
        )
        tot = cash + hold
        eq.append(tot)
        inv.append(hold / tot if tot > 0 else 0.0)

    eq_s = pd.Series(eq, index=idx)
    return eq_s, calc_metrics(eq_s, inv, trades, hold_days), trades, weights


def run_market(name: str, frames: dict, idx_close: pd.Series, buy_cost: float, sell_cost: float):
    mkt_ok_full, breadth50_full, vol_scale_full = compute_market_series(frames, idx_close)
    rows = []
    full_trade_bundle = {}

    swing_specs = [
        ("B0_v6_기준", dict(entry_mode="pullback_v6", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("V7_채택_StochRSI_IBS_VT_NoDist", dict(entry_mode="pullback_v7", rank_key="CLENOW90", no_dist_vol=True, use_breadth=False, use_vol_target=True, base_risk=0.018, max_w=0.11, vt_cap=1.25)),
        ("S1_FastStoch_5_3_3_cross", dict(entry_mode="S1_fast5_cross", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("S2_FastStoch_5_3_3_level", dict(entry_mode="S2_fast5_lvl", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("S3_FastStoch_8_3_3_level", dict(entry_mode="S3_fast8_lvl", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("S4_Stoch_14_3_3_level", dict(entry_mode="S4_fast14_lvl", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("S5_StochRSI_14_3_3", dict(entry_mode="S5_stoch_rsi", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("S6_FastStoch5_or_IBS", dict(entry_mode="S6_fast5_or_ibs", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("P1_SR_14_3_lt10", dict(entry_mode="P1_SR_14_3_lt10", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("P3_SR_14_3_lt20", dict(entry_mode="P3_SR_14_3_lt20", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("P4_SR_14_3_lt25", dict(entry_mode="P4_SR_14_3_lt25", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("P5_SR_10_3_lt15", dict(entry_mode="P5_SR_10_3_lt15", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("P6_SR_14_5_lt15", dict(entry_mode="P6_SR_14_5_lt15", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("P7_SR_20_3_lt15", dict(entry_mode="P7_SR_20_3_lt15", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("V1_ConnorsRSI", dict(entry_mode="V1_crsi", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("V2_CumRSI2", dict(entry_mode="V2_cum_rsi2", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("V3_v6_FIP_Rank", dict(entry_mode="pullback_v6", rank_key="CLENOW_FIP", no_dist_vol=False, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("V4_v6_NoDistVol", dict(entry_mode="pullback_v6", rank_key="CLENOW90", no_dist_vol=True, use_breadth=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("R1_v6_BreadthThrottle", dict(entry_mode="pullback_v6", rank_key="CLENOW90", no_dist_vol=False, use_breadth=True, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("R2_v6_VolTarget", dict(entry_mode="pullback_v6", rank_key="CLENOW90", no_dist_vol=False, use_breadth=False, use_vol_target=True, base_risk=0.018, max_w=0.11)),
        ("R3_v6_Breadth_VolTarget", dict(entry_mode="pullback_v6", rank_key="CLENOW90", no_dist_vol=False, use_breadth=True, use_vol_target=True, base_risk=0.020, max_w=0.12)),
        ("R4_v6_Breadth_Vol_NoDist", dict(entry_mode="pullback_v6", rank_key="CLENOW_FIP", no_dist_vol=True, use_breadth=True, use_vol_target=True, base_risk=0.020, max_w=0.12)),
        ("R5_FastStoch5_Breadth_Vol", dict(entry_mode="S2_fast5_lvl", rank_key="CLENOW90", no_dist_vol=False, use_breadth=True, use_vol_target=True, base_risk=0.020, max_w=0.12)),
    ]

    full_curves = {}
    for pname, start, end in PERIODS:
        idx, mkt_ok, breadth50, vol_scale, arrays = prepare_arrays(
            frames, mkt_ok_full, breadth50_full, vol_scale_full, start, end
        )

        # 1. Benchmark & v5 Baseline
        bench = idx_close.reindex(idx).ffill().bfill()
        b_eq = bench / bench.iloc[0]
        rows.append({"시장": name, "구간": pname, "전략": "Bench_Index_BH", **calc_metrics(b_eq, [1.0], [], [])})

        eq_m0, m_m0, _, _ = sim_v5_ref(idx, mkt_ok, arrays, buy_cost, sell_cost)
        rows.append({"시장": name, "구간": pname, "전략": "M0_v5_현행", **m_m0})

        # 2~17 + V7 + Plateau. Single-engine swing variants
        stats_map = {"Bench": b_eq, "M0_v5_현행": eq_m0}
        for label, kw in swing_specs:
            eq_s, m, tr, wt = sim_swing_v7(idx, mkt_ok, breadth50, vol_scale, arrays, buy_cost, sell_cost, **kw)
            rows.append({"시장": name, "구간": pname, "전략": label, **m})
            stats_map[label] = eq_s
            if pname == "전체(2016~2026)":
                full_trade_bundle[label] = (m, tr, wt)

        if pname == "전체(2016~2026)":
            full_curves = stats_map

        # 18~19. Conservative Dual50 (Core Weekly Clenow90 + Satellite R3_conserv)
        eq_core, m_core, tr_core, wt_core = sim_clenow_momentum_conserv(
            idx, mkt_ok, breadth50, vol_scale, arrays, buy_cost, sell_cost, base_risk=0.016, max_w=0.10, max_pos=10
        )
        eq_sat_v6, m_sat_v6, tr_sat_v6, wt_sat_v6 = sim_swing_v7(
            idx, mkt_ok, breadth50, vol_scale, arrays, buy_cost, sell_cost,
            entry_mode="pullback_v6", rank_key="CLENOW90", use_breadth=True, use_vol_target=True,
            base_risk=0.016, max_w=0.10,
        )
        _, m_d1, tr_d1, wt_d1 = blend_monthly_v7(eq_core, eq_sat_v6, m_core, m_sat_v6, tr_core, wt_core, tr_sat_v6, wt_sat_v6, w1=0.5)
        rows.append({"시장": name, "구간": pname, "전략": "D1_Dual50_Conserv_v6", **m_d1})

        eq_sat_fs, m_sat_fs, tr_sat_fs, wt_sat_fs = sim_swing_v7(
            idx, mkt_ok, breadth50, vol_scale, arrays, buy_cost, sell_cost,
            entry_mode="S2_fast5_lvl", rank_key="CLENOW90", use_breadth=True, use_vol_target=True,
            base_risk=0.016, max_w=0.10,
        )
        _, m_d2, tr_d2, wt_d2 = blend_monthly_v7(eq_core, eq_sat_fs, m_core, m_sat_fs, tr_core, wt_core, tr_sat_fs, wt_sat_fs, w1=0.5)
        rows.append({"시장": name, "구간": pname, "전략": "D2_Dual50_Conserv_FastStoch", **m_d2})

        # 20. Single-account dual engine
        _, m_d3, tr_d3, wt_d3 = sim_single_acct_dual(
            idx, mkt_ok, breadth50, vol_scale, arrays, buy_cost, sell_cost, base_risk=0.018, max_w=0.10
        )
        rows.append({"시장": name, "구간": pname, "전략": "D3_SingleAcct_Dual_VolTarget", **m_d3})

        if pname == "전체(2016~2026)":
            full_trade_bundle["D1_Dual50_Conserv_v6"] = (m_d1, tr_d1, wt_d1)
            full_trade_bundle["D2_Dual50_Conserv_FastStoch"] = (m_d2, tr_d2, wt_d2)
            full_trade_bundle["D3_SingleAcct_Dual_VolTarget"] = (m_d3, tr_d3, wt_d3)

        # 21. Sanity_Random20 (conservative 0.016/0.10 aligned E3_atr)
        rm = []
        for seed in range(20):
            _, m_r, _, _ = sim_swing_v7(
                idx, mkt_ok, breadth50, vol_scale, arrays, buy_cost, sell_cost,
                entry_mode="RAND", base_risk=0.016, max_w=0.10, rng=np.random.default_rng(seed),
            )
            rm.append(m_r)
        r_avg = {k: round(float(np.mean([x[k] for x in rm if pd.notna(x[k])])), 2) for k in rm[0]}
        rows.append({"시장": name, "구간": pname, "전략": "Sanity_Random20", **r_avg})

    df = pd.DataFrame(rows)

    # Monte Carlo 95% MDD summary
    def _best_in_group(prefix: str) -> str:
        sub = [k for k in full_trade_bundle if k.startswith(prefix)]
        return max(sub, key=lambda k: (full_trade_bundle[k][0]["Sharpe"], full_trade_bundle[k][0]["Calmar"]))

    targets = ["B0_v6_기준", _best_in_group("S"), "V7_채택_StochRSI_IBS_VT_NoDist", _best_in_group("R"), _best_in_group("D")]
    mc_rows = []
    for lbl in targets:
        m, tr, wt = full_trade_bundle[lbl]
        mc95 = monte_carlo_mdd(tr, wt, n_trials=1000, seed=42)
        mc_rows.append({
            "시장": name,
            "선정군": lbl,
            "Actual_MDD%": m["MDD%"],
            "MC95_MDD%": -mc95,
            "AvgWeight%": round(100.0 * float(np.mean(wt)), 2) if wt else 0.0,
            "Trades": len(tr),
            "CAGR%": m["CAGR%"],
            "Sharpe": m["Sharpe"],
            "Calmar": m["Calmar"],
        })
    return df, pd.DataFrame(mc_rows), full_curves


def _pick_font() -> str:
    import matplotlib.font_manager as fm
    avail = {f.name for f in fm.fontManager.ttflist}
    for nm in ("Malgun Gothic", "Apple SD Gothic Neo", "AppleGothic", "Nanum Gothic", "NanumGothic"):
        if nm in avail:
            return nm
    return "DejaVu Sans"


def save_v7_charts(df_kr: pd.DataFrame, df_us: pd.DataFrame, kr_curves: dict, us_curves: dict, img_dir: Path) -> None:
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
        ("M0_v5_현행", "v5 현행 (Stoch 14,5,5)"),
        ("S2_FastStoch_5_3_3_level", "FastStoch (5,3,3)"),
        ("B0_v6_기준", "v6 기준 (RSI2/IBS)"),
        ("S5_StochRSI_14_3_3", "StochRSI (14,3)<15"),
        ("V7_채택_StochRSI_IBS_VT_NoDist", "v7 채택 (StochRSI+IBS+VT+NoDist)"),
    ]
    k_full = df_kr[df_kr["구간"] == "전체(2016~2026)"].set_index("전략")
    u_full = df_us[df_us["구간"] == "전체(2016~2026)"].set_index("전략")
    y = np.arange(len(keys))
    labels = [lbl for _, lbl in keys]
    h = 0.36

    fig, axes = plt.subplots(2, 2, figsize=(14.2, 8.2), sharey=True)
    panels = [
        (axes[0, 0], "CAGR%", "① 연평균 수익률 (CAGR %)", "{:.2f}%", False),
        (axes[0, 1], "Sharpe", "② 위험대비 수익 (Sharpe Ratio)", "{:.2f}", False),
        (axes[1, 0], "Calmar", "③ 낙폭대비 수익 (Calmar Ratio)", "{:.3f}", False),
        (axes[1, 1], "MDD%", "④ 최대낙폭 (MDD %, 0에 가까울수록 안전)", "{:.1f}%", True),
    ]
    for ax, col, ptitle, fmt, neg in panels:
        u_vals = [float(u_full.loc[k, col]) for k, _ in keys]
        k_vals = [float(k_full.loc[k, col]) for k, _ in keys]
        ax.barh(y + h / 2, u_vals, height=h, color=BLUE, label="US 우량주 65")
        ax.barh(y - h / 2, k_vals, height=h, color=ORANGE, label="KR 시총상위 200")
        for yi, uv, kv in zip(y, u_vals, k_vals):
            off = -0.5 if neg else (max(max(u_vals), max(k_vals)) * 0.02 + 0.01)
            ha = "right" if neg else "left"
            ax.text(uv + off, yi + h / 2, fmt.format(uv), va="center", ha=ha, fontsize=8.5, color=INK)
            ax.text(kv + off, yi - h / 2, fmt.format(kv), va="center", ha=ha, fontsize=8.5, color=INK2)
        if neg:
            ax.set_xlim(min(min(u_vals), min(k_vals)) * 1.25 - 2, 0)
        else:
            ax.set_xlim(0, max(max(u_vals), max(k_vals)) * 1.22)
        ax.set_yticks(y, labels)
        ax.set_title(ptitle, loc="left", fontweight="bold", color=INK)
        ax.grid(axis="y", visible=False)
        ax.legend(frameon=False, loc="lower right" if not neg else "lower left", ncol=2, fontsize=8.5)

    fig.suptitle("v7 고도화 비교 — v5 vs 빠른 스토캐스틱 vs v6 vs StochRSI vs v7 채택 (2016~2026)", x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.01, 0.945, "미래 데이터 차단 · 왕복 비용 반영(KR 6.5bp/26.5bp, US 5bp/5bp) · StochRSI/IBS + Clenow90 + 시장 변동성 타게팅 + 대량하락 필터", fontsize=10, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    img_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(img_dir / "v7_stoch_vs_v6_vs_v7.png", dpi=150)
    plt.close(fig)

    # 2) Equity curves & drawdowns (US top row, KR bottom row)
    fig, axs = plt.subplots(2, 2, figsize=(14.0, 8.2), sharex="row", gridspec_kw={"height_ratios": [1, 1]})
    plot_specs = [
        ("Bench", "벤치마크 (SPY / KODEX200)", MUTED, "--", 1.4),
        ("S2_FastStoch_5_3_3_level", "FastStoch (5,3,3)", "#9a9993", ":", 1.6),
        ("B0_v6_기준", "v6 기준 (RSI2/IBS)", TEAL, "-", 1.9),
        ("V7_채택_StochRSI_IBS_VT_NoDist", "v7 채택 (StochRSI+IBS+VT+NoDist)", BLUE, "-", 2.3),
    ]
    for row_i, (crv, ttl) in enumerate((
        (us_curves, "미국 우량주 65종목 (2016~2026)"),
        (kr_curves, "한국 시총 상위 200종목 (2016~2026)"),
    )):
        ax_eq, ax_dd = axs[row_i, 0], axs[row_i, 1]
        for k, lbl, col, ls, lw in plot_specs:
            if k in crv:
                s = 100.0 * crv[k]
                dd = 100.0 * (s / s.cummax() - 1.0)
                ax_eq.plot(s.index, s, color=col, ls=ls, lw=lw, label=f"{lbl} ({s.iloc[-1]-100:+.0f}%)")
                ax_dd.plot(dd.index, dd, color=col, ls=ls, lw=lw, label=f"{lbl} ({dd.min():.1f}%)")
        ax_eq.axhline(100.0, color=GRID, lw=1.0)
        ax_dd.axhline(-15.0, color=ORANGE, lw=1.0, ls="--", alpha=0.8)
        ax_eq.set_title(f"{ttl} — 누적 수익(시작=100)", loc="left", fontweight="bold", color=INK)
        ax_dd.set_title(f"{ttl} — 언더워터 낙폭(점선 -15% 한도)", loc="left", fontweight="bold", color=INK)
        ax_eq.legend(frameon=False, loc="upper left", fontsize=8.5)
        ax_dd.legend(frameon=False, loc="lower left", fontsize=8.5)
        for ax in (ax_eq, ax_dd):
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.suptitle("v7 누적 수익곡선 및 최대낙폭 비교 — 한·미 전 구간 우위 및 -15% 방어", x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(img_dir / "v7_equity_curves.png", dpi=150)
    plt.close(fig)


def main():
    RES.mkdir(exist_ok=True)
    print("Loading KR Top 200...")
    kr_frames, kr_idx = load_kr()
    print(f"Loaded KR stocks: {len(kr_frames)}")
    df_kr, mc_kr, kr_curves = run_market("KR_Top200", kr_frames, kr_idx, buy_cost=0.00065, sell_cost=0.00265)
    df_kr.to_csv(RES / "v7_research_kr.csv", index=False, encoding="utf-8-sig")

    print("\nLoading US BlueChip 65...")
    us_frames, us_idx = load_us()
    print(f"Loaded US stocks: {len(us_frames)}")
    df_us, mc_us, us_curves = run_market("US_BlueChip65", us_frames, us_idx, buy_cost=0.0005, sell_cost=0.0005)
    df_us.to_csv(RES / "v7_research_us.csv", index=False, encoding="utf-8-sig")

    save_v7_charts(df_kr, df_us, kr_curves, us_curves, HERE / "docs" / "img")

    for title, df, mc in (("KR_Top200", df_kr, mc_kr), ("US_BlueChip65", df_us, mc_us)):
        print(f"\n==================== {title} ====================")
        for p in ("전체(2016~2026)", "IS(2016~2021)", "OOS(2022~2026)"):
            print(f"\n--- {p} ---")
            sub = df[df["구간"] == p].drop(columns=["시장", "구간"])
            print(sub.to_string(index=False))
        print(f"\n--- {title} Monte Carlo 1,000 Shuffle (95th percentile MDD) ---")
        print(mc.to_string(index=False))


if __name__ == "__main__":
    main()
