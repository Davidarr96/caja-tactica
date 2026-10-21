"""
Cartera mejorada: la caja completa trabaja como un solo fondo.

Diferencias con la simulación original (una parte fija de la caja por activo):
  1. Toda la caja se reparte en hasta `max_pos` posiciones simultáneas,
     tomando señales de cualquier activo de una lista amplia.
  2. El efectivo que espera una señal rinde una tasa anual (`cash_rate`).
  3. Se simula día a día y se valúan las posiciones al cierre (mark-to-market).

Las señales, entradas y salidas de cada activo salen del mismo motor de
strategy.py, así que las reglas son idénticas a las del resto del sitio.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategy import max_drawdown


def simulate(data: dict, trades: dict, max_pos: int = 3, cash_rate: float = 4.0,
             cost_pct: float = 0.05, start=None,
             cash_returns: pd.Series | None = None) -> tuple[pd.Series, pd.DataFrame, pd.Series]:
    """
    data:   {ticker: DataFrame con Close (y el resto)}
    trades: {ticker: DataFrame de backtest()} — se usan las operaciones cerradas y la abierta
    Devuelve (curva de valor base 1, operaciones tomadas/omitidas, exposición diaria 0..1).
    """
    closes = pd.concat({tk: d["Close"] for tk, d in data.items()}, axis=1).sort_index().ffill()
    if start is not None:
        closes = closes[closes.index >= pd.Timestamp(start)]
    dates = closes.index
    cost = cost_pct / 100
    daily_rate = (1 + cash_rate / 100) ** (1 / 252) - 1
    # Si se pasa `cash_returns` (rendimientos diarios reales, p. ej. del ETF BIL), reemplaza a la tasa fija.
    cash_r = cash_returns.reindex(dates).fillna(0.0) if cash_returns is not None else None

    allt = []
    for tk, tr in trades.items():
        t = tr[tr["status"].isin(["cerrada", "abierta"])].copy()
        t["ticker"] = tk
        allt.append(t)
    allt = pd.concat(allt, ignore_index=True) if allt else pd.DataFrame()
    if len(allt):
        allt = allt[allt["entry_date"] >= dates[0]]
        # Si hay más señales que lugares, se respeta el orden de llegada (el mismo día, orden alfabético).
        allt = allt.sort_values(["entry_date", "ticker"]).reset_index(drop=True)
    by_entry = {k: g for k, g in allt.groupby("entry_date")} if len(allt) else {}

    cash, positions = 1.0, []   # cada posición: dict(ticker, shares, exit_date, exit, idx)
    values, expo, log = [], [], []
    prev_close = closes.iloc[0]

    for date in dates:
        row = closes.loc[date]
        cash *= 1 + (cash_r.loc[date] if cash_r is not None else daily_rate)
        # 1) salidas del día
        still = []
        for p in positions:
            if pd.notna(p["exit_date"]) and p["exit_date"] == date:
                cash += p["shares"] * p["exit"] * (1 - cost)
            else:
                still.append(p)
        positions = still
        # 2) entradas del día
        if date in by_entry:
            port_val = cash + sum(p["shares"] * prev_close[p["ticker"]] for p in positions)
            for _, tr in by_entry[date].iterrows():
                if len(positions) >= max_pos or any(p["ticker"] == tr["ticker"] for p in positions):
                    log.append({**tr.to_dict(), "tomada": False})
                    continue
                alloc = min(cash, port_val / max_pos)
                if alloc <= 1e-9:
                    log.append({**tr.to_dict(), "tomada": False})
                    continue
                shares = alloc / (tr["entry"] * (1 + cost))
                cash -= alloc
                if pd.notna(tr["exit_date"]) and tr["exit_date"] == date:   # entró y salió el mismo día
                    cash += shares * tr["exit"] * (1 - cost)
                else:
                    positions.append({"ticker": tr["ticker"], "shares": shares, "exit_date": tr["exit_date"],
                                      "exit": tr["exit"]})
                log.append({**tr.to_dict(), "tomada": True, "peso_pct": alloc / port_val * 100})
        # 3) valuación al cierre
        invested = sum(p["shares"] * row[p["ticker"]] for p in positions)
        total = cash + invested
        values.append(total)
        expo.append(invested / total if total > 0 else 0)
        prev_close = row

    equity = pd.Series(values, index=dates)
    return equity, pd.DataFrame(log), pd.Series(expo, index=dates)


def stats(eq: pd.Series, start=None, end=None) -> dict:
    e = eq
    if start is not None:
        e = e[e.index >= pd.Timestamp(start)]
    if end is not None:
        e = e[e.index < pd.Timestamp(end)]
    if len(e) < 2:
        return {}
    e = e / e.iloc[0]
    years = (e.index[-1] - e.index[0]).days / 365.25
    rets = e.pct_change().dropna()
    vol = rets.std() * np.sqrt(252) * 100
    cagr = (e.iloc[-1] ** (1 / years) - 1) * 100 if years > 0 else np.nan
    return {"Anual %": cagr, "Volatilidad %": vol, "Peor caída %": max_drawdown(e),
            "Anual / caída": cagr / -max_drawdown(e) if max_drawdown(e) < 0 else np.nan,
            "Total %": (e.iloc[-1] - 1) * 100}
