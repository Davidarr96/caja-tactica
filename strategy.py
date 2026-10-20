"""
Motor de la estrategia "Caja Táctica de Swing Trading Institucional".

Misma lógica que el indicador de TradingView (swing_institucional.pine):
  - Zonas de valor: SMA 50, SMA 200 y POC (perfil de volumen ~1 año)
  - Retroceso desde un máximo reciente
  - Secado de oferta: volumen del retroceso por debajo de la media
  - Filtro de tendencia y pausa alrededor de balances
  - Gestión: orden límite en la zona, TP +4 %, SL -3 %

El backtest es deliberadamente conservador:
  - La señal se calcula con la vela CERRADA; la orden límite se coloca para las
    velas siguientes (no se compra en la misma vela que generó la señal).
  - Si TP y SL caen en la misma vela, se asume que primero tocó el SL.
  - Los gaps se respetan (si abre por debajo del SL, se sale a la apertura).
  - Se descuentan comisión + deslizamiento por lado.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable

import numpy as np
import pandas as pd


# ───────────────────────────── Parámetros ─────────────────────────────
@dataclass
class Params:
    sma_fast: int = 50
    sma_slow: int = 200
    zone_pct: float = 1.0          # tolerancia de toque a la zona (%)
    trend_mode: str = "sma50>sma200"   # "sma50>sma200" | "precio>sma200" | "ninguno"
    pb_look: int = 10              # barras para el máximo previo
    pb_min: float = 2.0            # retroceso mínimo desde el máximo (%)
    use_poc: bool = True
    poc_len: int = 252
    poc_bins: int = 40
    vol_len: int = 20
    dry_len: int = 3
    dry_mult: float = 0.7          # secado: vol. promedio del retroceso < x · media
    use_earnings: bool = True
    er_before: int = 7             # días corridos antes del reporte
    er_after: int = 2              # días corridos después
    tp_pct: float = 4.0
    sl_pct: float = 3.0
    max_bars: int = 15             # vigencia máxima de la operación
    order_valid: int = 3           # velas que la orden límite queda viva
    tech_exit: bool = True         # salir si cierra bajo el soporte con volumen alto
    cost_pct: float = 0.05         # comisión + deslizamiento por lado (%)
    use_dry: bool = True           # exigir secado de volumen
    exit_mode: str = "fijo"        # "fijo" (TP fijo) | "rebote" (sale al cerrar sobre la SMA corta)
    rebote_len: int = 5            # SMA corta para la salida por rebote

    def to_dict(self) -> dict:
        return asdict(self)


# ───────────────────────────── Indicadores ─────────────────────────────
def rolling_poc(df: pd.DataFrame, length: int, bins: int) -> pd.Series:
    """Point of Control aproximado: precio con más volumen en las últimas `length` velas."""
    hlc3 = ((df["High"] + df["Low"] + df["Close"]) / 3).to_numpy(float)
    vol = df["Volume"].fillna(0).to_numpy(float)
    hi = df["High"].rolling(length).max().to_numpy(float)
    lo = df["Low"].rolling(length).min().to_numpy(float)
    out = np.full(len(df), np.nan)
    for t in range(length - 1, len(df)):
        h, l = hi[t], lo[t]
        if not np.isfinite(h) or not np.isfinite(l) or h <= l:
            continue
        step = (h - l) / bins
        p = hlc3[t - length + 1: t + 1]
        v = vol[t - length + 1: t + 1]
        idx = np.clip(((p - l) / step).astype(int), 0, bins - 1)
        hist = np.bincount(idx, weights=v, minlength=bins)
        out[t] = l + step * (hist.argmax() + 0.5)
    return pd.Series(out, index=df.index)


def earnings_window(index: pd.DatetimeIndex, dates: Iterable, before: int, after: int) -> pd.Series:
    """True en las fechas que caen dentro de [reporte - before, reporte + after]."""
    mask = pd.Series(False, index=index)
    days = index.normalize()
    for d in dates or []:
        d = pd.Timestamp(d)
        if d.tzinfo is not None:
            d = d.tz_localize(None)
        d = d.normalize()
        mask |= (days >= d - pd.Timedelta(days=before)) & (days <= d + pd.Timedelta(days=after))
    return mask


def compute_indicators(df: pd.DataFrame, p: Params, earnings_dates=None) -> pd.DataFrame:
    """Agrega columnas de indicadores y la señal de entrada al DataFrame OHLCV."""
    d = df.copy()
    tol = p.zone_pct / 100
    d["sma_fast"] = d["Close"].rolling(p.sma_fast).mean()
    d["sma_slow"] = d["Close"].rolling(p.sma_slow).mean()
    d["vol_avg"] = d["Volume"].rolling(p.vol_len).mean()
    d["poc"] = rolling_poc(d, p.poc_len, p.poc_bins) if p.use_poc else np.nan

    def touch(lvl: pd.Series) -> pd.Series:
        return lvl.notna() & (d["Low"] <= lvl * (1 + tol)) & (d["Close"] >= lvl * (1 - tol))

    t_fast, t_slow = touch(d["sma_fast"]), touch(d["sma_slow"])
    t_poc = touch(d["poc"]) if p.use_poc else pd.Series(False, index=d.index)
    d["in_zone"] = t_fast | t_slow | t_poc
    d["zone_lvl"] = pd.concat(
        [d["sma_fast"].where(t_fast), d["sma_slow"].where(t_slow), d["poc"].where(t_poc)], axis=1
    ).max(axis=1)
    d["zone_src"] = np.select(
        [t_fast & (d["zone_lvl"] == d["sma_fast"]), t_slow & (d["zone_lvl"] == d["sma_slow"]), t_poc],
        [f"SMA {p.sma_fast}", f"SMA {p.sma_slow}", "POC"], default="",
    )

    if p.trend_mode == "sma50>sma200":
        d["trend_ok"] = d["sma_fast"] > d["sma_slow"]
    elif p.trend_mode == "precio>sma200":
        d["trend_ok"] = d["Close"] > d["sma_slow"]
    else:
        d["trend_ok"] = True

    recent_high = d["High"].rolling(p.pb_look).max()
    d["pullback"] = (recent_high - d["Low"]) / recent_high * 100 >= p.pb_min
    d["pb_vol"] = d["Volume"].rolling(p.dry_len).mean()
    d["vol_ratio"] = d["pb_vol"] / d["vol_avg"]
    d["dry_up"] = (d["vol_ratio"] < p.dry_mult) if p.use_dry else True
    d["sma_exit"] = d["Close"].rolling(p.rebote_len).mean()

    d["in_earnings"] = (
        earnings_window(d.index, earnings_dates, p.er_before, p.er_after)
        if p.use_earnings else False
    )
    d["signal"] = (
        d["in_zone"] & d["pullback"] & d["dry_up"] & d["trend_ok"] & ~d["in_earnings"]
    ).fillna(False)
    return d


# ───────────────────────────── Backtest ─────────────────────────────
def backtest(d: pd.DataFrame, p: Params, ticker: str = "") -> pd.DataFrame:
    """Simula órdenes límite + TP/SL sobre un DataFrame que ya tiene indicadores.

    Devuelve una fila por operación (incluida una posible operación abierta o una
    orden pendiente al final, para el monitor)."""
    o, h, l, c = (d[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    vol, vavg = d["Volume"].to_numpy(float), d["vol_avg"].to_numpy(float)
    sig, lvl = d["signal"].to_numpy(bool), d["zone_lvl"].to_numpy(float)
    src = d["zone_src"].to_numpy(object)
    sma_exit = d["sma_exit"].to_numpy(float)
    fixed_tp = p.exit_mode == "fijo"
    idx = d.index
    tol, cost = p.zone_pct / 100, p.cost_pct / 100
    trades: list[dict] = []
    n, t = len(d), 0

    while t < n:
        if not sig[t] or not np.isfinite(lvl[t]):
            t += 1
            continue
        limit = lvl[t]
        tp, sl = limit * (1 + p.tp_pct / 100), limit * (1 - p.sl_pct / 100)
        base = dict(ticker=ticker, signal_date=idx[t], zone=src[t], limit=limit, tp=tp, sl=sl)

        # 1) ¿Se ejecuta la orden límite en las próximas velas?
        fill_t, entry = None, np.nan
        for k in range(t + 1, min(t + 1 + p.order_valid, n)):
            if l[k] <= limit:
                fill_t, entry = k, min(o[k], limit)   # gap a la baja: se llena en la apertura
                break
        if fill_t is None:
            expired = t + p.order_valid < n
            trades.append({**base, "status": "orden vencida" if expired else "orden pendiente"})
            t += 1
            continue

        # 2) Gestión de la posición: TP y SL se miden desde el precio real de entrada
        tp, sl = entry * (1 + p.tp_pct / 100), entry * (1 - p.sl_pct / 100)
        base.update(tp=tp, sl=sl)
        exit_t, exit_px, reason = None, np.nan, ""
        for k in range(fill_t, min(fill_t + p.max_bars, n)):
            if k > fill_t and o[k] <= sl:                       # gap por debajo del stop
                exit_t, exit_px, reason = k, o[k], "SL (gap)"
            elif l[k] <= sl:
                exit_t, exit_px, reason = k, sl, "SL"
            elif fixed_tp and k > fill_t and o[k] >= tp:        # gap por encima del objetivo
                exit_t, exit_px, reason = k, o[k], "TP (gap)"
            elif fixed_tp and h[k] >= tp and (k > fill_t or c[k] >= tp or o[k] >= tp):
                exit_t, exit_px, reason = k, tp, "TP"
            elif not fixed_tp and k > fill_t and c[k] > sma_exit[k]:
                exit_t, exit_px, reason = k, c[k], "Rebote"
            elif p.tech_exit and k > fill_t and c[k] < limit * (1 - tol) and vol[k] > vavg[k]:
                exit_t, exit_px, reason = k, c[k], "Soporte perforado"
            if exit_t is not None:
                break
        if exit_t is None:
            last = min(fill_t + p.max_bars - 1, n - 1)
            if fill_t + p.max_bars - 1 <= n - 1:
                exit_t, exit_px, reason = last, c[last], "Tiempo"
            else:  # sigue abierta al final de los datos
                ret = (c[-1] * (1 - cost)) / (entry * (1 + cost)) - 1
                trades.append({**base, "status": "abierta", "entry_date": idx[fill_t], "entry": entry,
                               "exit_date": pd.NaT, "exit": c[-1], "reason": "Abierta",
                               "ret_pct": ret * 100, "bars": n - 1 - fill_t})
                break

        ret = (exit_px * (1 - cost)) / (entry * (1 + cost)) - 1
        trades.append({**base, "status": "cerrada", "entry_date": idx[fill_t], "entry": entry,
                       "exit_date": idx[exit_t], "exit": exit_px, "reason": reason,
                       "ret_pct": ret * 100, "bars": exit_t - fill_t + 1})
        t = exit_t + 1

    cols = ["ticker", "status", "signal_date", "zone", "limit", "tp", "sl", "entry_date",
            "entry", "exit_date", "exit", "reason", "ret_pct", "bars"]
    return pd.DataFrame(trades, columns=cols)


# ───────────────────────────── Métricas ─────────────────────────────
def equity_curve(d: pd.DataFrame, trades: pd.DataFrame) -> pd.Series:
    """Curva de capital (base 1) operando el 100 % de la caja asignada en cada trade."""
    eq = pd.Series(1.0, index=d.index)
    closed = trades[trades["status"] == "cerrada"]
    factor = 1.0
    for _, tr in closed.iterrows():
        factor *= 1 + tr["ret_pct"] / 100
        eq.loc[tr["exit_date"]:] = factor
    return eq


def max_drawdown(eq: pd.Series) -> float:
    if eq.empty:
        return 0.0
    return float((eq / eq.cummax() - 1).min() * 100)


def summarize(trades: pd.DataFrame, d: pd.DataFrame | None = None) -> dict:
    closed = trades[trades["status"] == "cerrada"] if len(trades) else trades
    n = len(closed)
    out = {"operaciones": n, "acierto_pct": np.nan, "ganancia_media_pct": np.nan,
           "perdida_media_pct": np.nan, "esperanza_pct": np.nan, "profit_factor": np.nan,
           "retorno_total_pct": 0.0, "max_drawdown_pct": 0.0, "ops_por_anio": np.nan,
           "dias_promedio": np.nan, "buy_hold_pct": np.nan}
    if d is not None and len(d) > 1:
        out["buy_hold_pct"] = (d["Close"].iloc[-1] / d["Close"].iloc[0] - 1) * 100
        years = (d.index[-1] - d.index[0]).days / 365.25
    else:
        years = np.nan
    if n == 0:
        return out
    r = closed["ret_pct"]
    wins, losses = r[r > 0], r[r <= 0]
    out["acierto_pct"] = len(wins) / n * 100
    out["ganancia_media_pct"] = wins.mean() if len(wins) else 0.0
    out["perdida_media_pct"] = losses.mean() if len(losses) else 0.0
    out["esperanza_pct"] = r.mean()
    out["profit_factor"] = wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf
    out["retorno_total_pct"] = (np.prod(1 + r / 100) - 1) * 100
    out["dias_promedio"] = closed["bars"].mean()
    if d is not None:
        out["max_drawdown_pct"] = max_drawdown(equity_curve(d, trades))
    if years and years > 0:
        out["ops_por_anio"] = n / years
    return out


def latest_status(d: pd.DataFrame, trades: pd.DataFrame, p: Params) -> dict:
    """Foto del último día cerrado, para el monitor y las alertas."""
    last = d.iloc[-1]
    date = d.index[-1]
    levels = {f"SMA {p.sma_fast}": last["sma_fast"], f"SMA {p.sma_slow}": last["sma_slow"]}
    if p.use_poc:
        levels["POC"] = last["poc"]
    below = {k: v for k, v in levels.items() if pd.notna(v) and v <= last["Close"] * (1 + p.zone_pct / 100)}
    nearest_name, nearest_lvl = (max(below.items(), key=lambda kv: kv[1]) if below else (None, np.nan))
    dist = (last["Close"] / nearest_lvl - 1) * 100 if pd.notna(nearest_lvl) else np.nan

    open_tr = trades[trades["status"] == "abierta"]
    pending = trades[(trades["status"] == "orden pendiente")]
    events = []
    if bool(last["signal"]):
        events.append("señal")
    if len(trades):
        if (trades["entry_date"] == date).any():
            events.append("entrada ejecutada")
        closed_today = trades[(trades["status"] == "cerrada") & (trades["exit_date"] == date)]
        for _, tr in closed_today.iterrows():
            events.append("TP" if tr["reason"].startswith("TP") else
                          "SL" if tr["reason"].startswith("SL") or tr["reason"] == "Soporte perforado" else "salida por tiempo")

    if len(open_tr):
        estado = "En posición"
    elif len(pending):
        estado = "Orden límite pendiente"
    elif bool(last["in_earnings"]):
        estado = "Pausa por balances"
    elif not bool(last["trend_ok"]):
        estado = "Sin tendencia"
    elif bool(last["in_zone"]):
        estado = "En zona, esperando secado"
    else:
        estado = "Esperando retroceso"

    return {
        "fecha": date, "cierre": last["Close"], "estado": estado, "eventos": events,
        "tendencia_ok": bool(last["trend_ok"]), "en_zona": bool(last["in_zone"]),
        "nivel_cercano": nearest_name, "precio_nivel": nearest_lvl, "distancia_pct": dist,
        "vol_ratio": last["vol_ratio"], "balances": bool(last["in_earnings"]),
        "posicion": open_tr.iloc[-1].to_dict() if len(open_tr) else None,
        "orden": pending.iloc[-1].to_dict() if len(pending) else None,
    }


def run(df: pd.DataFrame, p: Params, ticker: str = "", earnings_dates=None):
    """Atajo: indicadores + backtest + resumen."""
    d = compute_indicators(df, p, earnings_dates)
    trades = backtest(d, p, ticker)
    return d, trades, summarize(trades, d)


def period_metrics(results: dict, start=None, end=None) -> dict:
    """Métricas de la caja (partes iguales entre activos) para las operaciones que
    ENTRARON dentro de [start, end). `results` = {ticker: (d, trades)}."""
    start = pd.Timestamp(start) if start is not None else None
    end = pd.Timestamp(end) if end is not None else None
    curves, sel = [], []
    for d, tr in results.values():
        dd = d
        if start is not None:
            dd = dd[dd.index >= start]
        if end is not None:
            dd = dd[dd.index < end]
        if dd.empty:
            continue
        t = tr[(tr["status"] == "cerrada") & tr["entry_date"].between(dd.index[0], dd.index[-1])]
        sel.append(t)
        curves.append(equity_curve(dd, t))
    if not curves:
        return {}
    port = pd.concat(curves, axis=1).ffill().fillna(1.0).mean(axis=1)
    allt = pd.concat(sel, ignore_index=True) if sel else pd.DataFrame(columns=["status"])
    s = summarize(allt)
    years = (port.index[-1] - port.index[0]).days / 365.25
    bh = pd.concat([d.loc[port.index[0]:port.index[-1], "Close"] for d, _ in results.values()], axis=1)
    bh = (bh / bh.bfill().iloc[0]).ffill().mean(axis=1)
    return {
        "Operaciones": s["operaciones"],
        "Ops/año": s["operaciones"] / years if years else np.nan,
        "Acierto %": s["acierto_pct"],
        "Resultado medio %": s["esperanza_pct"],
        "Profit factor": s["profit_factor"],
        "Caja anual %": (port.iloc[-1] ** (1 / years) - 1) * 100 if years else np.nan,
        "Peor caída %": max_drawdown(port),
        "Comprar y mantener anual %": (bh.iloc[-1] ** (1 / years) - 1) * 100 if years else np.nan,
    }
