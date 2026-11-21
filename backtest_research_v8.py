r"""v8 최종 리서치 백테스트 — v7 vs Reddit/Quant 고도화 전략 전면 검증 (KR_Top200 + US_BlueChip65, 2016~2026).

비교군:
  - 기준선/Sanity (5): Bench_Index_BH, M0_v5_현행, B0_v6_기준, B1_v7_현행, Sanity_Random20
  - Axis A 청산/손절 어블레이션 (5): X1_TimeStop15, X2_TimeStop20, X3_RegimeLossExit, X4_Chandelier3ATR, X5_VolSpikeExit
  - Axis B 레짐/DD/분산 캡 어블레이션 (4~5): G1_VolRatioBrake, G2_DDBrake_7_10, G3_YellowLight, G4a_CorrCap65, (US) G4b_SectorCap2
  - Axis C 시그널/팩터 어블레이션 (4): F1_RisingSMA50, F2_RS60_Leader, F3_RiskAdjMom_Rank, F4_MultiOsc_Confirm
  - Axis D 순수 주식 조합 (4): P1_SafeCore, P2_SafeTrend, P3_SafeFull, P4_v7_DD_Corr_RegLoss
  - Axis E 현금파킹/방어 TAA 오버레이 (5): T1_v7_CashPark, T2_v7_RiskOff_GoldCash, T3_P1_CashPark, T4_P1_RiskOff_GoldCash, T5_P4_RiskOff_GoldCash

산출:
  - results/v8_research_kr.csv, results/v8_research_us.csv
  - results/v8_annual_kr.csv, results/v8_annual_us.csv
  - results/v8_stress_mc.csv
"""
from __future__ import annotations

import time
from pathlib import Path
import numpy as np
import pandas as pd

import strategy as st
from backtest_research_v7 import (
    _breadth_mult,
    _entry_ok,
    _inv_vol_amt,
    _wilder_rsi,
    add_v7_indicators,
    monte_carlo_mdd,
    sim_v5_ref,
)

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


def add_v8_indicators(df: pd.DataFrame, bench_ret60: pd.Series) -> pd.DataFrame:
    """v7 지표 + v8 인과적 추가 지표(50일선 기울기, 지수대비 60일 초과수익, 위험조정 모멘텀)."""
    out = add_v7_indicators(df)
    out["SMA50_SLOPE10"] = (out["SMA50"] > out["SMA50"].shift(10)).fillna(False).astype(bool)
    b_r60 = bench_ret60.reindex(out.index, method="ffill")
    out["RS60_EXCESS"] = out["RET60"] - b_r60
    out["SKIP_MOM_VOL"] = out["SKIP_MOM"] / out["VOL20"].replace(0, np.nan)
    return out


def _read_clean_ohlcv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df.sort_index()


def load_kr_v8():
    u = pd.read_csv(DATA_KR / "universe200.csv", dtype={"Code": str})
    idx_df = _read_clean_ohlcv(DATA_KR / "069500.csv")
    bench_ret60 = idx_df["Close"].pct_change(60)

    frames = {}
    for code in u["Code"]:
        f = DATA_KR / f"{code}.csv"
        if not f.exists():
            continue
        p = _read_clean_ohlcv(f)
        if len(p) >= 300:
            frames[code] = add_v8_indicators(p, bench_ret60)

    cash_etf = _read_clean_ohlcv(DATA_KR / "153130.csv")
    gold_etf = _read_clean_ohlcv(DATA_KR / "132030.csv")
    return frames, idx_df["Close"], cash_etf, gold_etf, {}


def load_us_v8():
    u = pd.read_csv(HERE / "us_bluechips.csv", dtype={"Code": str})
    idx_df = _read_clean_ohlcv(DATA_US / "SPY.csv")
    bench_ret60 = idx_df["Close"].pct_change(60)

    frames = {}
    sector_map = {}
    for _, row in u.iterrows():
        tk = str(row["Code"]).strip()
        f = DATA_US / f"{tk}.csv"
        if not f.exists():
            continue
        p = _read_clean_ohlcv(f)
        if len(p) >= 300:
            frames[tk] = add_v8_indicators(p, bench_ret60)
            sector_map[tk] = str(row["Sector"]).strip()

    cash_etf = _read_clean_ohlcv(DATA_US / "BIL.csv")
    gold_etf = _read_clean_ohlcv(DATA_US / "GLD.csv")
    return frames, idx_df["Close"], cash_etf, gold_etf, sector_map


def compute_market_series_v8(
    frames: dict[str, pd.DataFrame],
    idx_close: pd.Series,
    cash_etf: pd.DataFrame,
    gold_etf: pd.DataFrame,
):
    """전체 연속 데이터 기준 인과적 시장·방어ETF 지표 생성 (v7 mkt_ok / vol_scale 완전 호환)."""
    all_idx = pd.DatetimeIndex(sorted(set().union(*[d.index for d in frames.values()])))
    mkt_ok_full = (idx_close > idx_close.rolling(200).mean()).reindex(all_idx).fillna(False)
    bench_above50_full = (idx_close > idx_close.rolling(50).mean()).reindex(all_idx).fillna(False)

    idx_ret = idx_close.pct_change()
    mkt_vol20_full = (idx_ret.rolling(20).std() * np.sqrt(252.0)).reindex(all_idx)
    mkt_vol60_full = (idx_ret.rolling(60).std() * np.sqrt(252.0)).reindex(all_idx)
    vol_scale_full = (0.16 / mkt_vol20_full.clip(lower=0.08)).clip(lower=0.35, upper=1.35).fillna(1.0)
    vol_ratio_full = (mkt_vol20_full / mkt_vol60_full.replace(0, np.nan)).fillna(1.0)

    gold_c_raw = gold_etf["Close"]
    gold_ok_raw = (gold_c_raw > gold_c_raw.rolling(200).mean()).fillna(False)
    gold_ok_full = gold_ok_raw.reindex(all_idx, method="ffill").fillna(False)

    # Align ETF Open / Close causally onto all_idx (forward-fill Close; Open falls back to ffilled Close only if exchange holiday mismatch)
    cash_c_full = cash_etf["Close"].reindex(all_idx, method="ffill")
    cash_o_full = cash_etf["Open"].reindex(all_idx).fillna(cash_c_full)
    gold_c_full = gold_etf["Close"].reindex(all_idx, method="ffill")
    gold_o_full = gold_etf["Open"].reindex(all_idx).fillna(gold_c_full)

    # Full daily return matrix across stocks for strictly causal 60d rolling correlation lookup
    codes = list(frames.keys())
    ret_cols = [frames[c]["Close"].pct_change().reindex(all_idx).values.astype(float) for c in codes]
    full_rets_mat = np.vstack(ret_cols)  # shape (N_stocks, len(all_idx))

    return {
        "all_idx": all_idx,
        "codes": codes,
        "mkt_ok": mkt_ok_full,
        "bench_above50": bench_above50_full,
        "vol_scale": vol_scale_full,
        "vol_ratio": vol_ratio_full,
        "gold_ok": gold_ok_full,
        "cash_o": cash_o_full,
        "cash_c": cash_c_full,
        "gold_o": gold_o_full,
        "gold_c": gold_c_full,
        "full_rets_mat": full_rets_mat,
    }


