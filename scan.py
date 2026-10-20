"""
Escaneo diario + alertas por Telegram.

Corre una vez por día después del cierre de Wall Street (lo programa GitHub Actions,
ver .github/workflows/escaneo.yml) y avisa:
  🟢 nueva señal → colocar orden límite
  ✅ orden ejecutada / 🎯 Take Profit / 🛑 Stop Loss
  y un resumen corto del estado de cada activo.

Variables de entorno:
  TELEGRAM_TOKEN    token del bot (de @BotFather)
  TELEGRAM_CHAT_ID  tu chat id
  TICKERS           opcional, ej. "SPY,QQQ,JPM,GOOGL,KO"
  SOLO_NOVEDADES    "1" para no mandar nada los días sin novedades

Uso manual:
  python scan.py            # escanea y envía
  python scan.py --prueba   # manda un mensaje de prueba al bot
  python scan.py --seco     # escanea e imprime, sin enviar
"""
from __future__ import annotations

import html
import os
import sys

import pandas as pd
import requests

import data as dt
from strategy import Params, compute_indicators, backtest, latest_status

TICKERS = [t.strip().upper() for t in os.environ.get("TICKERS", "SPY,QQQ,JPM,GOOGL,KO").split(",") if t.strip()]


def send_telegram(text: str) -> None:
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("⚠️  Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID; muestro el mensaje en pantalla:\n")
        print(text)
        return
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": "true"}, timeout=30)
    r.raise_for_status()


def scan(p: Params) -> tuple[str, bool]:
    alerts, lines = [], []
    fecha = None
    for tk in TICKERS:
        try:
            df = dt.get_prices(tk, "2015-01-01")
            er = dt.get_earnings_dates(tk)
            d = compute_indicators(df, p, er)
            tr = backtest(d, p, tk)
            s = latest_status(d, tr, p)
        except Exception as e:  # noqa: BLE001
            lines.append(f"• <b>{tk}</b>: error al descargar ({html.escape(str(e))[:80]})")
            continue
        fecha = s["fecha"]
        o, pos = s["orden"], s["posicion"]
        if "señal" in s["eventos"] and o:
            alerts.append(f"⚠️ <b>[SWING INSTITUCIONAL] {tk}</b> — Precio en Zona de Entrada Límite - Activar Orden\n"
                          f"   Zona: {o['zone']} · Límite <b>{o['limit']:.2f}</b> · TP {o['tp']:.2f} · SL {o['sl']:.2f}\n"
                          f"   Orden válida {p.order_valid} ruedas.")
        if "entrada ejecutada" in s["eventos"] and pos:
            alerts.append(f"✅ <b>{tk}</b>: orden ejecutada a {pos['entry']:.2f} (TP {pos['tp']:.2f} / SL {pos['sl']:.2f})")
        if "TP" in s["eventos"]:
            alerts.append(f"🎯 <b>[SWING] {tk}</b>: Take Profit alcanzado (+{p.tp_pct:g}%)")
        if "SL" in s["eventos"]:
            alerts.append(f"🛑 <b>[SWING] {tk}</b>: Stop Loss Técnico Activado (-{p.sl_pct:g}%)")
        if "salida por tiempo" in s["eventos"]:
            alerts.append(f"⏱️ <b>{tk}</b>: operación cerrada por tiempo")

        extra = ""
        if pos:
            extra = f" · va {pos['ret_pct']:+.1f}%"
        elif pd.notna(s["distancia_pct"]) and s["nivel_cercano"]:
            extra = f" · {s['distancia_pct']:+.1f}% sobre {s['nivel_cercano']}"
        lines.append(f"• <b>{tk}</b> {s['cierre']:.2f} — {s['estado']}{extra}")

    head = f"📊 <b>Caja Táctica</b> · cierre {fecha:%d/%m/%Y}" if fecha is not None else "📊 <b>Caja Táctica</b>"
    body = "\n\n".join(alerts) if alerts else "Sin novedades hoy."
    return f"{head}\n\n{body}\n\n<i>Estado</i>\n" + "\n".join(lines), bool(alerts)


if __name__ == "__main__":
    if "--prueba" in sys.argv:
        send_telegram("✅ Bot de la Caja Táctica conectado. Vas a recibir las alertas acá.")
        sys.exit(0)
    msg, has_news = scan(Params())
    if "--seco" in sys.argv:
        print(msg)
    elif has_news or os.environ.get("SOLO_NOVEDADES") != "1":
        send_telegram(msg)
        print("Enviado.")
    else:
        print("Sin novedades; no se envió nada.\n\n" + msg)
