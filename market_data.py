"""공용 시세 로더 — FinanceDataReader 재시도·로컬 캐시·증분 갱신."""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd

HERE = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = HERE / "data_screen" / "prices"
OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


def fetch_ohlcv(code: str, start: dt.date | str, retries: int = 3) -> pd.DataFrame:
    """지정 시작일 이후 일봉 OHLCV를 조회한다 (재시도 포함)."""
    start_str = start.strftime("%Y-%m-%d") if isinstance(start, (dt.date, dt.datetime)) else str(start)
    for i in range(retries):
        try:
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
    cdir = cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR
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
