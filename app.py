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
from strategy import Params, compute_indicators, backtest, summarize, equity_curve, latest_status, max_drawdown

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
if not results:
    st.stop()

tab_sim, tab_mon, tab_chart, tab_help = st.tabs(["📊 Simulación", "📡 Monitor de hoy", "🕯️ Gráfico", "📘 Cómo funciona"])

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