def prepare_arrays_v8(frames: dict[str, pd.DataFrame], mkt: dict, start: str, end: str | None):
    all_idx = mkt["all_idx"]
    idx = all_idx[all_idx >= start]
    if end is not None:
        idx = idx[idx <= end]

    pos_in_all = all_idx.get_indexer(idx)

    code_to_row = {c: r for r, c in enumerate(mkt["codes"])}
    arrays = {}
    for code, df in frames.items():
        d = df.reindex(idx)
        arrays[code] = {
            "row_idx": code_to_row[code],
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
            "SKIP_MOM_VOL": d["SKIP_MOM_VOL"].values,
            "STOCH_RSI": d["STOCH_RSI"].values,
            "RET60": d["RET60"].values,
            "RS60_EXCESS": d["RS60_EXCESS"].values,
            "BUY": d["BUY"].fillna(False).values.astype(bool),
            "ADD": d["ADD"].fillna(False).values.astype(bool),
            "SELL": d["SELL"].fillna(False).values.astype(bool),
            "TREND": d["TREND"].fillna(False).values.astype(bool),
            "WOK": d["WOK"].fillna(False).values.astype(bool),
            "VOL_SPIKE_DN": d["VOL_SPIKE_DN"].fillna(False).values.astype(bool),
            "SMA50_SLOPE10": d["SMA50_SLOPE10"].fillna(False).values.astype(bool),
        }

    p_mkt = {
        "mkt_ok": mkt["mkt_ok"].reindex(idx).fillna(False).values.astype(bool),
        "bench_above50": mkt["bench_above50"].reindex(idx).fillna(False).values.astype(bool),
        "vol_scale": mkt["vol_scale"].reindex(idx).fillna(1.0).values.astype(float),
        "vol_ratio": mkt["vol_ratio"].reindex(idx).fillna(1.0).values.astype(float),
        "gold_ok": mkt["gold_ok"].reindex(idx).fillna(False).values.astype(bool),
        "cash_o": mkt["cash_o"].reindex(idx).values.astype(float),
        "cash_c": mkt["cash_c"].reindex(idx).values.astype(float),
        "gold_o": mkt["gold_o"].reindex(idx).values.astype(float),
        "gold_c": mkt["gold_c"].reindex(idx).values.astype(float),
        "pos_in_all": pos_in_all,
        "full_rets_mat": mkt["full_rets_mat"],
    }
    return idx, p_mkt, arrays


def calc_metrics_v8(
    eq: pd.Series,
    inv: list[float],
    def_inv: list[float],
    rf_close: np.ndarray,
    trades: list[float],
    hold_days: list[int],
    etf_trades: int = 0,
) -> dict:
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-6)
    cagr = 100.0 * (eq.iloc[-1] ** (1.0 / yrs) - 1.0)
    mdd = 100.0 * (eq / eq.cummax() - 1.0).min()
    rets = eq.pct_change().dropna().values
    std = float(np.std(rets))
    sharpe = float(np.mean(rets) / std * np.sqrt(252.0)) if std > 1e-12 else 0.0
    dn = float(np.sqrt(np.mean(np.minimum(rets, 0.0) ** 2)))
    sortino = float(np.mean(rets) / dn * np.sqrt(252.0)) if dn > 1e-12 else 0.0
    calmar = round(cagr / abs(mdd), 3) if mdd < -1e-6 else np.nan

    # Excess Sharpe over risk-free short bond ETF daily returns
    rf_s = pd.Series(rf_close, index=eq.index).ffill()
    rf_rets = rf_s.pct_change().dropna().values
    if len(rf_rets) == len(rets) and len(rets) > 0:
        ex_rets = rets - rf_rets
        ex_std = float(np.std(ex_rets))
        ex_sharpe = float(np.mean(ex_rets) / ex_std * np.sqrt(252.0)) if ex_std > 1e-12 else 0.0
    else:
        ex_sharpe = sharpe

    r = np.array(trades, dtype=float)
    pf = round(float(r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)), 2) if len(r) else 0.0
    return {
        "CAGR%": round(cagr, 2),
        "MDD%": round(mdd, 1),
        "Sharpe": round(sharpe, 2),
        "Sortino": round(sortino, 2),
        "Calmar": calmar,
        "ExSharpe": round(ex_sharpe, 2),
        "투자비중%": round(100.0 * float(np.mean(inv)), 1) if inv else 0.0,
        "방어비중%": round(100.0 * float(np.mean(def_inv)), 1) if def_inv else 0.0,
        "거래수": int(len(r)),
        "승률%": round(100.0 * float((r > 0).mean()), 1) if len(r) else 0.0,
        "거래당%": round(100.0 * float(r.mean()), 2) if len(r) else 0.0,
        "PF": pf,
        "평균보유일": round(float(np.mean(hold_days)), 1) if hold_days else 0.0,
        "ETF거래수": int(etf_trades),
    }


