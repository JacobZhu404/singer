import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PARENT = PROJECT_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_kline() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    n = 120
    base = 10.0
    returns = rng.normal(0.0005, 0.02, size=n)
    close = base * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.001, 0.02, size=n))
    low = close * (1 - rng.uniform(0.001, 0.02, size=n))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.005, size=n))
    vol = rng.uniform(1e6, 1e7, size=n)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "vol": vol},
        index=dates,
    )
