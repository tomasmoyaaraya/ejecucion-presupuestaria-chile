"""Fase 1 - Descarga la ejecución presupuestaria y la Ley de Presupuestos del Gobierno Central (DIPRES).

Uso:  python descargar.py   ->  datos/ejecucion.csv, datos/ley.csv, datos/catalogo.csv

Fuente: portal de datos abiertos datos.gob.cl (organización Dirección de Presupuestos), sin clave.
Cada mes la DIPRES publica dos conjuntos:
  - "Ejecución Presupuestaria a <mes> de <año>": lo gastado/recaudado en el mes y acumulado en el año.
  - "Ley de Presupuestos Inicial y Vigente a <mes> de <año>": el presupuesto aprobado y el vigente a ese mes
    (el vigente cambia durante el año por decretos de modificación).
Se usa el recurso "A nivel Capítulo" (un capítulo = un servicio o institución) desglosado por subtítulo
(tipo de ingreso o gasto). Montos en miles de pesos (o miles de dólares en la moneda DOLARES).

Los CSV originales se guardan en datos/crudos/ (caché): si ya están, no se vuelven a descargar.
"""
import io
import json
import re
import time
import unicodedata
import urllib.request
from pathlib import Path

import pandas as pd

BASE = Path(__file__).parent
DATOS = BASE / "datos"
CRUDOS = DATOS / "crudos"
API = "https://datos.gob.cl/api/3/action/package_search?fq=organization:direccion_de_presupuestos&rows=1000"
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre",
         "Noviembre", "Diciembre"]
DESDE = 2017  # antes de 2017 solo hay cierres anuales (no sirven para la curva mensual)

# Título del conjunto: "Ejecución Presupuestaria a Mayo de 2025 del Gobierno Central - Total"
PATRON = re.compile(r"^(Ejecución Presupuestaria|Ley de Presupuestos Inicial y Vigente) a (\w+) de (\d{4}) "
                    r"del Gobierno Central(?: - (.*))?$")
# Desde 2021 hay versiones por fuente (Ley Regular, fondos de emergencia FET); se usa siempre el Total.
VERSION_TOTAL = {None, "Total", "Ejecución Total"}


def catalogo() -> pd.DataFrame:
    """Lista los conjuntos mensuales disponibles y la URL del recurso a nivel capítulo."""
    with urllib.request.urlopen(API, timeout=60) as r:
        paquetes = json.load(r)["result"]["results"]
    filas = []
    for p in paquetes:
        m = PATRON.match(p["title"].strip())
        if not m or m.group(4) not in VERSION_TOTAL or m.group(2) not in MESES or int(m.group(3)) < DESDE:
            continue
        rec = [r for r in p["resources"] if "Capítulo" in r["name"] or "Capitulo" in r["name"]]
        if rec:
            filas.append({"tipo": "ejecucion" if m.group(1).startswith("Ejec") else "ley",
                          "año": int(m.group(3)), "mes": MESES.index(m.group(2)) + 1, "url": rec[0]["url"]})
    return pd.DataFrame(filas).drop_duplicates(["tipo", "año", "mes"]).sort_values(["tipo", "año", "mes"])


def bajar(url: str, destino: Path) -> bytes:
    if destino.exists():
        return destino.read_bytes()
    for intento in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                datos = r.read()
            destino.write_bytes(datos)
            time.sleep(0.5)  # no saturar el portal
            return datos
        except Exception as e:  # noqa: BLE001 - reintento simple ante cortes de red
            print(f"   reintento {intento + 1}: {e}")
            time.sleep(3)
    raise RuntimeError(f"No se pudo descargar {url}")


def sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def numero(s: pd.Series) -> pd.Series:
    """Montos con punto de miles y coma decimal ("1.234,5") -> float."""
    s = s.astype(str).str.strip().str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(s.replace({"": None, "nan": None, "-": None}), errors="coerce").fillna(0.0)


def leer(datos: bytes, tipo: str) -> pd.DataFrame:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            # index_col=False: algunos años traen un ";" sobrante al final de cada fila
            df = pd.read_csv(io.BytesIO(datos), sep=None, engine="python", encoding=enc, dtype=str, index_col=False)
            break
        except UnicodeDecodeError:
            continue
    # Los nombres de columna cambian de mayúsculas y tildes entre años: se normalizan.
    df.columns = [sin_tildes(c).strip().lower() for c in df.columns]
    col = lambda prefijo: next(c for c in df.columns if c.startswith(prefijo))
    out = pd.DataFrame({
        "moneda": df["moneda"].str.upper().map(sin_tildes),
        "partida": pd.to_numeric(df["partida"]).astype(int), "nombre_partida": df["nombre partida"].str.strip(),
        "capitulo": pd.to_numeric(df["capitulo"]).astype(int), "nombre_capitulo": df["nombre capitulo"].str.strip(),
        "subtitulo": pd.to_numeric(df["subtitulo"]).astype(int), "nombre_subtitulo": df["nombre subtitulo"].str.strip(),
    })
    if tipo == "ejecucion":
        out["ejec_mes"] = numero(df[col("ejecucion de")])
        out["ejec_acum"] = numero(df[col("ejecucion a")])
    else:
        out["ley_inicial"] = numero(df[col("monto ley inicial")])
        out["ley_vigente"] = numero(df[col("monto ley vigente")])
    return out


def main():
    CRUDOS.mkdir(parents=True, exist_ok=True)
    cat = catalogo()
    cat.to_csv(DATOS / "catalogo.csv", index=False)
    print(f"Catálogo: {len(cat)} archivos mensuales "
          f"({(cat.tipo == 'ejecucion').sum()} de ejecución, {(cat.tipo == 'ley').sum()} de ley)")
    partes = {"ejecucion": [], "ley": []}
    for f in cat.itertuples():
        destino = CRUDOS / f"{f.tipo}_{f.año}_{f.mes:02d}.csv"
        nuevo = not destino.exists()
        df = leer(bajar(f.url, destino), f.tipo)
        df.insert(0, "mes", f.mes)
        df.insert(0, "año", f.año)
        partes[f.tipo].append(df)
        if nuevo:
            print(f"   {f.tipo} {f.año}-{f.mes:02d}: {len(df)} filas")
    for tipo, lista in partes.items():
        out = pd.concat(lista, ignore_index=True)
        out.to_csv(DATOS / f"{tipo}.csv", index=False, encoding="utf-8")
        print(f"{tipo}.csv: {len(out):,} filas, años {out.año.min()}-{out.año.max()}")


if __name__ == "__main__":
    main()
