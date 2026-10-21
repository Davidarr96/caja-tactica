"""
Caja Táctica · Simulador y monitor de la estrategia de swing institucional.
Ejecutar:  streamlit run app.py
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import data as dt
from dataclasses import replace

import events as ev
import portfolio as pf
import rotation as ro
from strategy import (Params, compute_indicators, backtest, summarize, equity_curve, latest_status,
                      max_drawdown, period_metrics)

st.set_page_config(page_title="Caja Táctica · Swing", page_icon="📈", layout="wide")

DEFAULT_TICKERS = ["SPY", "QQQ", "JPM", "GOOGL", "KO"]
DEMO = os.environ.get("CAJA_DEMO") == "1"   # datos inventados, para probar sin internet


# ───────────────────────────── Datos (con caché) ─────────────────────────────
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load(ticker: str, start: str):
    if DEMO:
        seed = sum(map(ord, ticker))
        return dt.synthetic_prices(seed), []
    return dt.get_prices(ticker, start), dt.get_earnings_dates(ticker)


def fmt_pct(x, dec=1, sign=True):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:+.{dec}f}%" if sign else f"{x:.{dec}f}%"


# ───────────────────────────── Barra lateral ─────────────────────────────
st.sidebar.title("📈 Caja Táctica")
st.sidebar.caption("Swing institucional · TP +4 % / SL −3 %")
if st.sidebar.button("🔄 Actualizar datos ahora", width="stretch",
                     help="Vuelve a bajar los precios y las fechas de balances sin esperar las 6 horas de la caché."):
    st.cache_data.clear()
    st.session_state["ultima_act"] = pd.Timestamp.now(tz="America/Argentina/Buenos_Aires")
    st.rerun()
if "ultima_act" in st.session_state:
    st.sidebar.caption(f"Actualizado a las {st.session_state['ultima_act']:%H:%M} (hora de Argentina)")

tickers = st.sidebar.multiselect("Activos", DEFAULT_TICKERS + ["MSFT", "AAPL", "JNJ", "PG", "V", "DIA"],
                                 default=DEFAULT_TICKERS)
extra = st.sidebar.text_input("Agregar otros (separados por coma)", placeholder="ej.: AMZN, XOM")
tickers += [t.strip().upper() for t in extra.split(",") if t.strip()]
tickers = list(dict.fromkeys(tickers))
start = st.sidebar.date_input("Historia desde", pd.Timestamp("2012-01-01")).strftime("%Y-%m-%d")

with st.sidebar.expander("Gestión de riesgo", expanded=True):
    tp = st.number_input("Take Profit (%)", 0.5, 20.0, 4.0, 0.5)
    sl = st.number_input("Stop Loss (%)", 0.5, 20.0, 3.0, 0.5)
    max_bars = st.slider("Vigencia máxima (velas)", 3, 60, 15)
    order_valid = st.slider("Orden límite válida (velas)", 1, 10, 3)
    cost = st.number_input("Costo por lado: comisión + deslizamiento (%)", 0.0, 1.0, 0.05, 0.01)
    tech_exit = st.checkbox("Salir si cierra bajo el soporte con volumen alto", True)

with st.sidebar.expander("Reglas de entrada"):
    zone_pct = st.slider("Tolerancia de zona (%)", 0.2, 3.0, 1.0, 0.1)
    trend_label = st.selectbox("Filtro de tendencia", ["SMA50 > SMA200", "Precio > SMA200", "Sin filtro"])
    pb_min = st.slider("Retroceso mínimo desde el máximo (%)", 0.0, 8.0, 2.0, 0.5)
    pb_look = st.slider("Máximo de las últimas (velas)", 3, 30, 10)
    dry_mult = st.slider("Secado: volumen < x × media", 0.3, 1.2, 0.7, 0.05)
    dry_len = st.slider("Velas del retroceso a promediar", 1, 10, 3)
    use_poc = st.checkbox("Usar POC anual como zona", True)
    use_er = st.checkbox("Pausar alrededor de balances", True)
    er_before = st.slider("Días antes del reporte", 0, 21, 7)
    er_after = st.slider("Días después del reporte", 0, 10, 2)

P = Params(tp_pct=tp, sl_pct=sl, max_bars=max_bars, order_valid=order_valid, cost_pct=cost,
           tech_exit=tech_exit, zone_pct=zone_pct,
           trend_mode={"SMA50 > SMA200": "sma50>sma200", "Precio > SMA200": "precio>sma200"}.get(trend_label, "ninguno"),
           pb_min=pb_min, pb_look=pb_look, dry_mult=dry_mult, dry_len=dry_len, use_poc=use_poc,
           use_earnings=use_er, er_before=er_before, er_after=er_after)

if DEMO:
    st.warning("Modo demostración: los precios son **inventados**. Los resultados no significan nada.")

if not tickers:
    st.info("Elegí al menos un activo en la barra lateral.")
    st.stop()

# ───────────────────────────── Cálculo ─────────────────────────────
results, errors = {}, []
with st.spinner("Descargando datos y simulando…"):
    for tk in tickers:
        try:
            df, er = load(tk, start)
            d = compute_indicators(df, P, er)
            tr = backtest(d, P, tk)
            results[tk] = dict(d=d, trades=tr, summary=summarize(tr, d), er=er,
                               status=latest_status(d, tr, P))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{tk}: {e}")
for e in errors:
    st.error(f"No se pudo cargar {e}")
if results:
    _last = max(r["d"].index[-1] for r in results.values())
    st.sidebar.caption(f"Último dato de precios: {_last:%d/%m/%Y}")
if not results:
    st.stop()

tab_sim, tab_mon, tab_chart, tab_var, tab_port, tab_help, tab_ins, tab_cal, tab_rot = st.tabs(
    ["📊 Simulación", "📡 Monitor de hoy", "🕯️ Gráfico", "🧪 Comparar variantes", "💼 Cartera mejorada",
     "📘 Cómo funciona", "💡 Insights", "📅 Calendario", "🐢 Estrategias mensuales"])

# ───────────────────────────── Simulación ─────────────────────────────
with tab_sim:
    all_trades = pd.concat([r["trades"] for r in results.values()], ignore_index=True)
    closed = all_trades[all_trades["status"] == "cerrada"]
    tot = summarize(all_trades)

    # Cartera: caja repartida en partes iguales entre los activos
    curves = pd.concat({tk: equity_curve(r["d"], r["trades"]) for tk, r in results.items()}, axis=1).ffill().fillna(1.0)
    port = curves.mean(axis=1)
    bh = pd.concat({tk: r["d"]["Close"] / r["d"]["Close"].iloc[0] for tk, r in results.items()}, axis=1).ffill().bfill().mean(axis=1)
    years = (port.index[-1] - port.index[0]).days / 365.25
    cagr = (port.iloc[-1] ** (1 / years) - 1) * 100 if years > 0 else np.nan
    cagr_bh = (bh.iloc[-1] ** (1 / years) - 1) * 100 if years > 0 else np.nan

    st.subheader("Resultado de la caja táctica")
    breakeven = P.sl_pct / (P.tp_pct + P.sl_pct) * 100
    c = st.columns(6)
    c[0].metric("Operaciones", f"{tot['operaciones']}", f"{tot['operaciones'] / years:.1f} por año" if years else None,
                delta_color="off")
    c[1].metric("Tasa de acierto", fmt_pct(tot["acierto_pct"], 0, False),
                f"empate ≈ {breakeven:.0f}%", delta_color="off")
    c[2].metric("Resultado medio por operación", fmt_pct(tot["esperanza_pct"], 2))
    c[3].metric("Profit factor", "—" if not np.isfinite(tot["profit_factor"]) else f"{tot['profit_factor']:.2f}",
                "> 1 gana plata", delta_color="off")
    c[4].metric("Rentab. anual de la caja", fmt_pct(cagr), f"comprar y mantener: {fmt_pct(cagr_bh)}", delta_color="off")
    c[5].metric("Peor caída (drawdown)", fmt_pct(max_drawdown(port), 1))

    fig = go.Figure()
    fig.add_scatter(x=port.index, y=(port - 1) * 100, name="Caja táctica (estrategia)", line=dict(width=2.5, color="#1f9d74"))
    fig.add_scatter(x=bh.index, y=(bh - 1) * 100, name="Comprar y mantener los mismos activos",
                    line=dict(width=1.5, color="#8a8f98", dash="dot"))
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="Rendimiento acumulado (%)",
                      legend=dict(orientation="h", y=1.08), hovermode="x unified")
    st.plotly_chart(fig, width="stretch")
    st.caption("La caja se reparte en partes iguales entre los activos y cada parte opera el 100 % en cada señal. "
               "El tiempo fuera del mercado no rinde nada en esta simulación (en la realidad podría estar en un fondo de liquidez).")

    st.subheader("Por activo")
    rows = []
    for tk, r in results.items():
        s = r["summary"]
        rows.append({"Activo": tk, "Operaciones": s["operaciones"], "Acierto %": s["acierto_pct"],
                     "Ganancia media %": s["ganancia_media_pct"], "Pérdida media %": s["perdida_media_pct"],
                     "Resultado medio %": s["esperanza_pct"], "Profit factor": s["profit_factor"],
                     "Total estrategia %": s["retorno_total_pct"], "Comprar y mantener %": s["buy_hold_pct"],
                     "Peor caída %": s["max_drawdown_pct"], "Días promedio": s["dias_promedio"]})
    st.dataframe(pd.DataFrame(rows).set_index("Activo").round(2), width="stretch")

    st.subheader("Todas las operaciones")
    show = closed.sort_values("entry_date", ascending=False).copy()
    show = show.rename(columns={"ticker": "Activo", "signal_date": "Señal", "zone": "Zona", "entry_date": "Entrada",
                                "entry": "Precio entrada", "exit_date": "Salida", "exit": "Precio salida",
                                "reason": "Motivo", "ret_pct": "Resultado %", "bars": "Días"})
    st.dataframe(show[["Activo", "Señal", "Zona", "Entrada", "Precio entrada", "Salida", "Precio salida",
                       "Motivo", "Resultado %", "Días"]].round({"Precio entrada": 2, "Precio salida": 2, "Resultado %": 2}),
                 width="stretch", height=360)
    st.download_button("Descargar operaciones (CSV)", closed.to_csv(index=False).encode(), "operaciones.csv", "text/csv")
    if len(closed):
        st.caption("Motivos de salida: " + ", ".join(f"{k}: {v}" for k, v in closed["reason"].value_counts().items()))

# ───────────────────────────── Monitor ─────────────────────────────
with tab_mon:
    st.subheader("Estado al último cierre")
    rows = []
    for tk, r in results.items():
        s = r["status"]
        nxt = dt.next_earnings(r["er"], pd.Timestamp.today())
        pos = s["posicion"]
        ordr = s["orden"]
        detalle = ""
        if pos:
            detalle = f"Entró a {pos['entry']:.2f} · TP {pos['tp']:.2f} · SL {pos['sl']:.2f} · va {pos['ret_pct']:+.1f}%"
        elif ordr:
            detalle = f"Límite {ordr['limit']:.2f} · TP {ordr['tp']:.2f} · SL {ordr['sl']:.2f}"
        rows.append({"Activo": tk, "Fecha": s["fecha"].date(), "Cierre": round(s["cierre"], 2), "Estado": s["estado"],
                     "Nivel más cercano": s["nivel_cercano"] or "—",
                     "Distancia al nivel %": round(s["distancia_pct"], 2) if pd.notna(s["distancia_pct"]) else None,
                     "Vol. retroceso / media": round(s["vol_ratio"], 2) if pd.notna(s["vol_ratio"]) else None,
                     "Próximo balance": nxt.date() if nxt is not None else "—",
                     "Detalle": detalle, "Hoy": ", ".join(s["eventos"]) or ""})
    mon = pd.DataFrame(rows).set_index("Activo")
    st.dataframe(mon, width="stretch")
    hot = [tk for tk, r in results.items() if r["status"]["eventos"]]
    if hot:
        st.success("Novedades hoy: " + "; ".join(f"**{tk}**: {', '.join(results[tk]['status']['eventos'])}" for tk in hot))
    else:
        st.info("Sin novedades en el último cierre. "
                "“Distancia al nivel” indica cuánto tendría que bajar el precio para llegar a la zona de compra.")

# ───────────────────────────── Gráfico ─────────────────────────────
with tab_chart:
    tk = st.selectbox("Activo", list(results))
    r = results[tk]
    months = st.slider("Meses a mostrar", 3, 60, 18)
    d = r["d"][r["d"].index >= r["d"].index[-1] - pd.DateOffset(months=months)]
    trs = r["trades"]
    trs = trs[trs["entry_date"].notna() & (trs["entry_date"] >= d.index[0])]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03)
    fig.add_candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="Precio",
                        increasing_line_color="#26a69a", decreasing_line_color="#ef5350", row=1, col=1)
    fig.add_scatter(x=d.index, y=d["sma_fast"], name=f"SMA {P.sma_fast}", line=dict(color="#2962FF", width=1.6), row=1, col=1)
    fig.add_scatter(x=d.index, y=d["sma_slow"], name=f"SMA {P.sma_slow}", line=dict(color="#FF6D00", width=2), row=1, col=1)
    if P.use_poc:
        fig.add_scatter(x=d.index, y=d["poc"], name="POC anual", line=dict(color="#AB47BC", width=1, shape="hv"), row=1, col=1)
    for _, t in trs.iterrows():
        end = t["exit_date"] if pd.notna(t["exit_date"]) else d.index[-1]
        win = t["ret_pct"] > 0
        fig.add_shape(type="rect", x0=t["entry_date"], x1=end, y0=t["entry"], y1=t["tp"], fillcolor="#26a69a",
                      opacity=0.12, line_width=0, row=1, col=1)
        fig.add_shape(type="rect", x0=t["entry_date"], x1=end, y0=t["sl"], y1=t["entry"], fillcolor="#ef5350",
                      opacity=0.10, line_width=0, row=1, col=1)
    if len(trs):
        fig.add_scatter(x=trs["entry_date"], y=trs["entry"], mode="markers", name="Entrada",
                        marker=dict(symbol="triangle-up", size=11, color="#00C853"), row=1, col=1)
        ex = trs[trs["exit_date"].notna()]
        fig.add_scatter(x=ex["exit_date"], y=ex["exit"], mode="markers", name="Salida",
                        marker=dict(symbol="x", size=9, color=np.where(ex["ret_pct"] > 0, "#00897B", "#E53935")),
                        text=ex["reason"] + " " + ex["ret_pct"].round(1).astype(str) + "%", row=1, col=1)
    er = d[d["in_earnings"]]
    if len(er):
        fig.add_scatter(x=er.index, y=er["Low"] * 0.985, mode="markers", name="Ventana de balances",
                        marker=dict(symbol="square", size=4, color="#9e9e9e"), row=1, col=1)
    colors = np.where(d["dry_up"], "#4fc3f7", "#b0b4bb")
    fig.add_bar(x=d.index, y=d["Volume"], marker_color=colors, name="Volumen (celeste = secado)", row=2, col=1)
    fig.add_scatter(x=d.index, y=d["vol_avg"], name="Media de volumen", line=dict(color="#555", width=1), row=2, col=1)
    fig.update_layout(height=640, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=1.04), hovermode="x unified")
    st.plotly_chart(fig, width="stretch")

# ───────────────────────────── Variantes ─────────────────────────────
BASE = Params()
VARIANTS = {
    "A · Original (Hugo)": BASE,
    "B · Secado más laxo (85 %)": replace(BASE, dry_mult=0.85),
    "C · Sin salida por soporte": replace(BASE, tech_exit=False),
    "D · B + C": replace(BASE, dry_mult=0.85, tech_exit=False),
    "E · Sin filtro de volumen (D sin secado)": replace(BASE, use_dry=False, tech_exit=False),
    "F · D con TP 6 % / SL 4 %": replace(BASE, dry_mult=0.85, tech_exit=False, tp_pct=6.0, sl_pct=4.0),
    "G · D con salida por rebote (cierre > SMA 5)": replace(BASE, dry_mult=0.85, tech_exit=False, exit_mode="rebote"),
}
SPLIT = "2020-01-01"


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def compare_variants(tickers: tuple, start: str) -> pd.DataFrame:
    rows = []
    data = {tk: load(tk, start) for tk in tickers}
    for name, vp in VARIANTS.items():
        res = {}
        for tk, (df, er) in data.items():
            d = compute_indicators(df, vp, er)
            res[tk] = (d, backtest(d, vp, tk))
        for label, a, b in (("Diseño · hasta 2019", None, SPLIT), ("Validación · 2020 a hoy", SPLIT, None),
                            ("Período completo", None, None)):
            m = period_metrics(res, a, b)
            rows.append({"Variante": name, "Período": label, **m})
    return pd.DataFrame(rows)


with tab_var:
    st.subheader("Comparar variantes de la estrategia")
    st.caption("Mismos activos y fechas que la barra lateral; las reglas de cada variante son fijas (no usan los controles). "
               "Primero se mira el período de diseño; la variante elegida tiene que sostenerse en la validación, que no se usó para elegirla.")
    with st.spinner("Simulando variantes…"):
        cmp = compare_variants(tuple(results), start)
    for per in ("Diseño · hasta 2019", "Validación · 2020 a hoy", "Período completo"):
        st.markdown(f"**{per}**")
        st.table(cmp[cmp["Período"] == per].drop(columns="Período").set_index("Variante")
                 .style.format(precision=1, na_rep="—").format({"Profit factor": "{:.2f}", "Operaciones": "{:.0f}"}))
    st.markdown("""
