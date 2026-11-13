"""공통 픽스처 — 네트워크·로컬 데이터 폴더 없이 합성 OHLCV 로 테스트한다."""
import socket
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """테스트 중 실수로 외부 접속하면 바로 실패시킨다."""
    def guard(*a, **k):
        raise RuntimeError("테스트 중 네트워크 접속 시도")
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)


def make_ohlcv(close, start="2020-01-01", spread=0.01):
    """종가 배열 → 영업일 인덱스의 OHLCV DataFrame."""
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range(start, periods=len(close), name="Date")
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + spread)
    lo = np.minimum(open_, close) * (1 - spread)
    return pd.DataFrame({"Open": open_, "High": hi, "Low": lo, "Close": close,
                         "Volume": np.full(len(close), 1000.0)}, index=idx)


@pytest.fixture
def ohlcv():
    return make_ohlcv


@pytest.fixture
def random_walk():
    """재현 가능한 랜덤워크(600영업일)."""
    rng = np.random.default_rng(42)
    close = 10000 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, 600)))
    return make_ohlcv(close)