def _corr_exceeds(full_rets_mat: np.ndarray, all_j: int, cand_row: int, held_rows: list[int], thresh: float) -> bool:
    if not held_rows:
        return False
    w0 = max(0, all_j - 59)
    x = full_rets_mat[cand_row, w0 : all_j + 1]
    x_ok = ~np.isnan(x)
    for hr in held_rows:
        y = full_rets_mat[hr, w0 : all_j + 1]
        m = x_ok & (~np.isnan(y))
        if int(m.sum()) < 30:
            continue
        xm = x[m] - x[m].mean()
        ym = y[m] - y[m].mean()
        denom = float(np.sqrt(np.dot(xm, xm) * np.dot(ym, ym)))
        if denom > 1e-12:
            if float(np.dot(xm, ym) / denom) > thresh:
                return True
    return False


def sim_v8(
    idx: pd.DatetimeIndex,
    p_mkt: dict,
    arrays: dict,
    sector_map: dict,
    buy_cost: float,
    sell_cost: float,
    etf_cost: float = 0.0005,
    *,
    entry_mode: str = "pullback_v7",
    rank_key: str = "CLENOW90",
    no_dist_vol: bool = True,
    use_vol_target: bool = True,
    base_risk: float = 0.018,
    max_w: float = 0.11,
    vt_cap: float = 1.25,
    # Axis A: Exits
    time_stop_days: int | None = None,
    regime_loss_exit: bool = False,
    chandelier_mult: float | None = None,
    vol_spike_exit: bool = False,
    # Axis B: Regime & Caps
    vol_ratio_brake: float | None = None,
    soft_dd_brake: bool = False,
    yellow_light_mult: float | None = None,
    corr_cap: float | None = None,
    sector_cap: int | None = None,
    # Axis C: Signals / Factors
    require_slope50: bool = False,
    require_rs60: bool = False,
    multi_osc_confirm: bool = False,
    # Axis E: Defensive ETF overlay
    use_cash_park: bool = False,
    gold_riskoff_weight: float = 0.0,
    rng: np.random.Generator | None = None,
):
    mkt_ok = p_mkt["mkt_ok"]
    bench_above50 = p_mkt["bench_above50"]
    vol_scale = p_mkt["vol_scale"]
    vol_ratio = p_mkt["vol_ratio"]
    gold_ok = p_mkt["gold_ok"]
    cash_o = p_mkt["cash_o"]
    cash_c = p_mkt["cash_c"]
    gold_o = p_mkt["gold_o"]
    gold_c = p_mkt["gold_c"]
    pos_in_all = p_mkt["pos_in_all"]
    full_rets_mat = p_mkt["full_rets_mat"]

    cash = 1.0
    pos: dict[str, dict] = {}
    cash_etf_sh = 0.0
    gold_etf_sh = 0.0
    etf_trades = 0

    eq = [1.0]
    inv = [0.0]
    def_inv = [0.0]
    trades, weights, hold_days = [], [], []

    peak_eq = 1.0
    dd_mult = 1.0
    dd50_days = 0

    for i in range(1, len(idx)):
        j = i - 1
        c_oi, c_pc, c_ci = cash_o[i], cash_c[j], cash_c[i]
        g_oi, g_pc, g_ci = gold_o[i], gold_c[j], gold_c[i]

        # 1. Equity Exits (day i Open, signals strictly <= day j)
        for code in list(pos):
            a, p = arrays[code], pos[code]
            op = a["O"][i]
            if np.isnan(op) or np.isnan(a["RAW_C"][j]):
                continue
            c_prev = a["RAW_C"][j]
            h_prev = a["H"][j]
            if not np.isnan(h_prev):
                p["max_high"] = max(p["max_high"], float(h_prev))
            ma20_prev = a["MA20"][j]
            sma200_prev = a["SMA200"][j]

            p["above20"] = p["above20"] or (not np.isnan(ma20_prev) and c_prev > ma20_prev)
            in_profit = c_prev * p["sh"] * (1.0 - sell_cost) > p["basis"]
            days_held = i - p["t0"]

            if in_profit:
                if chandelier_mult is not None:
                    atr_j = a["ATR14"][j]
                    should_sell = bool(not np.isnan(atr_j) and c_prev < p["max_high"] - chandelier_mult * float(atr_j))
                else:
                    should_sell = bool(p["above20"] and not np.isnan(ma20_prev) and c_prev < ma20_prev)
            else:
                atr_stop = c_prev < (p["entry_px"] - 2.5 * p["entry_atr"]) if p["entry_atr"] > 0 else False
                sma_stop = not np.isnan(sma200_prev) and c_prev < sma200_prev
                t_stop = (time_stop_days is not None) and (days_held >= time_stop_days)
                r_stop = regime_loss_exit and (not mkt_ok[j])
                v_stop = vol_spike_exit and bool(a["VOL_SPIKE_DN"][j])
                should_sell = bool(atr_stop or sma_stop or t_stop or r_stop or v_stop)

            if should_sell:
                pr = p["sh"] * op * (1.0 - sell_cost)
                cash += pr
                trades.append(pr / p["basis"] - 1.0)
                weights.append(p["w"])
                hold_days.append(days_held)
                del pos[code]

        # 2. Causal Regime Multipliers (<= day j)
        if soft_dd_brake:
            dd_j = eq[-1] / peak_eq - 1.0
            if dd_j <= -0.08:
                dd_mult = 0.5
            elif dd_j > -0.04:
                dd_mult = 1.0
            if dd_mult == 0.5:
                dd50_days += 1

        vr_ok = True if vol_ratio_brake is None else bool(vol_ratio[j] <= vol_ratio_brake)
        yl_mult = (yellow_light_mult if (yellow_light_mult is not None and not bench_above50[j]) else 1.0)
        vs = min(float(vol_scale[j]), vt_cap) if use_vol_target else 1.0
        eff_risk = base_risk * vs * dd_mult * yl_mult

        # 3. Equity Entries
        planned_buys: list[tuple[str, float]] = []
        held_rows = [arrays[c]["row_idx"] for c in pos]
        sector_counts: dict[str, int] = {}
        if sector_cap is not None:
            for c in pos:
                sec = sector_map.get(c, "Other")
                sector_counts[sec] = sector_counts.get(sec, 0) + 1

        if mkt_ok[j] and vr_ok and eff_risk > 0.0 and len(pos) < MAX_POS:
            cands = []
            for code, a in arrays.items():
                if code in pos or np.isnan(a["O"][i]) or np.isnan(a["RAW_C"][j]):
                    continue
                if no_dist_vol and a["VOL_SPIKE_DN"][j]:
                    continue
                if require_slope50 and not a["SMA50_SLOPE10"][j]:
                    continue
                if require_rs60 and (np.isnan(a["RS60_EXCESS"][j]) or a["RS60_EXCESS"][j] <= 0.0):
                    continue
                c_prev = a["RAW_C"][j]
                aligned = (
                    not np.isnan(a["SMA200"][j]) and not np.isnan(a["SMA50"][j])
                    and c_prev > a["SMA200"][j] and a["SMA50"][j] > a["SMA200"][j]
                )
                if not aligned:
                    continue
                if multi_osc_confirm:
                    sr, rsi2, ibs, sma5 = a["STOCH_RSI"][j], a["RSI2"][j], a["IBS"][j], a["SMA5"][j]
                    sig = (
                        (not np.isnan(sr) and sr < 15.0 and not np.isnan(ibs) and ibs < 0.40)
                        or (not np.isnan(rsi2) and rsi2 < 10.0 and not np.isnan(ibs) and ibs < 0.40)
                        or (not np.isnan(ibs) and ibs < 0.15 and not np.isnan(sma5) and c_prev < sma5)
                    )
                else:
                    sig = _entry_ok(a, j, c_prev, entry_mode, rng)
                if sig:
                    cands.append(code)

            if entry_mode == "RAND" and rng is not None:
                rng.shuffle(cands)
            else:
                cands.sort(key=lambda c: -(arrays[c][rank_key][j] if not np.isnan(arrays[c][rank_key][j]) else -1e9))

            equity_j = (
                cash
                + sum(p["sh"] * arrays[c]["C"][j] for c, p in pos.items())
                + cash_etf_sh * (c_pc if not np.isnan(c_pc) else c_oi)
                + gold_etf_sh * (g_pc if not np.isnan(g_pc) else g_oi)
            )
            avail_liq = cash + (cash_etf_sh * c_oi * (1.0 - etf_cost) if (use_cash_park and not np.isnan(c_oi)) else 0.0)
            rem_liq = avail_liq

            all_j = int(pos_in_all[j])
            slots = MAX_POS - len(pos)
            for c in cands:
                if len(planned_buys) >= slots:
                    break
                if sector_cap is not None:
                    sec = sector_map.get(c, "Other")
                    if sector_counts.get(sec, 0) >= sector_cap:
                        continue
                if corr_cap is not None:
                    if _corr_exceeds(full_rets_mat, all_j, arrays[c]["row_idx"], held_rows, corr_cap):
                        continue
                amt = min(_inv_vol_amt(equity_j, arrays[c]["VOL20"][j], max_w=max_w, target_risk=eff_risk), rem_liq)
                if amt <= 1e-9:
                    break
                planned_buys.append((c, amt))
                rem_liq -= amt
                held_rows.append(arrays[c]["row_idx"])
                if sector_cap is not None:
                    sec = sector_map.get(c, "Other")
                    sector_counts[sec] = sector_counts.get(sec, 0) + 1

            # Raise cash from cash_etf if needed
            need_cash = sum(amt for _, amt in planned_buys)
            if need_cash > cash and use_cash_park and cash_etf_sh > 0 and not np.isnan(c_oi) and c_oi > 0:
                sh_sell = min(cash_etf_sh, (need_cash - cash) / (c_oi * (1.0 - etf_cost)))
                if sh_sell > 1e-12:
                    cash += sh_sell * c_oi * (1.0 - etf_cost)
                    cash_etf_sh -= sh_sell
                    etf_trades += 1

            equity_base = equity_j if equity_j > 0 else 1.0
            for c, amt_req in planned_buys:
                amt = min(amt_req, cash)
                if amt <= 1e-9:
                    break
                op = arrays[c]["O"][i]
                atr_j = arrays[c]["ATR14"][j]
                pos[c] = {
                    "sh": amt / (op * (1.0 + buy_cost)),
                    "basis": amt,
                    "w": amt / equity_base,
                    "t0": i,
                    "above20": False,
                    "entry_px": op,
                    "max_high": float(op),
                    "entry_atr": float(atr_j) if not np.isnan(atr_j) else 0.0,
                }
                cash -= amt

        # 4. Defensive ETF Overlay (at day i Open, 5% deadband, strictly <= day j signals)
        if use_cash_park or gold_riskoff_weight > 0.0:
            stock_est = sum(p["sh"] * (arrays[c]["O"][i] if p["t0"] == i else arrays[c]["C"][j]) for c, p in pos.items())
            gold_val = gold_etf_sh * g_oi if not np.isnan(g_oi) else 0.0
            cash_etf_val = cash_etf_sh * c_oi if not np.isnan(c_oi) else 0.0
            eq_est = cash + stock_est + gold_val + cash_etf_val

            target_gold_w = gold_riskoff_weight if ((not mkt_ok[j]) and gold_ok[j]) else 0.0
            target_gold_amt = target_gold_w * eq_est

            # Gold sell first when weight drops to 0, or if excess exceeds 5% deadband
            if target_gold_w == 0.0 and gold_etf_sh > 0 and not np.isnan(g_oi) and g_oi > 0:
                cash += gold_etf_sh * g_oi * (1.0 - etf_cost)
                gold_etf_sh = 0.0
                etf_trades += 1
            elif target_gold_w > 0.0 and not np.isnan(g_oi) and g_oi > 0:
                if abs(target_gold_amt - gold_val) > 0.05 * eq_est:
                    if gold_val > target_gold_amt:
                        sh_sell = min(gold_etf_sh, (gold_val - target_gold_amt) / (g_oi * (1.0 - etf_cost)))
                        if sh_sell > 1e-12:
                            cash += sh_sell * g_oi * (1.0 - etf_cost)
                            gold_etf_sh -= sh_sell
                            etf_trades += 1
                    else:
                        buy_need = target_gold_amt - gold_val
                        if buy_need > cash - 0.02 * eq_est and cash_etf_sh > 0 and not np.isnan(c_oi) and c_oi > 0:
                            shortfall = buy_need - max(0.0, cash - 0.02 * eq_est)
                            sh_sell = min(cash_etf_sh, shortfall / (c_oi * (1.0 - etf_cost)))
                            if sh_sell > 1e-12:
                                cash += sh_sell * c_oi * (1.0 - etf_cost)
                                cash_etf_sh -= sh_sell
                                cash_etf_val = cash_etf_sh * c_oi
                                etf_trades += 1
                        actual_buy = min(buy_need, max(0.0, cash - 0.02 * eq_est))
                        if actual_buy > 1e-9:
                            gold_etf_sh += actual_buy / (g_oi * (1.0 + etf_cost))
                            cash -= actual_buy
                            etf_trades += 1

            # Cash-parking ETF rebalance (leaving 2% cash buffer, 5% deadband)
            if use_cash_park and not np.isnan(c_oi) and c_oi > 0:
                cash_etf_val = cash_etf_sh * c_oi
                excess_cash = cash - 0.02 * eq_est
                if abs(excess_cash) > 0.05 * eq_est:
                    if excess_cash > 0:
                        buy_amt = min(excess_cash, cash)
                        if buy_amt > 1e-9:
                            cash_etf_sh += buy_amt / (c_oi * (1.0 + etf_cost))
                            cash -= buy_amt
                            etf_trades += 1
                    else:
                        sh_sell = min(cash_etf_sh, (-excess_cash) / (c_oi * (1.0 - etf_cost)))
                        if sh_sell > 1e-12:
                            cash += sh_sell * c_oi * (1.0 - etf_cost)
                            cash_etf_sh -= sh_sell
                            etf_trades += 1

        hold_eq = sum(p["sh"] * arrays[c]["C"][i] for c, p in pos.items())
        hold_def = (cash_etf_sh * c_ci if not np.isnan(c_ci) else 0.0) + (gold_etf_sh * g_ci if not np.isnan(g_ci) else 0.0)
        tot = cash + hold_eq + hold_def
        eq.append(tot)
        if tot > peak_eq:
            peak_eq = tot
        inv.append(hold_eq / tot if tot > 0 else 0.0)
        def_inv.append(hold_def / tot if tot > 0 else 0.0)

    eq_s = pd.Series(eq, index=idx)
    m = calc_metrics_v8(eq_s, inv, def_inv, cash_c, trades, hold_days, etf_trades=etf_trades)
    m["DD50일수"] = dd50_days
    return eq_s, m, trades, weights


