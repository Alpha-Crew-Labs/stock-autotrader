"""전략 엔진 단일 진실 공급원 — 지표·매수/물타기/청산 규칙·후보 순위.

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


def prep(df: pd.DataFrame, n: int = 14, k: int = 5, d: int = 5, stoch_fn=None) -> pd.DataFrame:
    """일봉 DataFrame에 스토캐스틱·추세·변동성 지표와 BUY/ADD/SELL/TREND 신호를 계산한다."""
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

    close = out["Close"]
    out["TREND"] = ((close > close.rolling(200).mean()) & (close.pct_change(120) > 0)).fillna(False).astype(bool)
    out["MA20"] = close.rolling(20).mean()
    out["RET60"] = close.pct_change(60)
    prev_c = close.shift(1)
    tr = pd.concat(
        [(out["High"] - out["Low"]).abs(), (out["High"] - prev_c).abs(), (out["Low"] - prev_c).abs()],
        axis=1,
    ).max(axis=1, skipna=False)
    out["ATR14"] = tr.rolling(14).mean()
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


def evaluate_position(
    pos: dict,
    f: pd.DataFrame,
    exit_mode: str = "E2",
    units: int = UNITS,
    sell_cost: float = 0.00215,
    hold_add_under20: bool = False,
) -> dict:
    """보유 포지션의 당일 종가 기준 청산/물타기/보유 판정을 반환한다."""
    last = f.iloc[-1]
    held = f[f.index >= pd.Timestamp(pos["opened"])]
    since = int((f.index > pd.Timestamp(pos["last_buy"])).sum())
    in_profit = bool(pos["qty"] > 0 and pos["qty"] * float(last["Close"]) * (1 - sell_cost) > pos["cost"])
    hit80 = bool(pos.get("hit80")) or bool((held["K"] > 80).any())
    above20 = bool(pos.get("above20")) or (
        bool((held["Close"] > held["MA20"]).any()) if "MA20" in held else False
    )
    k_down = bool(len(f) > 1 and float(last["K"]) < float(f["K"].iloc[-2]))
    below_ma20 = bool("MA20" in f and pd.notna(last["MA20"]) and float(last["Close"]) < float(last["MA20"]))

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
    elif exit_mode == "E4":
        if in_profit:
            if (hit80 or above20) and below_ma20:
                should_sell, sell_sig, sell_why = True, "매도(80·추세 후 20일선 이탈)", "이익 + 80/20일선 후 이탈"
        elif bool(last["SELL"]):
            should_sell, sell_sig, sell_why = True, "매도(손실 중 데드크로스)", "손실 + SELL 신호"
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
            "in_profit": in_profit,
            "status": "물타기 신호",
        }

    if not in_profit:
        if hold_add_under20 and float(last["K"]) < 20 and pos["units"] < units:
            status = "과매도 물타기 대기"
        else:
            status = "손실감시"
    elif exit_mode == "E2":
        status = "80도달·유지" if hit80 else "80도달 대기"
    elif exit_mode in ("E3", "E4"):
        status = "20일선 추적" if (above20 or hit80) else "추세진입 대기"
    else:
        status = "데드크로스 감시"

    return {
        "action": "hold",
        "sig": "",
        "why": "",
        "hit80": hit80,
        "above20": above20,
        "in_profit": in_profit,
        "status": status,
    }


def _finite(val) -> float | None:
    try:
        x = float(val)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def rank_candidates(rows: list[dict], rank_by: str = "rs60") -> list[dict]:
    """신규 매수 후보 정렬: 'rs60'(상대강도/60일수익률 내림차순) 또는 'k'(%K 오름차순)."""
    if rank_by == "k":
        return sorted(rows, key=lambda r: _finite(r.get("%K")) if _finite(r.get("%K")) is not None else 999.0)
    if rank_by == "rs60":
        def key(r):
            rs = _finite(r.get("RS60"))
            ret60 = _finite(r.get("Ret60%"))
            score = rs if rs is not None else (ret60 if ret60 is not None else -1e9)
            k_val = _finite(r.get("%K"))
            return (-score, k_val if k_val is not None else 999.0)
        return sorted(rows, key=key)
    raise ValueError(f"지원하지 않는 rank_by: {rank_by}")