- **A** reglas originales · **B** el volumen solo tiene que estar por debajo del 85 % de la media · **C** no corta antes por perforar el soporte
- **E** prueba si el filtro de volumen aporta algo · **F** objetivo y stop más amplios · **G** sin objetivo fijo: sale el primer día que cierra sobre la media de 5 ruedas (con stop −3 %)
""")

# ───────────────────────────── Cartera mejorada ─────────────────────────────
UNIVERSE = ("SPY, QQQ, DIA, IWM, XLF, XLK, XLV, XLP, XLE, XLI, XLU, AAPL, MSFT, GOOGL, AMZN, JPM, V, MA, "
            "JNJ, PG, KO, PEP, WMT, HD, UNH, XOM, COST, MCD")
RULES = {
    "F · TP 6 % / SL 4 %, secado 85 %, sin salida por soporte": replace(
        Params(), dry_mult=0.85, tech_exit=False, tp_pct=6.0, sl_pct=4.0),
    "A · Reglas originales de Hugo": Params(),
}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def run_portfolio(universe: tuple, start: str, rules: str, max_pos: int, cash_rate: float):
    rp = RULES[rules]
    data, trades, fails = {}, {}, []
    for tk in universe:
        try:
            df, er = load(tk, start)
            d = compute_indicators(df, rp, er)
            data[tk], trades[tk] = d, backtest(d, rp, tk)
        except Exception:  # noqa: BLE001
            fails.append(tk)
    first = max(d.index[0] for d in data.values())   # arranca cuando todos tienen datos
    eq, log, expo = pf.simulate(data, trades, max_pos, cash_rate, rp.cost_pct, start=first)

    # Referencias: caja original (5 activos, reglas A, una parte fija por activo) y comprar y mantener
    base = Params()
    curves = []
    for tk in DEFAULT_TICKERS:
        df, er = load(tk, start)
        d = compute_indicators(df, base, er)
        curves.append(equity_curve(d, backtest(d, base, tk)))
    orig = pd.concat(curves, axis=1).ffill().fillna(1.0).mean(axis=1)
    spy = load("SPY", start)[0]["Close"]
    ew = pd.concat({tk: d["Close"] for tk, d in data.items()}, axis=1).ffill()
    ew = ew[ew.index >= first]
    ew = (ew / ew.iloc[0]).mean(axis=1)
    refs = {"Cartera mejorada": eq, "Caja original (5 activos, reglas A)": orig[orig.index >= first],
            "Comprar y mantener SPY": spy[spy.index >= first], "Comprar y mantener la lista (partes iguales)": ew}
    refs = {k: v / v.iloc[0] for k, v in refs.items()}
    held = log[(log["tomada"]) & (log["status"] == "abierta")] if len(log) else pd.DataFrame()
    pend = [tr[tr["status"] == "orden pendiente"] for tr in trades.values()]
    current = pd.concat([held] + pend, ignore_index=True) if len(held) or any(len(p) for p in pend) else pd.DataFrame(
        columns=["ticker", "status", "zone", "limit", "entry_date", "entry", "tp", "sl", "ret_pct"])
    return refs, log, expo, fails, current


with tab_port:
    st.subheader("Cartera mejorada")
    st.markdown("Tres cambios respecto de la simulación original, que sigue igual en la primera pestaña:  \n"
                "**1.** toda la caja trabaja junta, en hasta *N* posiciones a la vez · "
                "**2.** busca señales en una lista amplia de activos · "
                "**3.** la plata que espera una señal rinde una tasa (caución / letras del Tesoro).")
    c1, c2, c3 = st.columns([2, 1, 1])
    rules = c1.selectbox("Reglas de entrada y salida", list(RULES))
    max_pos = c2.slider("Posiciones simultáneas", 1, 6, 3)
    cash_rate = c3.number_input("Tasa del efectivo (% anual en USD)", 0.0, 10.0, 4.0, 0.5)
    uni_txt = st.text_area("Lista de activos", UNIVERSE, height=70)
    universe = tuple(dict.fromkeys(t.strip().upper() for t in uni_txt.split(",") if t.strip()))

    with st.spinner(f"Simulando la cartera con {len(universe)} activos (la primera vez tarda un poco)…"):
        refs, plog, expo, fails, current = run_portfolio(universe, start, rules, max_pos, cash_rate)
    if fails:
        st.warning("No se pudieron cargar: " + ", ".join(fails))

    eqp = refs["Cartera mejorada"]
    full = {k: pf.stats(v) for k, v in refs.items()}
    taken = plog[plog["tomada"]] if len(plog) else plog
    closed_t = taken[taken["status"] == "cerrada"] if len(taken) else taken
    yrs = (eqp.index[-1] - eqp.index[0]).days / 365.25
    m = st.columns(5)
    m[0].metric("Rentabilidad anual", fmt_pct(full["Cartera mejorada"]["Anual %"]),
                f"caja original: {fmt_pct(full['Caja original (5 activos, reglas A)']['Anual %'])}", delta_color="off")
    m[1].metric("Peor caída", fmt_pct(full["Cartera mejorada"]["Peor caída %"]),
                f"SPY: {fmt_pct(full['Comprar y mantener SPY']['Peor caída %'])}", delta_color="off")
    m[2].metric("Tiempo invertido (promedio)", f"{expo.mean() * 100:.0f}%")
    m[3].metric("Operaciones tomadas", f"{len(taken)}", f"{len(taken) / yrs:.0f} por año · {int((~plog['tomada']).sum()) if len(plog) else 0} omitidas por falta de lugar",
                delta_color="off")
    m[4].metric("Acierto", fmt_pct(len(closed_t[closed_t["ret_pct"] > 0]) / len(closed_t) * 100, 0, False) if len(closed_t) else "—")

    fig = go.Figure()
    styles = {"Cartera mejorada": dict(width=2.8, color="#1f9d74"),
              "Caja original (5 activos, reglas A)": dict(width=1.8, color="#e8a33d"),
              "Comprar y mantener SPY": dict(width=1.4, color="#5b6b8c", dash="dot"),
              "Comprar y mantener la lista (partes iguales)": dict(width=1.4, color="#9aa0a8", dash="dash")}
    for k, v in refs.items():
        fig.add_scatter(x=v.index, y=(v - 1) * 100, name=k, line=styles[k])
    fig.update_layout(height=400, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="Rendimiento acumulado (%)",
                      legend=dict(orientation="h", y=1.1), hovermode="x unified")
    st.plotly_chart(fig, width="stretch")

    st.markdown("**Comparación por período**")
    rows = []
    for per, a, b in (("Diseño · hasta 2019", None, SPLIT), ("Validación · 2020 a hoy", SPLIT, None), ("Período completo", None, None)):
        for k, v in refs.items():
            rows.append({"Período": per, "Serie": k, **pf.stats(v, a, b)})
    st.table(pd.DataFrame(rows).set_index(["Período", "Serie"])
             .style.format(precision=1, na_rep="—").format({"Anual / caída": "{:.2f}"}))
    st.caption("“Anual / caída” = rentabilidad anual dividida por la peor caída: cuánto se gana por cada punto de caída soportado. "
               "Más alto es mejor.")

    st.markdown("**Posiciones y órdenes al último cierre**")
    if len(current):
        cur = current[["ticker", "status", "zone", "limit", "entry_date", "entry", "tp", "sl", "ret_pct"]].rename(
            columns={"ticker": "Activo", "status": "Estado", "zone": "Zona", "limit": "Límite", "entry_date": "Entrada",
                     "entry": "Precio entrada", "tp": "TP", "sl": "SL", "ret_pct": "Va %"})
        st.table(cur.style.format(precision=2, na_rep="—"))
    else:
        st.info("Ningún activo de la lista tiene una posición abierta ni una orden pendiente hoy.")

    st.markdown("**Últimas operaciones de la cartera**")
    if len(taken):
        last = taken.sort_values("entry_date", ascending=False).head(40)
        last = last[["ticker", "entry_date", "entry", "exit_date", "exit", "reason", "ret_pct", "peso_pct"]].rename(
            columns={"ticker": "Activo", "entry_date": "Entrada", "entry": "Precio entrada", "exit_date": "Salida",
                     "exit": "Precio salida", "reason": "Motivo", "ret_pct": "Resultado %", "peso_pct": "Peso en cartera %"})
        st.dataframe(last.round({"Precio entrada": 2, "Precio salida": 2, "Resultado %": 2, "Peso en cartera %": 1}),
                     width="stretch", height=320)
        st.download_button("Descargar operaciones de la cartera (CSV)", taken.to_csv(index=False).encode(),
                           "cartera_operaciones.csv", "text/csv")
    st.caption("Atención: la lista usa empresas que hoy son grandes y exitosas. En el pasado no siempre se sabía cuáles iban a "
               "serlo, así que los resultados históricos de la lista tienden a verse mejor de lo que habrían sido "
               "(sesgo de supervivencia). Afecta por igual a la estrategia y a “comprar y mantener la lista”.")

# ───────────────────────────── Ayuda ─────────────────────────────
with tab_help:
    st.markdown(f"""
