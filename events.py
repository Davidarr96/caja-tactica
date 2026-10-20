"""
Eventos que mueven el mercado.

Fuentes oficiales (verificadas el 20/10/2026):
  - Reuniones de la Fed (FOMC): https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
    (las fechas de 2027 son tentativas hasta que se confirman en la reunión anterior)
  - Inflación (CPI):            https://www.bls.gov/schedule/news_release/cpi.htm
  - Empleo (Employment Situation / NFP): https://www.bls.gov/schedule/news_release/empSit.htm
  - Feriados NYSE:              comunicado de NYSE Group, 23/12/2025

Para agregar fechas nuevas, sumar filas a las listas de abajo.
"""
from __future__ import annotations

import pandas as pd

FED_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
CPI_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"
NFP_URL = "https://www.bls.gov/schedule/news_release/empSit.htm"
NYSE_URL = "https://ir.theice.com/press/news-details/2025/NYSE-Group-Announces-2026-2027-and-2028-Holiday-and-Early-Closings-Calendar/"

# Fecha = día del anuncio (segundo día de la reunión). SEP = proyecciones económicas + conferencia más seguida.
_FOMC = [
    ("2026-10-28", False), ("2026-12-09", True),
    ("2027-01-27", False), ("2027-03-17", True), ("2027-04-28", False), ("2027-06-09", True),
    ("2027-07-28", False), ("2027-09-15", True), ("2027-10-27", False), ("2027-12-08", True),
]
_CPI = [  # (fecha de publicación, mes de referencia)
    ("2026-11-10", "oct-2026"), ("2026-12-10", "nov-2026"), ("2027-01-13", "dic-2026"), ("2027-02-11", "ene-2027"),
    ("2027-03-10", "feb-2027"), ("2027-04-13", "mar-2027"), ("2027-05-12", "abr-2027"), ("2027-06-10", "may-2027"),
    ("2027-07-14", "jun-2027"), ("2027-08-11", "jul-2027"), ("2027-09-14", "ago-2027"), ("2027-10-14", "sep-2027"),
    ("2027-11-10", "oct-2027"), ("2027-12-10", "nov-2027"),
]
_NFP = [
    ("2026-11-06", "oct-2026"), ("2026-12-04", "nov-2026"), ("2027-01-08", "dic-2026"), ("2027-02-05", "ene-2027"),
    ("2027-03-05", "feb-2027"), ("2027-04-02", "mar-2027"), ("2027-05-07", "abr-2027"), ("2027-06-04", "may-2027"),
    ("2027-07-02", "jun-2027"), ("2027-08-06", "jul-2027"), ("2027-09-03", "ago-2027"), ("2027-10-08", "sep-2027"),
    ("2027-11-05", "oct-2027"), ("2027-12-03", "nov-2027"),
]
_HOLIDAYS = [
    ("2026-11-26", "Feriado: Acción de Gracias (mercado cerrado)"),
    ("2026-11-27", "Cierre anticipado 13:00 ET (día después de Acción de Gracias)"),
    ("2026-12-24", "Cierre anticipado 13:00 ET (Nochebuena)"),
    ("2026-12-25", "Feriado: Navidad (mercado cerrado)"),
    ("2027-01-01", "Feriado: Año Nuevo"), ("2027-01-18", "Feriado: Martin Luther King Jr."),
    ("2027-02-15", "Feriado: Día de los Presidentes"), ("2027-03-26", "Feriado: Viernes Santo"),
    ("2027-05-31", "Feriado: Memorial Day"), ("2027-06-18", "Feriado: Juneteenth (observado)"),
    ("2027-07-05", "Feriado: Día de la Independencia (observado)"), ("2027-09-06", "Feriado: Labor Day"),
    ("2027-11-25", "Feriado: Acción de Gracias"), ("2027-11-26", "Cierre anticipado 13:00 ET"),
    ("2027-12-24", "Feriado: Navidad (observado)"),
]


def macro_calendar() -> pd.DataFrame:
    rows = []
    for d, sep in _FOMC:
        rows.append({"Fecha": d, "Tipo": "Fed (FOMC)", "Impacto": "Alto",
                     "Evento": "Decisión de tasas de la Fed, 14:00 ET" + (" + proyecciones económicas" if sep else ""),
                     "Fuente": FED_URL})
    for d, ref in _CPI:
        rows.append({"Fecha": d, "Tipo": "Inflación (CPI)", "Impacto": "Alto",
                     "Evento": f"Inflación de EE.UU. ({ref}), 8:30 ET", "Fuente": CPI_URL})
    for d, ref in _NFP:
        rows.append({"Fecha": d, "Tipo": "Empleo (NFP)", "Impacto": "Alto",
                     "Evento": f"Informe de empleo de EE.UU. ({ref}), 8:30 ET", "Fuente": NFP_URL})
    for d, txt in _HOLIDAYS:
        rows.append({"Fecha": d, "Tipo": "Mercado", "Impacto": "—", "Evento": txt, "Fuente": NYSE_URL})
    df = pd.DataFrame(rows)
    df["Fecha"] = pd.to_datetime(df["Fecha"])
    return df.sort_values("Fecha").reset_index(drop=True)


# Episodios de estrés del mercado (pico → piso aproximado del S&P 500)
CRISES = [
    ("Devaluación china", "2015-08-17", "2015-08-25"),
    ("Brexit", "2016-06-23", "2016-06-27"),
    ("Volmageddon", "2018-01-26", "2018-02-08"),
    ("Caída de fin de 2018", "2018-09-20", "2018-12-24"),
    ("COVID", "2020-02-19", "2020-03-23"),
    ("Bajista 2022 (suba de tasas)", "2022-01-03", "2022-10-12"),
    ("Quiebra de SVB", "2023-03-08", "2023-03-13"),
    ("Desarme del carry del yen", "2024-07-16", "2024-08-05"),
    ("Aranceles de abril 2025", "2025-04-02", "2025-04-08"),
]


def crisis_returns(series: dict) -> pd.DataFrame:
    """Rendimiento de cada serie (base cualquiera) entre el pico y el piso de cada crisis."""
    rows = []
    for name, a, b in CRISES:
        row = {"Episodio": name, "Desde": a, "Hasta": b}
        for k, s in series.items():
            s = s.dropna()
            pa, pb = s[s.index <= pd.Timestamp(a)], s[s.index <= pd.Timestamp(b)]
            row[k] = (pb.iloc[-1] / pa.iloc[-1] - 1) * 100 if len(pa) and len(pb) else None
        rows.append(row)
    return pd.DataFrame(rows)
