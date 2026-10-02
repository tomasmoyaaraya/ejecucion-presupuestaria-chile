"""Fase 2a - Une presupuesto y ejecución en una base SQLite (presupuesto.db) y crea las vistas de sql/vistas.sql.

Uso:  python modelo.py

Definiciones (también en el README):
  - Institución = capítulo de la Ley de Presupuestos (un servicio público), identificado por partida-capítulo.
  - Gasto = subtítulos 21 a 24, 26, 29, 31 y 33 (personal, bienes y servicios, prestaciones, transferencias,
    otros corrientes, activos no financieros, inversión y transferencias de capital). Se excluyen las operaciones
    financieras (30, 32, 34), los aportes fiscales (27, 28) y los íntegros al fisco (25), que no son gasto del servicio.
  - Se excluye la partida 50 Tesoro Público (cuentas fiscales, no un servicio) y los montos en dólares (<3% de las filas).
  - Montos en millones de pesos nominales.
"""
import sqlite3
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent
DATOS = BASE / "datos"
DB = BASE / "presupuesto.db"
SUBTITULOS_GASTO = [21, 22, 23, 24, 26, 29, 31, 33]
CLAVE = ["año", "mes", "partida", "capitulo", "subtitulo"]


def filtrar(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df.moneda == "PESOS") & df.subtitulo.isin(SUBTITULOS_GASTO) & (df.partida != 50)]


def main():
    e = filtrar(pd.read_csv(DATOS / "ejecucion.csv"))
    l = filtrar(pd.read_csv(DATOS / "ley.csv"))
    montos = ["ejec_mes", "ejec_acum", "ley_inicial", "ley_vigente"]
    hechos = (e.groupby(CLAVE)[["ejec_mes", "ejec_acum"]].sum()
              .join(l.groupby(CLAVE)[["ley_inicial", "ley_vigente"]].sum(), how="outer")
              .fillna(0.0).reset_index())
    hechos[montos] = hechos[montos] / 1e3  # miles de pesos -> millones de pesos
    hechos = hechos[(hechos[montos] != 0).any(axis=1)]

    # Nombres: se usa el más reciente de cada código (los servicios cambian de nombre con los años).
    nombres = pd.concat([e, l]).sort_values(["año", "mes"])
    inst = (nombres.drop_duplicates(["partida", "capitulo"], keep="last")
            [["partida", "capitulo", "nombre_partida", "nombre_capitulo"]]
            .rename(columns={"nombre_partida": "ministerio", "nombre_capitulo": "institucion"}))
    inst["ministerio"] = inst.ministerio.str.title().str.replace(r"\bDe\b|\bDel\b|\bLa\b|\bLas\b|\bY\b|\bE\b|\bEl\b|\bLos\b|\bPara\b",
                                                                 lambda m: m.group(0).lower(), regex=True)
    inst["institucion"] = inst.institucion.str.strip()
    sub = (nombres.drop_duplicates("subtitulo", keep="last")[["subtitulo", "nombre_subtitulo"]]
           .rename(columns={"nombre_subtitulo": "tipo_gasto"}))
    sub["tipo_gasto"] = sub.tipo_gasto.str.capitalize()

    DB.unlink(missing_ok=True)
    with sqlite3.connect(DB) as con:
        hechos.to_sql("hechos", con, index=False)
        inst.to_sql("instituciones", con, index=False)
        sub.to_sql("subtitulos", con, index=False)
        con.executescript((BASE / "sql" / "vistas.sql").read_text(encoding="utf-8"))
        n = con.execute("SELECT COUNT(*) FROM v_institucion_año").fetchone()[0]
    print(f"presupuesto.db: {len(hechos):,} filas de hechos, {len(inst)} instituciones, {n:,} institución-año")


if __name__ == "__main__":
    main()