**Qué simula.** Cada día, al cierre, revisa si el precio retrocedió hasta una zona de valor (SMA {P.sma_fast},
SMA {P.sma_slow} o el POC del último año) con **volumen bajo** (secado de oferta), dentro de una tendencia alcista
y fuera de la ventana de balances. Si se cumple todo, coloca una **orden límite** en ese nivel por {P.order_valid} velas.

**Cómo sale.** Take Profit **+{P.tp_pct:g} %**, Stop Loss **−{P.sl_pct:g} %** desde el límite, salida anticipada si cierra
por debajo del soporte con volumen alto, o al cumplirse {P.max_bars} velas.

**Supuestos conservadores.** No compra en la misma vela de la señal; si en una vela se tocan el TP y el SL,
cuenta como pérdida; respeta los gaps; descuenta {P.cost_pct:g} % por lado de costos.

**Cómo leer los números.**
- Con +{P.tp_pct:g} / −{P.sl_pct:g}, la estrategia empata con un acierto de ≈ {breakeven:.0f} %. Por encima, gana.
- *Profit factor* = ganancias totales ÷ pérdidas totales. Mayor que 1 es positivo; 1,3 o más ya es bueno.
- Compará siempre contra **comprar y mantener**: si la estrategia rinde menos, el esfuerzo no compensa.
- Un backtest no garantiza el futuro. Con pocas operaciones (menos de ~50) los números son poco confiables.

