"""
Estrategias de rotación mensual (pocas operaciones por año).

Se revisan UNA vez por mes, al cierre del último día hábil, y la orden se ejecuta
al cierre del día hábil siguiente (no se usa información que no se tenía).

  1. Tendencia SPY         — SPY si cierra sobre su media de 200 ruedas; si no, letras del Tesoro (BIL).
                              Regla popularizada por Meb Faber (2007).
  2. Momentum dual          — Entre EE.UU. (SPY) y resto del mundo (EFA) elige el que más subió en 12 meses;
                              si ninguno le ganó a las letras del Tesoro, pasa a bonos (AGG).
                              Regla "Global Equities Momentum" de Gary Antonacci (2014).
  3. Rotación sectorial     — Los 3 sectores de EE.UU. con mejor momentum (promedio de 3, 6 y 12 meses),
                              cada uno con 1/3 de la caja y solo si está sobre su media de 200; si no, ese tercio a BIL.

El efectivo rinde lo que rindieron de verdad las letras del Tesoro de EE.UU. cada año (ETF BIL),
no una tasa fija.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CASH = "BIL"
SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]

STRATEGIES = {
    "Tendencia SPY (media de 200)": {
        "tickers": ["SPY", CASH],
        "regla": "Fin de mes: si SPY cierra sobre su media de 200 ruedas → 100 % SPY; si no → 100 % letras del Tesoro (BIL).",
        "origen": "Meb Faber, 2007",
    },
    "Momentum dual (SPY / EFA / bonos)": {
        "tickers": ["SPY", "EFA", "AGG", CASH],
        "regla": "Fin de mes: el mejor de SPY y EFA en 12 meses, si le ganó a las letras del Tesoro; si no → bonos (AGG).",
        "origen": "Gary Antonacci, 2014",
    },
    "Rotación sectorial (top 3)": {
        "tickers": SECTORS + [CASH],
        "regla": "Fin de mes: los 3 sectores con mejor suba promedio a 3, 6 y 12 meses, 1/3 cada uno, "
                 "solo si están sobre su media de 200; si no, ese tercio a BIL.",
        "origen": "Rotación por momentum clásica",
    },
}
EQUITY = set(SECTORS) | {"SPY", "EFA", "QQQ"}


def month_ends(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = idx.to_series()
    return pd.DatetimeIndex(s.groupby(idx.to_period("M")).max().values)


def target_weights(name: str, closes: pd.DataFrame) -> pd.DataFrame:
    """Pesos objetivo decididos al cierre de cada fin de mes (filas = fechas de decisión)."""
    me = month_ends(closes.index)
    last = closes.index[-1]
    if len(me) and me[-1] == last and last + pd.offsets.BMonthEnd(0) != last:
        me = me[:-1]                                  # el mes en curso todavía no terminó
    rows = {}
    if name.startswith("Tendencia"):
        sma = closes["SPY"].rolling(200).mean()
        for d in me:
            if pd.isna(sma.loc[d]):
                continue
            rows[d] = {"SPY": 1.0} if closes.at[d, "SPY"] > sma.loc[d] else {CASH: 1.0}
    elif name.startswith("Momentum"):
        r12 = closes / closes.shift(252) - 1
        for d in me:
            v = r12.loc[d, ["SPY", "EFA", CASH]]
            if v.isna().any():
                continue
            best = "SPY" if v["SPY"] >= v["EFA"] else "EFA"
            rows[d] = {best: 1.0} if v[best] > v[CASH] else {"AGG": 1.0}
    elif name.startswith("Rotación"):
        sec = closes[SECTORS]
        score = ((sec / sec.shift(63) - 1) + (sec / sec.shift(126) - 1) + (sec / sec.shift(252) - 1)) / 3
        sma = sec.rolling(200).mean()
        for d in me:
            sc = score.loc[d].dropna()
            if len(sc) < 3 or sma.loc[d].isna().any():
                continue
            w = {}
            for tk in sc.sort_values(ascending=False).index[:3]:
                key = tk if sec.at[d, tk] > sma.at[d, tk] else CASH
                w[key] = w.get(key, 0) + 1 / 3
            rows[d] = w
    else:
        raise ValueError(name)
    return pd.DataFrame.from_dict(rows, orient="index").fillna(0.0).sort_index()


def run(name: str, closes: pd.DataFrame, cost_pct: float = 0.05, start=None):
    """Simula la estrategia. Devuelve (curva base 1, operaciones, exposición a acciones 0..1, pesos objetivo)."""
    closes = closes.sort_index().ffill()
    W = target_weights(name, closes)
    dates = closes.index
    if start is not None:
        W = W[W.index >= pd.Timestamp(start) - pd.offsets.MonthEnd(1)]
    cols = closes.columns
    rets = closes.pct_change().fillna(0.0)
    cost = cost_pct / 100

    exec_plan = {}
    for d, w in W.iterrows():
        pos = dates.get_indexer([d])[0]
        if 0 <= pos < len(dates) - 1:
            exec_plan[dates[pos + 1]] = w.reindex(cols).fillna(0.0)
    if not exec_plan:
        return pd.Series(dtype=float), pd.DataFrame(), pd.Series(dtype=float), W
    first = min(exec_plan)

    hold, values, expo, trades = None, [], [], []
    open_since: dict = {}
    for date in dates[dates >= first]:
        if hold is not None:
            hold = hold * (1 + rets.loc[date])
        if date in exec_plan:
            tgt = exec_plan[date]
            total = hold.sum() if hold is not None else 1.0
            cur = hold if hold is not None else pd.Series(0.0, index=cols)
            traded = (tgt * total - cur).abs()
            traded[CASH] = 0.0                       # mover a/desde letras no se cuenta como costo de acciones
            total -= traded.sum() * cost
            prev = set(cur[cur > 1e-9].index) - {CASH}
            new = set(tgt[tgt > 1e-9].index) - {CASH}
            for tk in new - prev:
                open_since[tk] = date
            for tk in prev - new:
                trades.append({"Activo": tk, "Entrada": open_since.pop(tk, None), "Salida": date})
            hold = tgt * total
        v = hold.sum()
        values.append(v)
        expo.append(hold[[c for c in cols if c in EQUITY]].sum() / v if v > 0 else 0.0)
    for tk, d in open_since.items():
        trades.append({"Activo": tk, "Entrada": d, "Salida": pd.NaT})
    eq = pd.Series(values, index=dates[dates >= first])
    return eq, pd.DataFrame(trades), pd.Series(expo, index=eq.index), W


def describe(w: pd.Series) -> str:
    w = w[w > 1e-9].sort_values(ascending=False)
    name = {CASH: "letras del Tesoro (BIL)", "AGG": "bonos (AGG)"}
    return " · ".join(f"{v * 100:.0f}% {name.get(k, k)}" for k, v in w.items())


def next_rebalance(today: pd.Timestamp) -> pd.Timestamp:
    """Último día hábil del mes (aproximado: no contempla feriados)."""
    return today.normalize() + pd.offsets.BMonthEnd(0)
