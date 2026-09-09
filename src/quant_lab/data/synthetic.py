"""Deterministic synthetic panel used for tests and the zero-token demo."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.data.schema import normalize_daily_frame


def make_synthetic_daily_data(
    n_symbols: int = 40,
    n_days: int = 700,
    start_date: str = "2020-01-01",
    seed: int = 42,
) -> pd.DataFrame:
    """Create realistic-enough OHLCV data; never use it to claim performance."""
    if n_symbols < 5 or n_days < 100:
        raise ValueError("Synthetic demo needs at least 5 symbols and 100 days")

    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start_date, periods=n_days)
    symbols = [f"{index:06d}.XSHE" for index in range(1, n_symbols + 1)]
    industries = np.array(["BANK", "TECH", "INDUSTRIAL", "CONSUMER", "HEALTH"])
    market_return = rng.normal(0.0002, 0.012, n_days)
    records = []

    for index, symbol in enumerate(symbols):
        beta = rng.uniform(0.7, 1.3)
        idiosyncratic = rng.normal(0.0, rng.uniform(0.008, 0.02), n_days)
        returns = np.clip(beta * market_return + idiosyncratic, -0.095, 0.095)
        close = rng.uniform(8.0, 35.0) * np.exp(np.cumsum(returns))
        overnight = rng.normal(0.0, 0.003, n_days)
        open_price = close / np.exp(returns) * np.exp(overnight)
        intraday_spread = np.abs(rng.normal(0.01, 0.004, n_days))
        high = np.maximum(open_price, close) * (1 + intraday_spread)
        low = np.minimum(open_price, close) * np.maximum(0.01, 1 - intraday_spread)
        volume = rng.lognormal(15.5, 0.45, n_days).round()
        amount = volume * (open_price + close) / 2
        shares_outstanding = rng.uniform(2e8, 4e9)

        symbol_frame = pd.DataFrame(
            {
                "trade_date": dates,
                "symbol": symbol,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": amount,
                "adj_factor": 1.0,
                "market_cap": close * shares_outstanding,
                "industry": industries[index % len(industries)],
                "list_date": dates[0] - pd.Timedelta(days=500 + index),
                "is_st": False,
                "is_suspended": False,
                "is_limit_up": False,
                "is_limit_down": False,
                "is_st_known": True,
                "is_suspended_known": True,
                "limit_status_known": True,
            }
        )
        records.append(symbol_frame)

    result = pd.concat(records, ignore_index=True)
    # Inject a few constraints without invalidating OHLC.
    suspended_rows = result.index[::997]
    result.loc[suspended_rows, "is_suspended"] = True
    normalized = normalize_daily_frame(result)
    normalized.attrs["synthetic"] = True
    return normalized
