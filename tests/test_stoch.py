import numpy as np
import pandas as pd

from backtest_hyung import stoch
from conftest import make_ohlcv


def test_k_d_bounded(random_walk):
    df = stoch(random_walk.copy())
    for col in ("K", "D"):
        v = df[col].dropna()
        assert len(v) > 0
        assert (v >= 0).all() and (v <= 100).all()


def test_warmup_nan_length(random_walk):
    df = stoch(random_walk.copy(), n=14, k=5, d=5)
    # fast %K 는 n-1 개, %K 는 그 뒤 k-1 개, %D 는 다시 d-1 개 NaN
    assert df["K"].isna().sum() == 13 + 4
    assert df["D"].isna().sum() == 13 + 4 + 4
    assert df["K"].iloc[17:].notna().all()
    assert df["D"].iloc[21:].notna().all()


def test_known_rising_and_falling():
    up = pd.DataFrame({"Close": np.arange(1.0, 41.0)}, index=pd.bdate_range("2021-01-01", periods=40))
    up["High"] = up["Low"] = up["Close"]
    s = stoch(up.copy())
    assert np.allclose(s["K"].dropna(), 100.0)
    assert np.allclose(s["D"].dropna(), 100.0)

    dn = up.iloc[::-1].copy()
    dn.index = up.index
    s = stoch(dn.copy())
    assert np.allclose(s["K"].dropna(), 0.0)


def test_known_hand_computed_value():
    # 종가 = 고가 = 저가, n=3, k=1, d=1 로 단순화해 손으로 계산
    c = np.array([1, 2, 3, 2, 1], dtype=float)
    df = pd.DataFrame({"Close": c, "High": c, "Low": c}, index=pd.bdate_range("2021-01-01", periods=5))
    s = stoch(df.copy(), n=3, k=1, d=1)
    # day3: lo=1 hi=3 c=3 -> 100 ; day4: lo=2 hi=3 c=2 -> 0 ; day5: lo=1 hi=3 c=1 -> 0
    assert np.isnan(s["K"].iloc[1])
    assert np.allclose(s["K"].iloc[2:].values, [100.0, 0.0, 0.0])


def test_flat_range_is_nan_not_inf():
    df = make_ohlcv(np.full(40, 100.0), spread=0.0)
    s = stoch(df.copy())
    assert s["K"].isna().all()
    assert not np.isinf(s["K"]).any()