def _build_specs(is_us: bool) -> list[tuple[str, dict]]:
    v7_base = dict(entry_mode="pullback_v7", rank_key="CLENOW90", no_dist_vol=True, use_vol_target=True, base_risk=0.018, max_w=0.11, vt_cap=1.25)
    c2_base = {**v7_base, "vol_ratio_brake": 1.50, "yellow_light_mult": 0.5}
    c3_base = {**v7_base, "vol_ratio_brake": 1.50, "soft_dd_brake": True}
    c4_base = {**v7_base, "vol_ratio_brake": 1.50, "yellow_light_mult": 0.5, "soft_dd_brake": True}
    c5_base = {**v7_base, "vol_ratio_brake": 1.50, "yellow_light_mult": 0.7, "soft_dd_brake": True}

    specs = [
        ("B0_v6_기준", dict(entry_mode="pullback_v6", rank_key="CLENOW90", no_dist_vol=False, use_vol_target=False, base_risk=0.016, max_w=0.10)),
        ("B1_v7_현행", dict(v7_base)),
        # Axis A: Exits
        ("X1_TimeStop15", {**v7_base, "time_stop_days": 15}),
        ("X2_TimeStop20", {**v7_base, "time_stop_days": 20}),
        ("X3_RegimeLossExit", {**v7_base, "regime_loss_exit": True}),
        ("X4_Chandelier3ATR", {**v7_base, "chandelier_mult": 3.0}),
        ("X5_VolSpikeExit", {**v7_base, "vol_spike_exit": True}),
        # Axis B: Regime / Non-deadlock Soft DD / Diversification Caps & VolRatio Plateau
        ("VR_135", {**v7_base, "vol_ratio_brake": 1.35}),
        ("G1_VolRatioBrake", {**v7_base, "vol_ratio_brake": 1.50}),
        ("VR_165", {**v7_base, "vol_ratio_brake": 1.65}),
        ("VR_180", {**v7_base, "vol_ratio_brake": 1.80}),
        ("G2_SoftDD_8_Half", {**v7_base, "soft_dd_brake": True}),
        ("G3_YellowLight", {**v7_base, "yellow_light_mult": 0.5}),
        ("G4a_CorrCap65", {**v7_base, "corr_cap": 0.65}),
    ]
    if is_us:
        specs.append(("G4b_SectorCap2", {**v7_base, "sector_cap": 2}))
    specs.extend([
        # Axis C: Signals / Factors
        ("F1_RisingSMA50", {**v7_base, "require_slope50": True}),
        ("F2_RS60_Leader", {**v7_base, "require_rs60": True}),
        ("F3_RiskAdjMom_Rank", {**v7_base, "rank_key": "SKIP_MOM_VOL"}),
        ("F4_MultiOsc_Confirm", {**v7_base, "multi_osc_confirm": True}),
        # Axis D: Non-deadlock pure equity combinations (0% cash)
        ("C2_VolBrake_Yellow50", c2_base),
        ("C3_VolBrake_SoftDD", c3_base),
        ("C4_VolBrake_Yellow50_SoftDD", c4_base),
        ("C5_VolBrake_Yellow70_SoftDD", c5_base),
        # Axis E: Cash-parking overlays (BIL / 153130, 2% buffer, 5% deadband) + Gold ref
        ("T1_v7_CashPark", {**v7_base, "use_cash_park": True}),
        ("T2_v7_RiskOff_GoldCash", {**v7_base, "use_cash_park": True, "gold_riskoff_weight": 0.35}),
        ("V8a_VolBrake_CashPark", {**v7_base, "vol_ratio_brake": 1.50, "use_cash_park": True}),
        ("V8b_VolBrake_Yellow50_CashPark", {**c2_base, "use_cash_park": True}),
        ("V8c_VolBrake_SoftDD_CashPark", {**c3_base, "use_cash_park": True}),
        ("V8d_VolBrake_Yellow50_SoftDD_CashPark", {**c4_base, "use_cash_park": True}),
        ("V8e_VolBrake_Yellow70_SoftDD_CashPark", {**c5_base, "use_cash_park": True}),
    ])
    return specs


