# Caja Táctica · Simulador, monitor y alertas

Sitio web propio para la estrategia de swing institucional: zonas de valor (SMA 50, SMA 200 y POC anual), secado de volumen, pausa por balances, TP +4 % / SL −3 %.

## Qué incluye

| Archivo | Qué hace |
|---|---|
| `app.py` | El sitio web, con cuatro pestañas: **Simulación** (backtest con resultados y comparación contra comprar y mantener), **Monitor de hoy**, **Gráfico** y **Cómo funciona**. |
| `strategy.py` | La lógica de la estrategia, igual a la del indicador de TradingView. |
| `data.py` | Descarga de precios y fechas de balances desde Yahoo Finance. |
| `scan.py` | Escaneo diario. Manda las alertas por ntfy y/o Telegram. |
| `.github/workflows/escaneo.yml` | Corre `scan.py` solo, de lunes a viernes a las 18:40 hora de Argentina. |

Cuesta $0. Los datos salen de Yahoo Finance, el sitio corre en Streamlit Cloud, las alertas en GitHub Actions y los mensajes llegan por ntfy (o Telegram).

---

## Paso 1 · Probarlo en tu computadora

1. **Instalá Python.**
   - Bajalo de **python.org/downloads**.
   - En Windows, durante la instalación, tildá **"Add Python to PATH"**.
2. **Descomprimí** `caja-tactica.zip` en una carpeta, por ejemplo en Documentos.
3. **Abrí una terminal en esa carpeta.**
   - Windows: abrí la carpeta, escribí `cmd` en la barra de direcciones y apretá Enter.
   - Mac: clic derecho sobre la carpeta → *Nuevo terminal en la carpeta*.
4. **Instalá las dependencias.** Se hace una sola vez:
   ```
   pip install -r requirements.txt
   ```
5. **Arrancá el sitio:**
   ```
   streamlit run app.py
   ```
   Se abre solo en el navegador, en `http://localhost:8501`. Para cerrarlo, apretá `Ctrl + C` en la terminal.

En la barra de la izquierda podés cambiar los activos, el TP, el SL y las reglas. La simulación se recalcula al instante.

## Paso 2 · Publicarlo en internet, gratis

1. **Creá una cuenta en github.com.**
   - Creá un repositorio nuevo y **privado**. Por ejemplo, `caja-tactica`.
   - Subí todos los archivos de la carpeta con *Add file → Upload files*.
   - Incluí la carpeta `.github`. Si no se sube al arrastrar, creá el archivo a mano: *Add file → Create new file*, con el nombre `.github/workflows/escaneo.yml`, y pegá su contenido.
2. **Publicá el sitio.**
   - Entrá a **share.streamlit.io** con tu cuenta de GitHub.
   - Elegí *Create app*, el repositorio y el archivo `app.py`, y dale a *Deploy*.
   - Te da un link propio. En *Settings → Sharing* podés limitar quién lo ve, por ejemplo vos y Hugo.

## Paso 3 · Alertas al celular (ntfy)

Todos los días hábiles a las 18:40 hora de Argentina, GitHub corre `scan.py` y manda un resumen con dos secciones:
**💼 Cartera mejorada** (reglas F, 28 activos, hasta 3 posiciones) y **📈 Estrategia original** (reglas de Hugo sobre 5 activos),
más los balances y eventos macro de los próximos días. Si hay señales o salidas, llega con prioridad alta.

1. Instalá la app **ntfy** (gratis, Android y iPhone).
2. Tocá **+** (Suscribirse a un tema) y escribí el nombre del canal que figura en
   `.github/workflows/escaneo.yml` (línea `NTFY_TOPIC`). No compartas ese nombre: quien lo sepa puede leer las alertas.
3. Para probar: en GitHub, pestaña *Actions* → **Escaneo diario Caja Táctica** → *Run workflow* → modo `prueba`.
   Con modo `resumen` manda el resumen completo del día.

Para recibir solo los días con novedades: *Settings → Secrets and variables → Actions → Variables* → crear `SOLO_NOVEDADES` = `1`.

Opcional: también puede mandar por Telegram cargando los secretos `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`
(bot creado con @BotFather; tu id lo da @userinfobot).

---

## Cómo simula (para no engañarse)

- **Señales:** se calculan con la vela del día **ya cerrada**. La orden límite queda para las ruedas siguientes, 3 por defecto. No compra en la misma vela que dio la señal.
- **TP y SL en el mismo día:** cuenta como **pérdida**.
- **Gaps:** se respetan. Si abre por debajo del stop, sale a la apertura.
- **Costos:** descuenta 0,05 % por lado por comisión y deslizamiento. Se puede cambiar en la barra lateral.
- **Tasa de acierto de empate:** con +4 / −3 se empata con ≈ 43 % de aciertos. Por debajo de eso, la estrategia pierde.
- **Comparación:** compará siempre contra **comprar y mantener** los mismos activos. Esa es la vara.
- **Balances:** las fechas viejas pueden faltar en Yahoo, así que en años antiguos la pausa puede no aplicarse.
- **Validez:** un backtest no garantiza resultados futuros. Antes de usar dinero real conviene operar en papel unos meses.

## Problemas comunes

- **"No se encontraron datos":** el ticker está mal escrito, o Yahoo limitó las descargas por un rato. Esperá unos minutos.
- **`pip` no se reconoce:** Python no quedó en el PATH. Reinstalalo tildando esa opción, o usá `python -m pip install -r requirements.txt`.
- **El escaneo de GitHub falla de vez en cuando:** Yahoo a veces bloquea servidores. El día siguiente suele andar. Si pasa seguido, se puede cambiar a una API con clave gratuita, como Alpaca o Twelve Data.
