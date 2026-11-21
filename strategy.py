"""전략 엔진 단일 진실 공급원 — 지표·레짐·매수/물타기/청산 규칙·역변동성 사이징·후보 순위.

백테스트와 실전/모의 실행(screener.py, run_daily.py, backtest_*.py)이 동일한
함수를 공유해 규칙 불일치를 방지한다. 모든 지표는 순수 과거 데이터만 사용한다.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

UNITS = 3


def stoch(df: pd.DataFrame, n: int = 14, k: int = 5, d: int = 5) -> pd.DataFrame:
    """스토캐스틱(n, k, d) 계산 후 K, D 컬럼을 추가한다."""
    lo = df["Low"].rolling(n).min()
    hi = df["High"].rolling(n).max()
    df["K"] = (100 * (df["Close"] - lo) / (hi - lo).replace(0, np.nan)).rolling(k).mean()
    df["D"] = df["K"].rolling(d).mean()
    return df


def _clenow90(close: pd.Series, w: int = 90) -> np.ndarray:
    """90일 로그주가 선형회귀 연율화 기울기 * R^2 (순수 과거 데이터)."""
    arr = np.full(len(close), np.nan, dtype=float)
    log_c = np.log(close.values.astype(float))
    if len(log_c) < w:
        return arr
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
    arr[w - 1 :] = ann * r2
    return arr


def _wilder_rsi(series: pd.Series, period: int) -> pd.Series:
    """순수 과거 데이터 기반 Wilder RSI 계산."""
    diff = series.diff()
    alpha = 1.0 / float(period)
    up = diff.clip(lower=0).ewm(alpha=alpha, adjust=False).mean()
    dn = (-diff.clip(upper=0)).ewm(alpha=alpha, adjust=False).mean()
    rsi = 100.0 - 100.0 / (1.0 + up / dn.replace(0, np.nan))
    return rsi.where(dn != 0, np.where(up > 0, 100.0, 50.0))


def market_vol_scale(
    bench_close: pd.Series,
    target_vol: float = 0.16,
    lo: float = 0.35,
    hi: float = 1.25,
) -> float:
    """벤치마크 20일 실현변동성 기반 포트폴리오 변동성 타게팅 배율 (순수 과거 데이터)."""
    if bench_close is None or len(bench_close) < 21:
        return 1.0
    vol20 = bench_close.pct_change().rolling(20).std() * np.sqrt(252.0)
    valid = vol20.dropna()
    if valid.empty:
        return 1.0
    v = _finite(valid.iloc[-1])
    if v is None or v <= 0:
        return 1.0
    return float(np.clip(float(target_vol) / max(v, 0.08), float(lo), float(hi)))


def market_regime(
    bench_close: pd.Series,
    target_vol: float = 0.16,
    lo: float = 0.35,
    hi: float = 1.25,
    vol_ratio_max: float | None = 1.50,
    yellow_mult: float = 0.50,
    vol_targeting: bool = True,
) -> dict:
    """벤치마크 종가 기준 v8 통합 레짐 판정 (200일선 · 50일선 노란불 · VolRatio 크래시 브레이크 · 변동성 타게팅).

    모든 지표는 `bench_close.iloc[-1]` 이전의 순수 과거 데이터만 사용한다.
    """
    if bench_close is None or len(bench_close) == 0:
        return {
            "mkt_on": False,
            "above50": False,
            "vol20": None,
            "vol60": None,
            "vol_ratio": 1.0,
            "vol_brake": False,
            "vol_scale": 1.0,
            "yellow_light": False,
            "regime_scale": 0.0,
            "label": "OFF",
        }
    last_c = _finite(bench_close.iloc[-1])
    sma200 = _finite(bench_close.rolling(200).mean().iloc[-1])
    sma50 = _finite(bench_close.rolling(50).mean().iloc[-1])
    mkt_on = bool(last_c is not None and sma200 is not None and last_c > sma200)
    above50 = bool(last_c is not None and sma50 is not None and last_c > sma50)

    rets = bench_close.pct_change()
    vol20 = _finite((rets.rolling(20).std() * np.sqrt(252.0)).iloc[-1])
    vol60 = _finite((rets.rolling(60).std() * np.sqrt(252.0)).iloc[-1])
    vol_ratio = (vol20 / vol60) if (vol20 is not None and vol60 is not None and vol60 > 0) else 1.0
    vol_brake = bool(vol_ratio_max is not None and vol_ratio > float(vol_ratio_max))
    vol_scale = market_vol_scale(bench_close, target_vol=target_vol, lo=lo, hi=hi) if vol_targeting else 1.0
    yellow_light = bool(mkt_on and (not above50))
    ym = float(yellow_mult)

    if not mkt_on:
        regime_scale = 0.0
        label = "OFF"
    elif vol_brake:
        regime_scale = 0.0
        label = "BRAKE(VolRatio)"
    elif yellow_light:
        regime_scale = float(vol_scale * ym)
        label = f"YELLOW(x{ym:.2f})"
    else:
        regime_scale = float(vol_scale)
        label = "ON"

    return {
        "mkt_on": mkt_on,
        "above50": above50,
        "vol20": vol20,
        "vol60": vol60,
        "vol_ratio": float(vol_ratio),
        "vol_brake": vol_brake,
        "vol_scale": float(vol_scale),
        "yellow_light": yellow_light,
        "regime_scale": regime_scale,
        "label": label,
    }


def prep(df: pd.DataFrame, n: int = 14, k: int = 5, d: int = 5, stoch_fn=None) -> pd.DataFrame:
    """일봉 DataFrame에 스토캐스틱·v7 StochRSI/IBS/거래량급락·추세·변동성 지표를 계산한다."""
    out = df.copy()
    out = stoch_fn(out) if stoch_fn is not None else stoch(out, n=n, k=k, d=d)
    K, D = out["K"], out["D"]
    gap = (K - D).abs()
    narrow3 = (gap < gap.shift()) & (gap.shift() < gap.shift(2)) & (gap.shift(2) < gap.shift(3))
    near_os = (K.rolling(5).min() < 20) & (K < 30)
    gc = (K > D) & (K.shift() <= D.shift())
    dc = (K < D) & (K.shift() >= D.shift())
    out["BUY"] = (near_os & (((K < D) & narrow3 & (K > K.shift())) | gc)).fillna(False).astype(bool)
    wid2 = ((D - K) > (D - K).shift()) & ((D - K).shift() > (D - K).shift(2))
    out["ADD"] = ((K < 20) & (K < D) & wid2).fillna(False).astype(bool)
    out["SELL"] = (((K > D) & narrow3 & (K < K.shift())) | dc).fillna(False).astype(bool)

    close, high, low = out["Close"], out["High"], out["Low"]
    out["TREND"] = ((close > close.rolling(200).mean()) & (close.pct_change(120) > 0)).fillna(False).astype(bool)
    out["MA20"] = close.rolling(20).mean()
    out["RET60"] = close.pct_change(60)
    prev_c = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_c).abs(), (low - prev_c).abs()],
        axis=1,
    ).max(axis=1, skipna=False)
    out["ATR14"] = tr.rolling(14).mean()

    # v6 / v7 추가 지표 (모두 순수 과거 데이터)
    out["RSI2"] = _wilder_rsi(close, 2)
    rsi14 = _wilder_rsi(close, 14)
    r_lo = rsi14.rolling(14).min()
    r_hi = rsi14.rolling(14).max()
    raw_sr = 100.0 * (rsi14 - r_lo) / (r_hi - r_lo).replace(0, np.nan)
    out["STOCH_RSI"] = raw_sr.rolling(3).mean()

    rng = high - low
    out["IBS"] = np.where(rng > 0, (close - low) / rng, 0.5)
    out["SMA5"] = close.rolling(5).mean()
    out["SMA50"] = close.rolling(50).mean()
    out["SMA100"] = close.rolling(100).mean()
    out["SMA200"] = close.rolling(200).mean()
    out["VOL20"] = (close.pct_change().rolling(20).std() * np.sqrt(252.0)).clip(lower=0.10)
    out["SKIP_MOM"] = close.shift(20) / close.shift(120) - 1.0
    out["CLENOW90"] = _clenow90(close, 90)
    vol_s = out["Volume"] if "Volume" in out else pd.Series(0.0, index=out.index)
    out["VOL_SPIKE_DN"] = ((close < prev_c) & (vol_s > 2.5 * vol_s.rolling(20).mean())).fillna(False).astype(bool)

    aligned = (close > out["SMA200"]) & (out["SMA50"] > out["SMA200"])
    fast_pb = (out["RSI2"] < 10.0) | ((out["IBS"] < 0.20) & (close < out["SMA5"]))
    out["PULLBACK_V6"] = (aligned & fast_pb).fillna(False).astype(bool)
    fast_pb7 = (out["STOCH_RSI"] < 15.0) | ((out["IBS"] < 0.20) & (close < out["SMA5"]))
    out["PULLBACK_V7"] = (aligned & fast_pb7 & (~out["VOL_SPIKE_DN"])).fillna(False).astype(bool)
    return out


def weekly_ok(df: pd.DataFrame, n: int = 14, k: int = 5, d: int = 5) -> pd.Series:
    """직전 완성 주봉(금요일 마감)의 스토캐스틱 %K > %D 여부 (미래 데이터 차단)."""
    w = df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    if w.empty:
        return pd.Series(False, index=df.index, dtype=bool)
    w = stoch(w, n=n, k=k, d=d)
    ok = (w["K"] > w["D"]).shift(1, freq="D")
    return ok.reindex(df.index, method="ffill").fillna(False).astype(bool)


def check_entry(
    last: pd.Series,
    mkt_on: bool,
    entry_mode: str = "pullback_v7",
    vol_brake: bool = False,
) -> tuple[bool, str]:
    """신규 매수 진입 여부와 사유 라벨을 반환한다."""
    if entry_mode in ("pullback_v8", "pullback_v7"):
        if not bool(last.get("PULLBACK_V7", False)):
            return False, ""
        if not mkt_on:
            return False, "매수신호(시장필터 OFF)"
        if vol_brake:
            return False, "매수신호(변동성 브레이크)"
        return True, "신규매수"
    if entry_mode == "pullback_v6":
        if not bool(last.get("PULLBACK_V6", False)):
            return False, ""
        if not mkt_on:
            return False, "매수신호(시장필터 OFF)"
        if vol_brake:
            return False, "매수신호(변동성 브레이크)"
        return True, "신규매수"
    if entry_mode == "stoch_v5":
        if not bool(last.get("BUY", False)):
            return False, ""
        if not mkt_on:
            return False, "매수신호(시장필터 OFF)"
        if vol_brake:
            return False, "매수신호(변동성 브레이크)"
        if not bool(last.get("TREND", False)):
            return False, "매수신호(종목필터 탈락)"
        if not bool(last.get("WOK", False)):
            return False, "매수신호(주봉 하락 중)"
        return True, "신규매수"
    raise ValueError(f"지원하지 않는 entry_mode: {entry_mode}")


def inverse_vol_amount(
    equity: float,
    vol20: float | None,
    target_risk: float = 0.018,
    max_pos_weight: float = 0.11,
    vol_scale: float = 1.0,
) -> float:
    """역변동성(Inverse Volatility) + 시장 변동성 타게팅 포지션 금액을 계산한다."""
    v = _finite(vol20)
    eff_vol = max(v, 0.10) if (v is not None and v > 0) else 0.25
    eff_risk = max(float(target_risk) * float(vol_scale), 0.0)
    weight = min(float(max_pos_weight), eff_risk / eff_vol)
    return max(float(equity), 0.0) * max(weight, 0.0)


def evaluate_position(
    pos: dict,
    f: pd.DataFrame,
    exit_mode: str = "E3_atr",
    units: int = UNITS,
    sell_cost: float = 0.00215,
    hold_add_under20: bool = False,
    atr_stop_mult: float = 2.5,
) -> dict:
    """보유 포지션의 당일 종가 기준 청산/물타기/보유 판정을 반환한다."""
    last = f.iloc[-1]
    held = f[f.index >= pd.Timestamp(pos["opened"])]
    since = int((f.index > pd.Timestamp(pos["last_buy"])).sum())
    close_val = float(last["Close"])
    in_profit = bool(pos["qty"] > 0 and pos["qty"] * close_val * (1 - sell_cost) > pos["cost"])
    hit80 = bool(pos.get("hit80")) or bool((held["K"] > 80).any())
    above20 = bool(pos.get("above20")) or (
        bool((held["Close"] > held["MA20"]).any()) if "MA20" in held else False
    )
    max_high = max(
        float(pos.get("max_high") or 0.0),
        float(held["High"].max()) if (len(held) and "High" in held and held["High"].notna().any()) else 0.0,
    )
    k_down = bool(len(f) > 1 and float(last["K"]) < float(f["K"].iloc[-2]))
    below_ma20 = bool("MA20" in f and pd.notna(last["MA20"]) and close_val < float(last["MA20"]))

    entry_px = float(pos.get("entry_px") or (pos["cost"] / max(pos["qty"], 1) if pos["qty"] > 0 else 0.0))
    entry_atr = float(pos.get("entry_atr") or 0.0)
    sma200_val = _finite(last.get("SMA200")) if "SMA200" in f else None
    atr_now = _finite(last.get("ATR14")) if "ATR14" in f else None
    sma5_val = _finite(last.get("SMA5")) if "SMA5" in f else None
    prev_high = _finite(f["High"].iloc[-2]) if (len(f) > 1 and "High" in f) else None

    below_sma200 = bool(sma200_val is not None and close_val < sma200_val)
    atr_stop_hit = bool(entry_px > 0 and entry_atr > 0 and close_val < entry_px - atr_stop_mult * entry_atr)

    should_sell = False
    sell_sig = ""
    sell_why = ""

    if exit_mode == "E0":
        if bool(last["SELL"]):
            should_sell, sell_sig, sell_why = True, "매도(데드크로스)", "SELL 신호"
    elif exit_mode == "E2":
        if in_profit:
            if hit80 and k_down:
                should_sell, sell_sig, sell_why = True, "매도(80 도달 후 꺾임)", "이익 + %K 80 후 하락"
        elif bool(last["SELL"]):
            should_sell, sell_sig, sell_why = True, "매도(손실 중 데드크로스)", "손실 + SELL 신호"
    elif exit_mode == "E3":
        if in_profit:
            if above20 and below_ma20:
                should_sell, sell_sig, sell_why = True, "매도(이익 중 20일선 이탈)", "이익 + 20일선 이탈"
        elif bool(last["SELL"]):
            should_sell, sell_sig, sell_why = True, "매도(손실 중 데드크로스)", "손실 + SELL 신호"
    elif exit_mode == "E3_atr":
        if in_profit:
            if above20 and below_ma20:
                should_sell, sell_sig, sell_why = True, "매도(이익 중 20일선 이탈)", "이익 + 20일선 이탈"
        else:
            if atr_stop_hit:
                should_sell, sell_sig, sell_why = True, "매도(손실 ATR 손절)", f"손실 + -{atr_stop_mult}ATR 이탈"
            elif below_sma200:
                should_sell, sell_sig, sell_why = True, "매도(200일선 이탈)", "손실 + 200일선 이탈"
            elif entry_atr <= 0 and sma200_val is None and bool(last["SELL"]):
                should_sell, sell_sig, sell_why = True, "매도(손실 중 데드크로스)", "손실 + SELL 신호"
    elif exit_mode == "E4":
        if in_profit:
            if (hit80 or above20) and below_ma20:
                should_sell, sell_sig, sell_why = True, "매도(80·추세 후 20일선 이탈)", "이익 + 80/20일선 후 이탈"
        elif bool(last["SELL"]):
            should_sell, sell_sig, sell_why = True, "매도(손실 중 데드크로스)", "손실 + SELL 신호"
    elif exit_mode == "chandelier":
        if in_profit:
            if max_high > 0 and atr_now is not None and atr_now > 0 and close_val < max_high - atr_stop_mult * atr_now:
                should_sell, sell_sig, sell_why = True, "매도(Chandelier ATR 이탈)", f"이익 + 고점-{atr_stop_mult}ATR 이탈"
        else:
            if atr_stop_hit:
                should_sell, sell_sig, sell_why = True, "매도(손실 ATR 손절)", f"손실 + -{atr_stop_mult}ATR 이탈"
            elif below_sma200:
                should_sell, sell_sig, sell_why = True, "매도(200일선 이탈)", "손실 + 200일선 이탈"
    elif exit_mode == "meanrev":
        mr_hit = (sma5_val is not None and close_val > sma5_val) or (prev_high is not None and close_val > prev_high)
        held_days = max(len(held) - 1, 0)
        if mr_hit:
            should_sell, sell_sig, sell_why = True, "매도(단기 평균회귀)", "종가 > 5일선/전일고가"
        elif (not in_profit and held_days >= 10) or below_sma200:
            should_sell, sell_sig, sell_why = True, "매도(타임스톱/추세이탈)", "10일 타임스톱 또는 200일선 이탈"
    else:
        raise ValueError(f"지원하지 않는 exit_mode: {exit_mode}")

    if hold_add_under20 and not in_profit and should_sell and float(last["K"]) < 20 and pos["units"] < units:
        should_sell = False

    if should_sell:
        return {
            "action": "sell",
            "sig": sell_sig,
            "why": sell_why,
            "hit80": hit80,
            "above20": above20,
            "max_high": max_high,
            "in_profit": in_profit,
            "status": "청산 신호",
        }

    if bool(last["ADD"]) and pos["units"] < units and since >= 5:
        return {
            "action": "add",
            "sig": "추가매수",
            "why": f"ADD {pos['units'] + 1}/{units}",
            "hit80": hit80,
            "above20": above20,
            "max_high": max_high,
            "in_profit": in_profit,
            "status": "물타기 신호",
        }

    if not in_profit:
        if exit_mode in ("E3_atr", "chandelier"):
            status = "ATR/200일선 감시"
        elif exit_mode == "meanrev":
            status = "평균회귀 대기"
        elif hold_add_under20 and float(last["K"]) < 20 and pos["units"] < units:
            status = "과매도 물타기 대기"
        else:
            status = "손실감시"
    elif exit_mode == "E2":
        status = "80도달·유지" if hit80 else "80도달 대기"
    elif exit_mode in ("E3", "E3_atr", "E4"):
        status = "20일선 추적" if (above20 or hit80) else "추세진입 대기"
    elif exit_mode == "chandelier":
        status = "Chandelier 추적"
    elif exit_mode == "meanrev":
        status = "5일선 반등 대기"
    else:
        status = "데드크로스 감시"

    return {
        "action": "hold",
        "sig": "",
        "why": "",
        "hit80": hit80,
        "above20": above20,
        "max_high": max_high,
        "in_profit": in_profit,
        "status": status,
    }


def _finite(val) -> float | None:
    try:
        x = float(val)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def rank_candidates(rows: list[dict], rank_by: str = "clenow") -> list[dict]:
    """신규 매수 후보 정렬: 'clenow'(Clenow90 내림차순), 'rs60', 'k'(%K 오름차순)."""
    if rank_by == "k":
        return sorted(rows, key=lambda r: _finite(r.get("%K")) if _finite(r.get("%K")) is not None else 999.0)
    if rank_by == "rs60":
        def key_rs(r):
            rs = _finite(r.get("RS60"))
            ret60 = _finite(r.get("Ret60%"))
            score = rs if rs is not None else (ret60 if ret60 is not None else -1e9)
            k_val = _finite(r.get("%K"))
            return (-score, k_val if k_val is not None else 999.0)
        return sorted(rows, key=key_rs)
    if rank_by == "clenow":
        def key_cl(r):
            cl = _finite(r.get("Clenow90"))
            rs = _finite(r.get("RS60"))
            ret60 = _finite(r.get("Ret60%"))
            k_val = _finite(r.get("%K"))
            kv = k_val if k_val is not None else 999.0
            if cl is not None:
                return (0, -cl, kv)
            if rs is not None:
                return (1, -rs, kv)
            if ret60 is not None:
                return (2, -ret60, kv)
            return (3, 0.0, kv)
        return sorted(rows, key=key_cl)
    raise ValueError(f"지원하지 않는 rank_by: {rank_by}")