def _annual_returns(eq_s: pd.Series) -> dict[str, float]:
    out = {}
    yr_groups = eq_s.groupby(eq_s.index.year)
    prev_end = None
    for yr, grp in yr_groups:
        base = prev_end if prev_end is not None else grp.iloc[0]
        out[str(yr)] = round(100.0 * (grp.iloc[-1] / base - 1.0), 2)
        prev_end = grp.iloc[-1]
    return out


def run_market_v8(
    name: str,
    frames: dict,
    idx_close: pd.Series,
    cash_etf: pd.DataFrame,
    gold_etf: pd.DataFrame,
    sector_map: dict,
    buy_cost: float,
    sell_cost: float,
    etf_cost: float = 0.0005,
):
    is_us = name.startswith("US")
    mkt = compute_market_series_v8(frames, idx_close, cash_etf, gold_etf)
    specs = _build_specs(is_us)

    rows = []
    full_curves = {}
    full_bundles = {}

    for pname, start, end in PERIODS:
        idx, p_mkt, arrays = prepare_arrays_v8(frames, mkt, start, end)
        rf_close = p_mkt["cash_c"]

        # 1. Benchmark & v5 reference
        bench = idx_close.reindex(idx).ffill().bfill()
        b_eq = bench / bench.iloc[0]
        bm = calc_metrics_v8(b_eq, [1.0], [0.0], rf_close, [], [])
        bm["DD50일수"] = 0
        rows.append({"시장": name, "구간": pname, "전략": "Bench_Index_BH", **bm})

        eq_m0, m_m0_raw, _, _ = sim_v5_ref(idx, p_mkt["mkt_ok"], arrays, buy_cost, sell_cost)
        # Recompute ExSharpe cleanly on eq_m0
        m_m0 = {
            **m_m0_raw,
            "ExSharpe": calc_metrics_v8(eq_m0, [m_m0_raw["투자비중%"] / 100.0], [0.0], rf_close, [], [])["ExSharpe"],
            "방어비중%": 0.0,
            "ETF거래수": 0,
            "DD50일수": 0,
        }
        rows.append({"시장": name, "구간": pname, "전략": "M0_v5_현행", **m_m0})

        if pname == "전체(2016~2026)":
            full_curves["Bench_Index_BH"] = b_eq
            full_curves["M0_v5_현행"] = eq_m0

        for label, kw in specs:
            eq_s, m, tr, wt = sim_v8(
                idx, p_mkt, arrays, sector_map,
                buy_cost=buy_cost, sell_cost=sell_cost, etf_cost=etf_cost, **kw,
            )
            rows.append({"시장": name, "구간": pname, "전략": label, **m})
            if pname == "전체(2016~2026)":
                full_curves[label] = eq_s
                full_bundles[label] = (m, tr, wt)

        # Sanity_Random20 (aligned + E3_atr + v7 sizing)
        rm = []
        for seed in range(20):
            _, m_r, _, _ = sim_v8(
                idx, p_mkt, arrays, sector_map,
                buy_cost=buy_cost, sell_cost=sell_cost, etf_cost=etf_cost,
                entry_mode="RAND", no_dist_vol=False, use_vol_target=True,
                base_risk=0.018, max_w=0.11, vt_cap=1.25, rng=np.random.default_rng(seed),
            )
            rm.append(m_r)
        r_avg = {k: round(float(np.mean([x[k] for x in rm if pd.notna(x[k])])), 2) for k in rm[0]}
        rows.append({"시장": name, "구간": pname, "전략": "Sanity_Random20", **r_avg})

    df = pd.DataFrame(rows)

    # Pick top 3 single-ablation strategies by Full Sharpe
    single_labels = [lbl for lbl, _ in specs if lbl[0] in ("X", "G", "F")]
    top3_single = sorted(
        single_labels,
        key=lambda k: (full_bundles[k][0]["Sharpe"], full_bundles[k][0]["Calmar"]),
        reverse=True,
    )[:3]

    # Build annual returns table
    ann_keys = [
        "Bench_Index_BH", "B0_v6_기준", "B1_v7_현행",
        "G1_VolRatioBrake", "G2_SoftDD_8_Half", "G3_YellowLight",
        "C2_VolBrake_Yellow50", "C3_VolBrake_SoftDD", "C4_VolBrake_Yellow50_SoftDD", "C5_VolBrake_Yellow70_SoftDD",
        "T1_v7_CashPark", "V8a_VolBrake_CashPark", "V8b_VolBrake_Yellow50_CashPark",
        "V8c_VolBrake_SoftDD_CashPark", "V8d_VolBrake_Yellow50_SoftDD_CashPark", "V8e_VolBrake_Yellow70_SoftDD_CashPark",
    ]
    ann_map = {}
    ann_rows = []
    for k in ann_keys:
        if k in full_curves:
            ar = _annual_returns(full_curves[k])
            ann_map[k] = ar
            ann_rows.append({"시장": name, "전략": k, **ar})
    df_ann = pd.DataFrame(ann_rows)

    # 2x cost stress test + Monte Carlo 1,000 shuffle + WinYears/11
    stress_keys = [
        "B0_v6_기준", "B1_v7_현행", "G1_VolRatioBrake",
        "C2_VolBrake_Yellow50", "C3_VolBrake_SoftDD", "C4_VolBrake_Yellow50_SoftDD", "C5_VolBrake_Yellow70_SoftDD",
        "T1_v7_CashPark", "V8a_VolBrake_CashPark", "V8b_VolBrake_Yellow50_CashPark",
        "V8c_VolBrake_SoftDD_CashPark", "V8d_VolBrake_Yellow50_SoftDD_CashPark", "V8e_VolBrake_Yellow70_SoftDD_CashPark",
    ]
    spec_dict = dict(specs)
    stress_rows = []
    idx_full, p_mkt_full, arr_full = prepare_arrays_v8(frames, mkt, "2016-01-01", None)
    idx_oos, p_mkt_oos, arr_oos = prepare_arrays_v8(frames, mkt, "2022-01-01", None)

    for lbl in stress_keys:
        kw = spec_dict[lbl]
        m_1x, tr_1x, wt_1x = full_bundles[lbl]
        mc95 = monte_carlo_mdd(tr_1x, wt_1x, n_trials=1000, seed=42)
        ar = ann_map.get(lbl, _annual_returns(full_curves[lbl]))
        win_yrs = sum(1 for v in ar.values() if v > 0.0)
        _, m_2x_full, _, _ = sim_v8(
            idx_full, p_mkt_full, arr_full, sector_map,
            buy_cost=buy_cost * 2.0, sell_cost=sell_cost * 2.0, etf_cost=etf_cost * 2.0, **kw,
        )
        _, m_2x_oos, _, _ = sim_v8(
            idx_oos, p_mkt_oos, arr_oos, sector_map,
            buy_cost=buy_cost * 2.0, sell_cost=sell_cost * 2.0, etf_cost=etf_cost * 2.0, **kw,
        )
        stress_rows.append({
            "시장": name,
            "전략": lbl,
            "WinYears/11": f"{win_yrs}/{len(ar)}",
            "1x_CAGR%": m_1x["CAGR%"],
            "1x_MDD%": m_1x["MDD%"],
            "1x_Sharpe": m_1x["Sharpe"],
            "1x_ExSh": m_1x["ExSharpe"],
            "MC95_MDD%": -mc95,
            "2xFull_CAGR%": m_2x_full["CAGR%"],
            "2xFull_MDD%": m_2x_full["MDD%"],
            "2xFull_Sharpe": m_2x_full["Sharpe"],
            "2xOOS_CAGR%": m_2x_oos["CAGR%"],
            "2xOOS_MDD%": m_2x_oos["MDD%"],
            "2xOOS_Sharpe": m_2x_oos["Sharpe"],
        })
    return df, df_ann, pd.DataFrame(stress_rows), top3_single, full_curves


