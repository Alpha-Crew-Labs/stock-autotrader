"""공용 시세 로더 — 한국(FinanceDataReader) + 미국(yfinance) 재시도·로컬 캐시·증분 갱신."""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd

HERE = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = HERE / "data_screen" / "prices"
DEFAULT_US_CACHE_DIR = HERE / "data_us"
OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


def is_us_ticker(code: str) -> bool:
    """영문자가 포함된 심볼은 미국 티커로 판별 (6자리 숫자 KRX 코드와 구분)."""
    return any(ch.isalpha() for ch in str(code).strip())


def _normalize_us_frame(df: pd.DataFrame, code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLS)
    if isinstance(df.columns, pd.MultiIndex):
        if code in df.columns.get_level_values(-1):
            df = df.xs(code, axis=1, level=-1)
        elif code in df.columns.get_level_values(0):
            df = df[code]
        else:
            df = df.droplevel(-1, axis=1)
    out = df[OHLCV_COLS].dropna().copy()
    if out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    out.index.name = "Date"
    return out[out["Volume"] > 0]


def fetch_ohlcv(code: str, start: dt.date | str, retries: int = 3) -> pd.DataFrame:
    """지정 시작일 이후 일봉 OHLCV를 조회한다 (KR=FDR, US=yfinance, 재시도 포함)."""
    start_str = start.strftime("%Y-%m-%d") if isinstance(start, (dt.date, dt.datetime)) else str(start)
    us = is_us_ticker(code)
    for i in range(retries):
        try:
            if us:
                import yfinance as yf

                df = yf.download(code, start=start_str, auto_adjust=True, progress=False)
                out = _normalize_us_frame(df, code)
                if len(out):
                    return out
            else:
                df = fdr.DataReader(code, start_str)
                if df is not None and len(df):
                    out = df[OHLCV_COLS].copy()
                    return out[out["Volume"] > 0]
        except Exception:
            if i + 1 < retries:
                time.sleep(1 + i)
    return pd.DataFrame(columns=OHLCV_COLS)


def load_prices(
    code: str,
    days: int = 420,
    cache_dir: Path | None = None,
    today: dt.date | None = None,
) -> pd.DataFrame:
    """캐시 우선 일봉 조회. 당일 16시 이후 캐시는 재사용, 60일 내 캐시는 최근 10일만 증분 갱신."""
    today = today or dt.date.today()
    if cache_dir is not None:
        cdir = cache_dir
    else:
        cdir = DEFAULT_US_CACHE_DIR if is_us_ticker(code) else DEFAULT_CACHE_DIR
    cdir.mkdir(parents=True, exist_ok=True)
    f = cdir / f"{code}.csv"

    if f.exists():
        try:
            old = pd.read_csv(f, index_col=0, parse_dates=True)
            if "Volume" in old.columns:
                old = old[old["Volume"] > 0]
            mtime = dt.datetime.fromtimestamp(f.stat().st_mtime)
            if len(old) and mtime.date() == today and mtime.hour >= 16:
                return old
            if len(old) and (today - old.index[-1].date()).days < 60:
                new = fetch_ohlcv(code, old.index[-1].date() - dt.timedelta(days=10))
                if len(new):
                    df = pd.concat([old[old.index < new.index[0]], new])
                    df = df[~df.index.duplicated(keep="last")].sort_index()
                    df.to_csv(f)
                    return df
                return old
        except Exception:
            pass

    df = fetch_ohlcv(code, today - dt.timedelta(days=int(days * 1.5)))
    if len(df):
        df.to_csv(f)
    return df
