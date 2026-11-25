"""Acceso a datos del dashboard: solo lectura, sobre las vistas de db/03_views.sql.

Cada consulta se guarda en caché 5 minutos (st.cache_data): navegar entre
páginas no vuelve a consultar la base, y los datos nuevos (que llegan cada
hora) aparecen solos. La sesión se abre en modo de solo lectura, así que
el dashboard no puede modificar nada aunque tuviera un error.
"""

import pandas as pd
import psycopg
import streamlit as st

from common.jobs import pg_params

TTL_SECONDS = 300


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Corre una consulta y regresa un DataFrame (los numeric de PostgreSQL como float)."""
    with psycopg.connect(**pg_params(), connect_timeout=10,
                         options="-c default_transaction_read_only=on") as conn:
        cur = conn.execute(sql, params)
        df = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    for column in df.columns:
        if df[column].dtype == object and len(df) and df[column].map(
                lambda v: v.__class__.__name__ == "Decimal").any():
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def weeks() -> pd.DataFrame:
    return query("SELECT week_key, week_end, recency FROM v_chart_week ORDER BY week_key DESC")


def scalar(sql: str, params: tuple = ()):
    df = query(sql, params)
    return None if df.empty else df.iloc[0, 0]