def _pick_font() -> str:
    import matplotlib.font_manager as fm
    avail = {f.name for f in fm.fontManager.ttflist}
    for nm in ("Malgun Gothic", "Apple SD Gothic Neo", "AppleGothic", "Nanum Gothic", "NanumGothic"):
        if nm in avail:
            return nm
    return "DejaVu Sans"


def save_v8_charts(df_kr: pd.DataFrame, df_us: pd.DataFrame, kr_curves: dict, us_curves: dict, img_dir: Path) -> None:
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
        ("B0_v6_기준", "v6 기준 (RSI2/IBS)"),
        ("B1_v7_현행", "v7 현행 (StochRSI+VT)"),
        ("C2_VolBrake_Yellow50", "v8 순수주식 (VolBrake+노란불)"),
        ("V8b_VolBrake_Yellow50_CashPark", "v8 최종본 (단기채 파킹 포함)"),
    ]
    k_full = df_kr[df_kr["구간"] == "전체(2016~2026)"].set_index("전략")
    u_full = df_us[df_us["구간"] == "전체(2016~2026)"].set_index("전략")
    y = np.arange(len(keys))
    labels = [lbl for _, lbl in keys]
    h = 0.36

    fig, axes = plt.subplots(2, 2, figsize=(14.6, 8.6), sharey=True)
    panels = [
        (axes[0, 0], "CAGR%", "① 연평균 수익률 (CAGR %)", "{:.2f}%", False),
        (axes[0, 1], "Sharpe", "② 총 위험대비 수익 (Sharpe)", "{:.2f}", False),
        (axes[1, 0], "ExSharpe", "③ 단기채 초과수익 샤프 (ExSharpe)", "{:.2f}", False),
        (axes[1, 1], "MDD%", "④ 최대낙폭 (MDD %, 0에 가까울수록 안전)", "{:.1f}%", True),
    ]
    for ax, col, ptitle, fmt, neg in panels:
        u_vals = [float(u_full.loc[k, col]) for k, _ in keys]
        k_vals = [float(k_full.loc[k, col]) for k, _ in keys]
        ax.barh(y + h / 2, u_vals, height=h, color=BLUE, label="US 우량주 65")
        ax.barh(y - h / 2, k_vals, height=h, color=ORANGE, label="KR 시총상위 200")
        span = max(max(u_vals), max(k_vals)) - min(min(u_vals), min(k_vals), 0.0)
        pad = span * 0.02 + (0.45 if neg else 0.01)
        for yi, uv, kv in zip(y, u_vals, k_vals):
            for val, y_pos, txt_col in ((uv, yi + h / 2, INK), (kv, yi - h / 2, INK2)):
                is_neg = val < 0
                ax.text(
                    val - pad if is_neg else val + pad,
                    y_pos,
                    fmt.format(val),
                    va="center",
                    ha="right" if is_neg else "left",
                    fontsize=8.5,
                    color=txt_col,
                )
        if neg:
            ax.set_xlim(min(min(u_vals), min(k_vals)) * 1.28 - 2.0, 0)
            leg_loc = "lower left"
        else:
            lo_x = min(min(u_vals), min(k_vals), 0.0)
            hi_x = max(max(u_vals), max(k_vals)) * 1.26
            ax.set_xlim(lo_x * 1.28 - 0.15 if lo_x < 0 else 0, hi_x)
            leg_loc = "upper left" if lo_x < 0 else "lower right"
        ax.set_yticks(y, labels)
        ax.set_title(ptitle, loc="left", fontweight="bold", color=INK)
        ax.grid(axis="y", visible=False)
        ax.legend(frameon=False, loc=leg_loc, ncol=2 if col != "ExSharpe" else 1, fontsize=8.5)

    fig.suptitle("v8 최종본 고도화 비교 — v5 vs v6 vs v7 vs v8 순수주식 vs v8 최종본 (2016~2026)", x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.01, 0.945, "미래 데이터 차단 · 왕복 비용 반영(KR 주식 6.5/26.5bp·ETF 5bp, US 5/5bp) · VolRatio(<=1.50) + 노란불(0.5x) + 단기채(153130/BIL) 파킹", fontsize=10, color=INK2)
    fig.tight_layout(rect=(0, 0, 1, 0.925))
    img_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(img_dir / "v8_final_comparison.png", dpi=150)
    plt.close(fig)

    fig, axs = plt.subplots(2, 2, figsize=(14.4, 8.6), sharex="row", gridspec_kw={"height_ratios": [1, 1]})
    plot_specs = [
        ("Bench_Index_BH", "벤치마크 (SPY / KODEX200)", MUTED, "--", 1.3),
        ("B0_v6_기준", "v6 기준 (RSI2/IBS)", "#9a9993", ":", 1.5),
        ("B1_v7_현행", "v7 현행 (StochRSI+VT)", TEAL, "-", 1.7),
        ("C2_VolBrake_Yellow50", "v8 순수주식 (VolBrake+노란불)", ORANGE, "-.", 1.9),
        ("V8b_VolBrake_Yellow50_CashPark", "v8 최종본 (단기채 파킹 포함)", BLUE, "-", 2.3),
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
        ax_eq.legend(frameon=False, loc="upper left", fontsize=8.2)
        ax_dd.legend(frameon=False, loc="lower left", fontsize=8.2)
        for ax in (ax_eq, ax_dd):
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.suptitle("v8 최종본 누적 수익곡선 및 낙폭 비교 — 순수주식·현금파킹 동시 우위 (2016~2026)", x=0.01, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(img_dir / "v8_equity_curves.png", dpi=150)
    plt.close(fig)


def main():
    t0 = time.perf_counter()
    RES.mkdir(exist_ok=True)

    print("Loading KR Top 200 + Defensive ETFs (153130, 132030)...")
    kr_frames, kr_idx, kr_cash, kr_gold, kr_sec = load_kr_v8()
    print(f"Loaded KR stocks: {len(kr_frames)}")
    df_kr, ann_kr, st_kr, top3_kr, kr_curves = run_market_v8(
        "KR_Top200", kr_frames, kr_idx, kr_cash, kr_gold, kr_sec,
        buy_cost=0.00065, sell_cost=0.00265, etf_cost=0.0005,
    )
    df_kr.to_csv(RES / "v8_research_kr.csv", index=False, encoding="utf-8-sig")
    ann_kr.to_csv(RES / "v8_annual_kr.csv", index=False, encoding="utf-8-sig")

    print("\nLoading US BlueChip 65 + Defensive ETFs (BIL, GLD)...")
    us_frames, us_idx, us_cash, us_gold, us_sec = load_us_v8()
    print(f"Loaded US stocks: {len(us_frames)}")
    df_us, ann_us, st_us, top3_us, us_curves = run_market_v8(
        "US_BlueChip65", us_frames, us_idx, us_cash, us_gold, us_sec,
        buy_cost=0.0005, sell_cost=0.0005, etf_cost=0.0005,
    )
    df_us.to_csv(RES / "v8_research_us.csv", index=False, encoding="utf-8-sig")
    ann_us.to_csv(RES / "v8_annual_us.csv", index=False, encoding="utf-8-sig")

    df_stress = pd.concat([st_kr, st_us], ignore_index=True)
    df_stress.to_csv(RES / "v8_stress_mc.csv", index=False, encoding="utf-8-sig")

    save_v8_charts(df_kr, df_us, kr_curves, us_curves, HERE / "docs" / "img")

    elapsed = time.perf_counter() - t0
    print(f"\nElapsed: {elapsed:.1f}s | KR top3 single={top3_kr} | US top3 single={top3_us}")

    for title, df, ann in (("KR_Top200", df_kr, ann_kr), ("US_BlueChip65", df_us, ann_us)):
        print(f"\n==================== {title} ====================")
        for p in ("전체(2016~2026)", "IS(2016~2021)", "OOS(2022~2026)"):
            print(f"\n--- {p} ---")
            sub = df[df["구간"] == p].drop(columns=["시장", "구간"])
            print(sub.to_string(index=False))
        print(f"\n--- {title} Annual Returns (%) ---")
        print(ann.drop(columns=["시장"]).to_string(index=False))

    print("\n==================== 2x Cost Stress + Monte Carlo 95% MDD ====================")
    print(df_stress.to_string(index=False))


if __name__ == "__main__":
    main()
