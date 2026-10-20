"""Descarga de datos de mercado (Yahoo Finance vía yfinance) y fechas de balances."""
from __future__ import annotations

import numpy as np
import pandas as pd

ETFS = {"SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "XLF", "XLK", "XLE", "XLV", "XLP", "XLI", "XLY", "XLU"}


def get_prices(ticker: str, start: str = "2010-01-01") -> pd.DataFrame:
    """Velas diarias ajustadas por dividendos y splits. Columnas: Open High Low Close Volume."""
    import yfinance as yf

    df = yf.download(ticker, start=start, interval="1d", auto_adjust=True,
                     progress=False, threads=False)
    if df is None or df.empty:
        raise ValueError(f"No se encontraron datos para {ticker}")
    if isinstance(df.columns, pd.MultiIndex):           # yfinance >= 0.2 devuelve MultiIndex
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def get_earnings_dates(ticker: str) -> list[pd.Timestamp]:
    """Fechas de reportes trimestrales (pasados y próximo). Lista vacía para ETFs o si falla."""
    if ticker.upper() in ETFS:
        return []
    try:
        import yfinance as yf

        ed = yf.Ticker(ticker).get_earnings_dates(limit=100)
        if ed is None or ed.empty:
            return []
        return sorted({pd.Timestamp(x).tz_localize(None).normalize()
                       if pd.Timestamp(x).tzinfo else pd.Timestamp(x).normalize() for x in ed.index})
    except Exception:
        return []


def next_earnings(dates: list[pd.Timestamp], today: pd.Timestamp) -> pd.Timestamp | None:
    future = [d for d in dates if d >= today.normalize()]
    return min(future) if future else None


def synthetic_prices(seed: int = 0, years: int = 12, drift: float = 0.08, vol: float = 0.18) -> pd.DataFrame:
    """Serie inventada (paseo aleatorio) solo para probar el código sin internet."""
    rng = np.random.default_rng(seed)
    n = int(years * 252)
    idx = pd.bdate_range("2014-01-02", periods=n)
    r = rng.normal(drift / 252, vol / np.sqrt(252), n)
    close = 100 * np.exp(np.cumsum(r))
    opn = close * np.exp(rng.normal(0, vol / np.sqrt(252) / 3, n))
    high = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    volume = rng.lognormal(16, 0.45, n) * (1 + 3 * np.abs(r) / (vol / np.sqrt(252)) * 0.2)
    return pd.DataFrame({"Open": opn, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)
