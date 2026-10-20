"""
Escaneo diario + notificaciones al celular.

Corre después del cierre de Wall Street (GitHub Actions, ver .github/workflows/escaneo.yml)
y manda UN mensaje con dos secciones:

  💼 Cartera mejorada  — reglas F, lista amplia, hasta 3 posiciones (como la pestaña del sitio)
  📈 Estrategia original — reglas de Hugo sobre SPY, QQQ, JPM, GOOGL y KO

más los balances y eventos macro de los próximos días.

Canales (se usan los que estén configurados):
  NTFY_TOPIC        canal de ntfy (app gratuita). Recomendado.
  TELEGRAM_TOKEN + TELEGRAM_CHAT_ID   opcional.

Otras variables:
  SOLO_NOVEDADES  "1" para no avisar los días sin novedades

Uso manual:
  python scan.py            # escanea y envía
  python scan.py --prueba   # manda un mensaje de prueba
  python scan.py --seco     # escanea e imprime, sin enviar
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

import pandas as pd
import requests

import data as dt
import events as ev
import portfolio as pf
from strategy import Params, compute_indicators, backtest, latest_status

SITE = "https://caja-tactica-david.streamlit.app"
START = "2015-01-01"

ORIG_TICKERS = ["SPY", "QQQ", "JPM", "GOOGL", "KO"]
ORIG_RULES = Params()

PORT_UNIVERSE = ["SPY", "QQQ", "DIA", "IWM", "XLF", "XLK", "XLV", "XLP", "XLE", "XLI", "XLU", "AAPL", "MSFT",
                 "GOOGL", "AMZN", "JPM", "V", "MA", "JNJ", "PG", "KO", "PEP", "WMT", "HD", "UNH", "XOM", "COST", "MCD"]
PORT_RULES = replace(Params(), dry_mult=0.85, tech_exit=False, tp_pct=6.0, sl_pct=4.0)
PORT_MAX_POS = 3
PORT_CASH_RATE = 4.0

_DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def fdate(x) -> str:
    x = pd.Timestamp(x)
    return f"{_DIAS[x.weekday()]} {x:%d/%m}"


# ───────────────────────────── Envío ─────────────────────────────
def send(title: str, text: str, urgent: bool = False) -> list[str]:
    sent = []
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic:
        r = requests.post(f"https://ntfy.sh/{topic}", data=text.encode("utf-8"), timeout=30, headers={
            "Title": title.encode("utf-8"),
            "Priority": "high" if urgent else "default",
            "Tags": "chart_with_upwards_trend",
            "Click": SITE,
        })
        r.raise_for_status()
        sent.append("ntfy")
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                          data={"chat_id": chat, "text": f"{title}\n\n{text}", "disable_web_page_preview": "true"})
        r.raise_for_status()
        sent.append("telegram")
    if not sent:
        print("⚠️  No hay canal configurado (NTFY_TOPIC o TELEGRAM_*). Muestro el mensaje:\n")
        print(title + "\n\n" + text)
    return sent


# ───────────────────────────── Datos ─────────────────────────────
_cache: dict = {}


def load(tk: str):
    if tk not in _cache:
        _cache[tk] = (dt.get_prices(tk, START), dt.get_earnings_dates(tk))
    return _cache[tk]


# ───────────────────────────── Secciones ─────────────────────────────
def section_portfolio() -> tuple[list[str], list[str], set, pd.Timestamp | None]:
    """Devuelve (novedades, estado, activos en cartera, fecha)."""
    data, trades, fails = {}, {}, []
    for tk in PORT_UNIVERSE:
        try:
            df, er = load(tk)
            d = compute_indicators(df, PORT_RULES, er)
            data[tk], trades[tk] = d, backtest(d, PORT_RULES, tk)
        except Exception:  # noqa: BLE001
            fails.append(tk)
    if not data:
        return ["No se pudieron descargar datos."], [], set(), None
    first = max(d.index[0] for d in data.values())
    eq, log, _ = pf.simulate(data, trades, PORT_MAX_POS, PORT_CASH_RATE, PORT_RULES.cost_pct, start=first)
    date = eq.index[-1]
    taken = log[log["tomada"]] if len(log) else log
    held = taken[taken["status"] == "abierta"] if len(taken) else taken

    news, status = [], []
    # Salidas de hoy
    if len(taken):
        for _, r in taken[(taken["status"] == "cerrada") & (taken["exit_date"] == date)].iterrows():
            icon = "🎯" if r["reason"].startswith("TP") else "🛑" if r["reason"].startswith("SL") else "⏱️"
            news.append(f"{icon} {r['ticker']}: salió por {r['reason']} a {r['exit']:.2f} ({r['ret_pct']:+.1f}%)")
        for _, r in taken[taken["entry_date"] == date].iterrows():
            news.append(f"✅ {r['ticker']}: entró a {r['entry']:.2f} · TP {r['tp']:.2f} · SL {r['sl']:.2f}")
    # Señales nuevas (órdenes límite para mañana)
    free = PORT_MAX_POS - len(held)
    held_set = set(held["ticker"]) if len(held) else set()
    signals = []
    for tk, tr in trades.items():
        p = tr[(tr["status"] == "orden pendiente") & (tr["signal_date"] == date)]
        if len(p) and tk not in held_set:
            signals.append(p.iloc[-1])
    for i, r in enumerate(sorted(signals, key=lambda x: x["ticker"])):
        if i < free:
            news.append(f"⚠️ {r['ticker']}: colocar orden límite a {r['limit']:.2f} ({r['zone']}) · "
                        f"TP {r['limit'] * (1 + PORT_RULES.tp_pct / 100):.2f} · SL {r['limit'] * (1 - PORT_RULES.sl_pct / 100):.2f} · válida {PORT_RULES.order_valid} ruedas")
        else:
            news.append(f"ℹ️ {r['ticker']}: señal en {r['zone']}, pero la cartera está llena")
    # Estado
    for _, r in held.iterrows():
        px = data[r["ticker"]]["Close"].iloc[-1]
        ret = (px / r["entry"] - 1) * 100
        status.append(f"• {r['ticker']} desde {r['entry_date']:%d/%m} · va {ret:+.1f}% · TP {r['tp']:.2f} / SL {r['sl']:.2f}")
    status.append(f"• Lugares libres: {free} de {PORT_MAX_POS}")
    if fails:
        status.append(f"• Sin datos hoy: {', '.join(fails)}")
    return news, status, held_set, date


def section_original() -> tuple[list[str], list[str]]:
    news, status = [], []
    for tk in ORIG_TICKERS:
        try:
            df, er = load(tk)
            d = compute_indicators(df, ORIG_RULES, er)
            tr = backtest(d, ORIG_RULES, tk)
            s = latest_status(d, tr, ORIG_RULES)
        except Exception as e:  # noqa: BLE001
            status.append(f"• {tk}: error al descargar ({str(e)[:60]})")
            continue
        o, pos = s["orden"], s["posicion"]
        if "señal" in s["eventos"] and o:
            news.append(f"⚠️ [SWING INSTITUCIONAL] {tk}: Precio en Zona de Entrada Límite - Activar Orden · "
                        f"límite {o['limit']:.2f} ({o['zone']}) · TP {o['tp']:.2f} · SL {o['sl']:.2f}")
        if "entrada ejecutada" in s["eventos"] and pos:
            news.append(f"✅ {tk}: entró a {pos['entry']:.2f} · TP {pos['tp']:.2f} · SL {pos['sl']:.2f}")
        if "TP" in s["eventos"]:
            news.append(f"🎯 [SWING] {tk}: Take Profit alcanzado (+{ORIG_RULES.tp_pct:g}%)")
        if "SL" in s["eventos"]:
            news.append(f"🛑 [SWING] {tk}: Stop Loss Técnico Activado (-{ORIG_RULES.sl_pct:g}%)")
        if "salida por tiempo" in s["eventos"]:
            news.append(f"⏱️ {tk}: operación cerrada por tiempo")
        extra = ""
        if pos:
            extra = f" · va {pos['ret_pct']:+.1f}%"
        elif pd.notna(s["distancia_pct"]) and s["nivel_cercano"]:
            extra = f" · {s['distancia_pct']:+.1f}% sobre {s['nivel_cercano']}"
        status.append(f"• {tk} {s['cierre']:.2f} — {s['estado']}{extra}")
    return news, status


def section_calendar(held: set, today: pd.Timestamp) -> list[str]:
    lines = []
    for tk in sorted(held):
        nxt = dt.next_earnings(load(tk)[1], today)
        if nxt is not None and (nxt - today).days <= 10:
            lines.append(f"⚠️ {tk} (en cartera) presenta balance el {fdate(nxt)}")
    macro = ev.macro_calendar()
    soon = macro[(macro["Fecha"] > today) & (macro["Fecha"] <= today + pd.Timedelta(days=7))]
    for _, r in soon.iterrows():
        lines.append(f"📅 {fdate(r['Fecha'])}: {r['Evento']}")
    return lines


# ───────────────────────────── Principal ─────────────────────────────
def build() -> tuple[str, str, bool]:
    p_news, p_status, held, date = section_portfolio()
    o_news, o_status = section_original()
    today = (date or pd.Timestamp.today()).normalize()
    cal = section_calendar(held, today)
    has_news = bool(p_news or o_news)

    parts = ["💼 CARTERA MEJORADA"]
    parts += p_news or ["Sin novedades."]
    parts += p_status
    parts += ["", "📈 ESTRATEGIA ORIGINAL"]
    parts += o_news or ["Sin novedades."]
    parts += o_status
    if cal:
        parts += ["", "🗓️ PRÓXIMOS DÍAS"] + cal
    title = f"Caja Táctica · cierre {today:%d/%m}" + (" · ¡HAY NOVEDADES!" if has_news else "")
    return title, "\n".join(parts), has_news


if __name__ == "__main__":
    if "--prueba" in sys.argv:
        print("Enviado por:", send("Caja Táctica conectada ✅",
                                   "Desde ahora vas a recibir acá el resumen diario después del cierre de Wall Street."))
        sys.exit(0)
    title, text, has_news = build()
    if "--seco" in sys.argv:
        print(title + "\n\n" + text)
    elif has_news or os.environ.get("SOLO_NOVEDADES") != "1":
        print("Enviado por:", send(title, text, urgent=has_news))
        print(title + "\n\n" + text)
    else:
        print("Sin novedades; no se envió nada.\n\n" + title + "\n\n" + text)