**Datos.** Precios diarios de Yahoo Finance ajustados por dividendos. Las fechas de balances también salen de Yahoo
y pueden estar incompletas en años viejos.
""")


# ───────────────────────────── Insights ─────────────────────────────
F_KEY, A_KEY = list(RULES)
DEF_UNI = tuple(t.strip() for t in UNIVERSE.split(","))
TODAY = pd.Timestamp.today().normalize()
_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def fdate(x, year=True) -> str:
    x = pd.Timestamp(x)
    return f"{_DIAS[x.weekday()]} {x:%d/%m}" + (f"/{x:%Y}" if year else "")


def upcoming_earnings(tickers) -> pd.DataFrame:
    rows = []
    for tk in tickers:
        try:
            _, er = load(tk, start)
        except Exception:  # noqa: BLE001
            continue
        nxt = dt.next_earnings(er, TODAY)
        if nxt is not None:
            rows.append({"Activo": tk, "Próximo balance": nxt, "En días": (nxt - TODAY).days,
                         "Pausa desde": nxt - pd.Timedelta(days=Params().er_before)})
    return pd.DataFrame(rows).sort_values("Próximo balance") if rows else pd.DataFrame(
        columns=["Activo", "Próximo balance", "En días", "Pausa desde"])


with tab_ins:
    st.subheader("💡 Insights")
    st.caption("Conclusiones calculadas con los datos actuales. Se recalculan solas cada vez que se actualizan los precios. "
               "Es un análisis de simulaciones, no una recomendación de inversión.")
    with st.spinner("Calculando insights…"):
        cmp_i = compare_variants(tuple(DEFAULT_TICKERS), start)
        refs_i, log_i, expo_i, _, cur_i = run_portfolio(DEF_UNI, start, F_KEY, 3, 4.0)
        refs_0 = run_portfolio(DEF_UNI, start, F_KEY, 3, 0.0)[0]

    def g(var, per, col):
        r = cmp_i[(cmp_i["Variante"].str.startswith(var)) & (cmp_i["Período"].str.startswith(per))]
        return float(r[col].iloc[0]) if len(r) else np.nan

    st_c = {k: pf.stats(v) for k, v in refs_i.items()}
    port_cagr, spy_cagr = st_c["Cartera mejorada"]["Anual %"], st_c["Comprar y mantener SPY"]["Anual %"]
    list_cagr = st_c["Comprar y mantener la lista (partes iguales)"]["Anual %"]
    orig_cagr = st_c["Caja original (5 activos, reglas A)"]["Anual %"]
    cash_part = port_cagr - pf.stats(refs_0["Cartera mejorada"])["Anual %"]
    ratio_p = st_c["Cartera mejorada"]["Anual / caída"]
    ratio_s = st_c["Comprar y mantener SPY"]["Anual / caída"]

    st.markdown("#### Highlights")
    cards = [
        ("🐢", "La estrategia original casi no opera",
         f"{g('A', 'Período', 'Ops/año'):.1f} operaciones por año entre 5 activos y {g('A', 'Período', 'Acierto %'):.0f}% de acierto "
         f"(necesita ≈ 43% para empatar). Rinde {fmt_pct(orig_cagr)} anual."),
        ("🧪", "Las reglas F mejoran y se sostienen",
         f"Profit factor {g('F', 'Diseño', 'Profit factor'):.2f} en 2012–2019 y {g('F', 'Validación', 'Profit factor'):.2f} "
         f"en 2020–hoy, con {g('F', 'Período', 'Acierto %'):.0f}% de acierto. La original fue "
         f"{g('A', 'Diseño', 'Profit factor'):.2f} y {g('A', 'Validación', 'Profit factor'):.2f}."),
        ("💼", "La cartera mejorada sí rinde",
         f"{fmt_pct(port_cagr)} anual con la caja completa trabajando, contra {fmt_pct(orig_cagr)} de la caja original. "
         f"Está invertida el {expo_i.mean() * 100:.0f}% del tiempo."),
        ("🏦", "Parte del rendimiento es la tasa",
         f"De ese {fmt_pct(port_cagr, sign=False)}, unos {cash_part:.1f} puntos vienen de la tasa del efectivo (4%). "
         f"Las operaciones aportan ≈ {port_cagr - cash_part:.1f}% anual."),
        ("📈", "Comprar y mantener sigue ganando",
         f"SPY rindió {fmt_pct(spy_cagr)} anual y la lista completa {fmt_pct(list_cagr)}. "
         f"Por cada punto de caída, la cartera gana {ratio_p:.2f} y SPY {ratio_s:.2f}."),
        ("🛡️", "Cae menos en las crisis",
         f"Peor caída de la cartera {fmt_pct(st_c['Cartera mejorada']['Peor caída %'])} contra "
         f"{fmt_pct(st_c['Comprar y mantener SPY']['Peor caída %'])} de SPY. Ver detalle por episodio abajo."),
    ]
    for i in range(0, len(cards), 3):
        cols = st.columns(3)
        for col, (icon, title, text) in zip(cols, cards[i:i + 3]):
            with col.container(border=True):
                st.markdown(f"**{icon} {title}**")
                st.write(text)

    st.markdown("#### Cómo le fue en los momentos difíciles")
    cr = ev.crisis_returns({"Cartera mejorada %": refs_i["Cartera mejorada"],
                            "SPY %": refs_i["Comprar y mantener SPY"],
                            "Caja original %": refs_i["Caja original (5 activos, reglas A)"]})
    st.table(cr.set_index("Episodio").style.format(precision=1, na_rep="—"))
    fig = go.Figure()
    for k, sty in (("Cartera mejorada", dict(width=2.6, color="#1f9d74")),
                   ("Comprar y mantener SPY", dict(width=1.4, color="#5b6b8c"))):
        v = refs_i[k]
        fig.add_scatter(x=v.index, y=(v - 1) * 100, name=k, line=sty)
    for name, a, b in ev.CRISES:
        fig.add_vrect(x0=a, x1=b, fillcolor="#ef5350", opacity=0.13, line_width=0,
                      annotation_text=name, annotation_position="top left", annotation_font_size=10,
                      annotation_textangle=-90)
    fig.update_layout(height=430, margin=dict(l=10, r=10, t=40, b=10), yaxis_title="Rendimiento acumulado (%)",
                      legend=dict(orientation="h", y=1.12), hovermode="x unified")
    st.plotly_chart(fig, width="stretch")
    worse = cr.dropna(subset=["Cartera mejorada %", "SPY %"])
    better_n = int((worse["Cartera mejorada %"] > worse["SPY %"]).sum())
    st.caption(f"En {better_n} de {len(worse)} episodios la cartera perdió menos que SPY. Ayuda que esté parte del tiempo "
               "en efectivo; la contracara es que también se pierde parte de las subas.")

    st.markdown("#### Recomendaciones para los próximos pasos")
    recs = []
    if g("F", "Validación", "Profit factor") > g("A", "Validación", "Profit factor") or \
            g("F", "Período", "Profit factor") > g("A", "Período", "Profit factor"):
        recs.append("**Usar las reglas F en lugar de las originales.** Fueron mejores en el período de diseño y se "
                    "sostuvieron en la validación; las originales operan tan poco que sus números no son confiables.")
    recs.append("**Usar la caja completa con pocas posiciones (pestaña Cartera mejorada).** Repartirla en partes fijas por "
                "activo deja casi toda la plata quieta.")
    if port_cagr < spy_cagr:
        recs.append(f"**No esperar que la caja táctica le gane al core.** En la simulación rindió {fmt_pct(port_cagr)} "
                    f"contra {fmt_pct(spy_cagr)} de SPY, y tampoco gana ajustando por riesgo. Si la usan, que sea un "
                    "porcentaje chico, como plantea Hugo, y con expectativas acordes.")
    recs.append("**Probar en papel antes de poner plata.** Activar las alertas de Telegram y anotar 2 o 3 meses de señales "
                "reales; recién ahí comparar con lo que dice la simulación.")
    recs.append("**Mirar el calendario antes de cada orden.** Además de los balances (que la estrategia ya evita), las "
                "decisiones de la Fed y los datos de inflación y empleo suelen mover todo el mercado el mismo día.")
    recs.append("**Tener presente el sesgo de supervivencia.** La lista usa empresas que hoy son grandes; el pasado real "
                "habría sido algo peor tanto para la estrategia como para comprar y mantener esa lista.")
    for i, r in enumerate(recs, 1):
        st.markdown(f"{i}. {r}")

    st.markdown("#### Para esta semana")
    macro = ev.macro_calendar()
    soon = macro[(macro["Fecha"] >= TODAY) & (macro["Fecha"] <= TODAY + pd.Timedelta(days=14))]
    held = sorted(set(cur_i["ticker"])) if len(cur_i) else []
    ue = upcoming_earnings(held)
    bullets = []
    if held:
        for _, r in cur_i.iterrows():
            if r["status"] == "abierta":
                bullets.append(f"Posición abierta en **{r['ticker']}** desde {r['entry_date']:%d/%m} "
                               f"(TP {r['tp']:.2f} · SL {r['sl']:.2f} · va {r['ret_pct']:+.1f}%).")
            else:
                bullets.append(f"Orden límite pendiente en **{r['ticker']}** a {r['limit']:.2f}.")
        for _, r in ue[ue["En días"] <= 21].iterrows():
            bullets.append(f"⚠️ **{r['Activo']}** presenta balance el {r['Próximo balance']:%d/%m} "
                           f"(en {r['En días']} días): riesgo de salto de precio con la posición abierta.")
    else:
        bullets.append("La cartera mejorada no tiene posiciones abiertas ni órdenes pendientes.")
    for _, r in soon.iterrows():
        bullets.append(f"📅 {fdate(r['Fecha'], False)}: {r['Evento']}.")
    if soon.empty:
        bullets.append("No hay eventos macro importantes en los próximos 14 días.")
    for b in bullets:
        st.markdown(f"- {b}")


# ───────────────────────────── Calendario ─────────────────────────────
with tab_cal:
    st.subheader("📅 Calendario")
    c1, c2 = st.columns([2, 1])
    which = c1.radio("Activos", ["Lista de la cartera mejorada", "Activos de la barra lateral"], horizontal=True)
    horizon = c2.slider("Próximos días", 14, 180, 60)
    cal_t = DEF_UNI if which.startswith("Lista") else tuple(results)
    end = TODAY + pd.Timedelta(days=horizon)

    with st.spinner("Buscando fechas de balances…"):
        ue = upcoming_earnings(cal_t)
    held = set(cur_i["ticker"]) if len(cur_i) else set()
    ue_h = ue[ue["Próximo balance"] <= end].copy()
    macro = ev.macro_calendar()
    mac_h = macro[(macro["Fecha"] >= TODAY) & (macro["Fecha"] <= end)]

    m = st.columns(4)
    m[0].metric("Balances en el período", len(ue_h))
    m[1].metric("Decisiones de la Fed", int((mac_h["Tipo"] == "Fed (FOMC)").sum()))
    m[2].metric("Datos de inflación y empleo", int(mac_h["Tipo"].isin(["Inflación (CPI)", "Empleo (NFP)"]).sum()))
    nxt = mac_h.iloc[0] if len(mac_h) else None
    m[3].metric("Próximo evento macro", f"{nxt['Fecha']:%d/%m}" if nxt is not None else "—",
                nxt["Tipo"] if nxt is not None else None, delta_color="off")

    tl = pd.concat([
        pd.DataFrame({"Fecha": ue_h["Próximo balance"], "Fila": "Balances",
                      "Texto": ue_h["Activo"] + np.where(ue_h["Activo"].isin(held), " ●", "")}),
        pd.DataFrame({"Fecha": mac_h["Fecha"], "Fila": mac_h["Tipo"], "Texto": mac_h["Evento"]}),
    ], ignore_index=True)
    if len(tl):
        colors = {"Balances": "#1f9d74", "Fed (FOMC)": "#c62828", "Inflación (CPI)": "#ef6c00",
                  "Empleo (NFP)": "#6a1b9a", "Mercado": "#78909c"}
        fig = go.Figure()
        for fila, grp in tl.groupby("Fila"):
            fig.add_scatter(x=grp["Fecha"], y=[fila] * len(grp), mode="markers+text", name=fila,
                            text=grp["Texto"] if fila == "Balances" else None, textposition="top center",
                            textfont=dict(size=9), hovertext=grp["Texto"], hoverinfo="text+x",
                            marker=dict(size=11, color=colors.get(fila, "#888"), symbol="diamond"))
        fig.add_vline(x=TODAY, line_dash="dot", line_color="#888")
        fig.update_layout(height=340, margin=dict(l=10, r=10, t=20, b=10), showlegend=False,
                          yaxis=dict(categoryorder="array",
                                     categoryarray=["Mercado", "Empleo (NFP)", "Inflación (CPI)", "Fed (FOMC)", "Balances"]))
        st.plotly_chart(fig, width="stretch")
        st.caption("● = activo con posición abierta u orden pendiente en la cartera mejorada.")

    a, b = st.columns(2)
    with a:
        st.markdown("**Balances trimestrales**")
        if len(ue_h):
            show = ue_h.assign(**{"Posición": ue_h["Activo"].isin(held).map({True: "● Sí", False: ""})})
            show["Próximo balance"] = show["Próximo balance"].map(fdate)
            show["Pausa desde"] = show["Pausa desde"].map(lambda x: fdate(x, False))
            st.table(show.set_index("Activo"))
        else:
            st.info("Sin balances en el período (los ETF como SPY o QQQ no presentan balances).")
        st.caption(f"La estrategia no abre posiciones desde {Params().er_before} días antes hasta "
                   f"{Params().er_after} días después de cada reporte. Fechas de Yahoo Finance; "
                   "algunas son estimadas hasta que la empresa las confirma.")
    with b:
        st.markdown("**Eventos que mueven todo el mercado**")
        if len(mac_h):
            st.table(mac_h.assign(Fecha=mac_h["Fecha"].map(fdate))[["Fecha", "Tipo", "Evento"]].set_index("Fecha"))
        st.caption(f"Fuentes oficiales: [Reserva Federal]({ev.FED_URL}) · [Inflación BLS]({ev.CPI_URL}) · "
                   f"[Empleo BLS]({ev.NFP_URL}) · [Feriados NYSE]({ev.NYSE_URL}). "
                   "Las fechas de la Fed de 2027 son tentativas.")


# ───────────────────────────── Estrategias mensuales ─────────────────────────────
ROT_START = "2005-01-01"   # historia extra para calcular medias y momentum antes del período evaluado


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def run_rotations(start_: str, cost: float):
    tks = sorted(set(sum((v["tickers"] for v in ro.STRATEGIES.values()), [])))
    closes = pd.concat({tk: load(tk, ROT_START)[0]["Close"] for tk in tks}, axis=1).sort_index().ffill()
    out = {}
    for name, spec in ro.STRATEGIES.items():
        sub = closes[spec["tickers"]].dropna()
        eq, tr, ex, W = ro.run(name, sub, cost, start_)
        out[name] = dict(eq=eq, trades=tr, expo=ex, W=W)
    return out, closes


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def port_real_cash(universe: tuple, start_: str, rules: str, max_pos: int):
    """Cartera mejorada con el efectivo rindiendo las letras del Tesoro reales (BIL) en vez de una tasa fija."""
    rp = RULES[rules]
    data, trades = {}, {}
    for tk in universe:
        try:
            df, er = load(tk, start_)
            d = compute_indicators(df, rp, er)
            data[tk], trades[tk] = d, backtest(d, rp, tk)
        except Exception:  # noqa: BLE001
            pass
    first = max(d.index[0] for d in data.values())
    bil = load(ro.CASH, ROT_START)[0]["Close"].pct_change()
    eq, log, expo = pf.simulate(data, trades, max_pos, 0.0, rp.cost_pct, start=first, cash_returns=bil)
    return eq, log, expo


with tab_rot:
    st.subheader("🐢 Estrategias mensuales: pocas operaciones por año")
    st.markdown("Se revisan **una vez por mes** (último día hábil) y casi siempre la respuesta es *no hacer nada*. "
                "Son reglas públicas y conocidas, no ajustadas por nosotros a estos datos.")
    with st.spinner("Simulando estrategias mensuales (la primera vez tarda un poco)…"):
        rot, rcl = run_rotations(start, P.cost_pct)
        peq, plog, pexpo = port_real_cash(DEF_UNI, start, F_KEY, 3)

    eval_start = max([pd.Timestamp(start) + pd.DateOffset(years=1)] + [r["eq"].index[0] for r in rot.values()])
    series = {k: r["eq"] for k, r in rot.items()}
    series["Cartera mejorada (efectivo a tasa real)"] = peq
    series["Comprar y mantener SPY"] = rcl["SPY"]
    series = {k: (v[v.index >= eval_start] / v[v.index >= eval_start].iloc[0]) for k, v in series.items()}
    yrs = (series["Comprar y mantener SPY"].index[-1] - eval_start).days / 365.25

    def ops_year(k):
        if k in rot:
            t = rot[k]["trades"]
            return (t["Entrada"] >= eval_start).sum() / yrs if len(t) else 0.0
        if k.startswith("Cartera"):
            tk_ = plog[plog["tomada"]] if len(plog) else plog
            return (tk_["entry_date"] >= eval_start).sum() / yrs if len(tk_) else 0.0
        return 0.0

    def expo_avg(k):
        if k in rot:
            return rot[k]["expo"][rot[k]["expo"].index >= eval_start].mean() * 100
        if k.startswith("Cartera"):
            return pexpo[pexpo.index >= eval_start].mean() * 100
        return 100.0

    rows = []
    for k, v in series.items():
        stt = pf.stats(v)
        rows.append({"Estrategia": k, "Operaciones por año": ops_year(k), "Anual %": stt["Anual %"],
                     "Peor caída %": stt["Peor caída %"], "Anual / caída": stt["Anual / caída"],
                     "Volatilidad %": stt["Volatilidad %"], "Tiempo en acciones %": expo_avg(k)})
    tab = pd.DataFrame(rows).set_index("Estrategia")

    best = tab.drop(index="Comprar y mantener SPY")["Anual / caída"].idxmax()
    cm = tab.loc["Cartera mejorada (efectivo a tasa real)"]
    cols = st.columns(len(ro.STRATEGIES))
    for col, name in zip(cols, ro.STRATEGIES):
        r = tab.loc[name]
        col.metric(name, fmt_pct(r["Anual %"]) + " anual",
                   f"{r['Operaciones por año']:.1f} operaciones/año · caída {r['Peor caída %']:.0f}%", delta_color="off")
    st.caption(f"Desde {eval_start:%m/%Y}. Referencias: cartera mejorada {fmt_pct(cm['Anual %'])} anual con "
               f"{cm['Operaciones por año']:.0f} operaciones/año · comprar y mantener SPY "
               f"{fmt_pct(tab.loc['Comprar y mantener SPY', 'Anual %'])} sin operar. "
               f"Mejor relación ganancia/caída: **{best}**.")

    st.table(tab.style.format(precision=1, na_rep="—").format({"Anual / caída": "{:.2f}"}))
    st.caption("Operación = comprar un activo y después venderlo (mover la plata a letras del Tesoro no cuenta). "
               "“Anual / caída” = rentabilidad anual dividida por la peor caída: más alto es mejor.")

    fig = go.Figure()
    palette = ["#1f9d74", "#2962FF", "#AB47BC", "#e8a33d", "#5b6b8c"]
    for (k, v), c in zip(series.items(), palette):
        fig.add_scatter(x=v.index, y=(v - 1) * 100, name=k,
                        line=dict(width=1.5 if k.startswith(("Comprar", "Cartera")) else 2.4, color=c,
                                  dash="dot" if k.startswith("Comprar") else None))
    fig.update_layout(height=430, margin=dict(l=10, r=10, t=30, b=10), yaxis_title="Rendimiento acumulado (%)",
                      legend=dict(orientation="h", y=1.12), hovermode="x unified")
    st.plotly_chart(fig, width="stretch")

    st.markdown("**¿Se sostiene en distintas épocas?**")
    rows = []
    for per, a, b in ((f"{eval_start:%Y}–2019", None, SPLIT), ("2020 a hoy", SPLIT, None)):
        for k, v in series.items():
            stt = pf.stats(v, a, b)
            rows.append({"Período": per, "Estrategia": k, "Anual %": stt.get("Anual %"),
                         "Peor caída %": stt.get("Peor caída %"), "Anual / caída": stt.get("Anual / caída")})
    st.table(pd.DataFrame(rows).set_index(["Período", "Estrategia"]).style.format(precision=1, na_rep="—")
             .format({"Anual / caída": "{:.2f}"}))

    st.markdown("**En los momentos difíciles**")
    cr = ev.crisis_returns({f"{k} %": v for k, v in series.items()})
    cr = cr[pd.to_datetime(cr["Desde"]) >= eval_start]
    st.table(cr.set_index("Episodio").style.format(precision=1, na_rep="—"))

    st.markdown("**Qué tendría cada una hoy**")
    nxt = ro.next_rebalance(TODAY)
    rows = []
    for name, r in rot.items():
        W = r["W"]
        last_w = W.iloc[-1]
        changes = W.ne(W.shift()).any(axis=1)
        since = W.index[changes][-1] if changes.any() else W.index[0]
        rows.append({"Estrategia": name, "Posición": ro.describe(last_w), "Decidida al cierre del": fdate(W.index[-1]),
                     "Sin cambios desde": fdate(since), "Próxima revisión": fdate(nxt)})
    st.table(pd.DataFrame(rows).set_index("Estrategia"))
    st.caption("La próxima revisión es el último día hábil del mes; la orden se ejecuta al día siguiente. "
               "Si la posición no cambia, no hay que hacer nada.")

    with st.expander("Reglas de cada estrategia"):
        for name, spec in ro.STRATEGIES.items():
            st.markdown(f"**{name}** ({spec['origen']}). {spec['regla']}")
        st.markdown("El efectivo rinde lo que rindieron de verdad las letras del Tesoro de EE.UU. (ETF BIL): casi 0 % "
                    "hasta 2021 y alrededor de 4–5 % desde 2023. Por eso la cartera mejorada de esta pestaña rinde menos "
                    "que en su propia pestaña, donde la tasa es fija y editable. Costos: "
                    f"{P.cost_pct:g} % por cada compra o venta.")
