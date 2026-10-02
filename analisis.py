"""Fase 2b - Análisis de la ejecución presupuestaria del Gobierno Central (presupuesto.db).

Uso:  python analisis.py   ->  resultados/informe_ejecucion.xlsx, resultados/resumen.json

1. Cuánto se ejecuta: % de ejecución contra la Ley inicial, el presupuesto vigente al corte y el vigente final
2. Ritmo del gasto: curva mensual y peso de diciembre
3. Proyección de cierre al corte (agosto) por institución, con dos métodos
4. Validación sin mirar el futuro: cada año se proyecta solo con los años anteriores
5. Alertas del año en curso: instituciones que, según la proyección, no ejecutarán su presupuesto
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
DB = BASE / "presupuesto.db"
OUT = BASE / "resultados"
K = ["partida", "capitulo"]

# Parámetros fijados antes de validar
UMBRAL_SUBEJECUCION = 0.90   # cierra bajo 90% del presupuesto vigente al corte
MIN_PRESUPUESTO = 1_000      # MM$: se ignoran instituciones con presupuesto menor (ruido de montos chicos)
AÑOS_HISTORIA = 3            # años previos para estimar el patrón propio de cada institución
AÑOS_ATIPICOS = {2020, 2021} # pandemia: se informan aparte en la validación
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def cargar():
    con = sqlite3.connect(DB)
    m = pd.read_sql("SELECT * FROM v_institucion_mes", con)
    inst = pd.read_sql("SELECT * FROM instituciones", con)
    sub = pd.read_sql("SELECT h.año, h.mes, h.subtitulo, s.tipo_gasto, SUM(h.ejec_acum) ejec_acum, "
                      "SUM(h.ley_vigente) ley_vigente FROM hechos h JOIN subtitulos s USING (subtitulo) "
                      "GROUP BY 1, 2, 3, 4", con)
    con.close()
    return m, inst, sub


def corte_actual(m: pd.DataFrame) -> tuple[int, int]:
    """Último año y mes con datos (el año en curso)."""
    año = int(m.año.max())
    return año, int(m[m.año == año].mes.max())


# ------------------------------------------------------------------
#  1-2. Panorama: ejecución anual y ritmo mensual
# ------------------------------------------------------------------
def panorama_anual(m: pd.DataFrame, corte: int) -> pd.DataFrame:
    dic = m[m.mes == 12].groupby("año")[["ejec_acum", "ley_inicial", "ley_vigente"]].sum()
    vc = m[m.mes == corte].groupby("año").ley_vigente.sum().rename("vig_corte")
    t = dic.join(vc)
    return pd.DataFrame({
        "Ley inicial MM$": t.ley_inicial, "Vigente final MM$": t.ley_vigente, "Ejecutado MM$": t.ejec_acum,
        "% vs Ley inicial": t.ejec_acum / t.ley_inicial * 100, "% vs vigente final": t.ejec_acum / t.ley_vigente * 100,
        "Modificación del presupuesto %": (t.ley_vigente / t.ley_inicial - 1) * 100,
    }).round(1)


def curva_mensual(m: pd.DataFrame) -> pd.DataFrame:
    """% del gasto anual ejecutado a cada mes (años cerrados) y % del vigente ejecutado (año en curso)."""
    n = m.groupby(["año", "mes"])[["ejec_acum", "ley_vigente"]].sum().reset_index()
    cerrado = n[n.año.isin(n[n.mes == 12].año)]
    total = cerrado[cerrado.mes == 12].set_index("año").ejec_acum
    cerrado = cerrado.assign(pct=cerrado.ejec_acum / cerrado.año.map(total) * 100)
    return cerrado.pivot(index="mes", columns="año", values="pct").round(1)


def institucion_año(m: pd.DataFrame, inst: pd.DataFrame, corte: int) -> pd.DataFrame:
    """Una fila por institución y año cerrado: lo ejecutado a diciembre y la foto al corte."""
    dic = m[m.mes == 12][["año"] + K + ["ejec_mes", "ejec_acum", "ley_inicial", "ley_vigente"]].rename(
        columns={"ejec_mes": "ejec_dic", "ejec_acum": "ejec_final", "ley_vigente": "vig_final"})
    c = m[m.mes == corte][["año"] + K + ["ejec_acum", "ley_vigente"]].rename(
        columns={"ejec_acum": "ejec_corte", "ley_vigente": "vig_corte"})
    d = dic.merge(c, on=["año"] + K, how="left").merge(inst, on=K)
    d["pct_final"] = d.ejec_final / d.vig_corte          # cierre contra el presupuesto que tenía al corte
    d["pct_oficial"] = d.ejec_final / d.vig_final        # cierre contra el vigente final (cifra oficial)
    d["peso_diciembre"] = d.ejec_dic / d.ejec_final
    d["avance_corte"] = d.ejec_corte / d.ejec_final      # qué fracción de su gasto anual llevaba al corte
    d["recorte_post_corte"] = d.vig_final / d.vig_corte - 1
    return d


# ------------------------------------------------------------------
#  3. Proyección de cierre al corte
# ------------------------------------------------------------------
def proyectar(hist: pd.DataFrame, foto: pd.DataFrame, corte: int) -> pd.DataFrame:
    """hist: años cerrados ANTERIORES (institucion_año). foto: el año a proyectar al corte (ejec_corte, vig_corte).

    Método patrón: cierre = ejecutado al corte / avance típico de esa institución al corte en sus últimos años
    (mediana; si no tiene historia, el avance típico de todo el Estado). Método lineal: ritmo diario parejo.
    """
    años = sorted(hist.año.unique())[-AÑOS_HISTORIA:]
    h = hist[hist.año.isin(años) & hist.avance_corte.between(0.05, 1.5)]
    propio = h.groupby(K).avance_corte.median().rename("avance_tipico")
    nacional = (h.groupby("año").apply(lambda g: g.ejec_corte.sum() / g.ejec_final.sum(), include_groups=False)
                .median())
    f = foto.merge(propio, on=K, how="left")
    f["usa_historia"] = f.avance_tipico.notna()
    f["avance_tipico"] = f.avance_tipico.fillna(nacional)
    f["proy_patron"] = f.ejec_corte / f.avance_tipico
    f["proy_lineal"] = f.ejec_corte * 12 / corte
    f["pct_proy"] = f.proy_patron / f.vig_corte
    f["pct_proy_lineal"] = f.proy_lineal / f.vig_corte
    f["alerta"] = f.pct_proy < UMBRAL_SUBEJECUCION
    f["sin_ejecutar_proy"] = (f.vig_corte - f.proy_patron).clip(lower=0)
    return f


# ------------------------------------------------------------------
#  4. Validación: proyectar cada año pasado con lo que se sabía en ese momento
# ------------------------------------------------------------------
def validar(ia: pd.DataFrame, corte: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    filas, detalle = [], []
    for año in sorted(ia.año.unique()):
        hist = ia[ia.año < año]
        if hist.año.nunique() < 2:
            continue
        foto = ia[(ia.año == año) & (ia.vig_corte > MIN_PRESUPUESTO) & (ia.ejec_corte > 0)]
        p = proyectar(hist, foto, corte)
        real_bajo = p.pct_final < UMBRAL_SUBEJECUCION
        vp, fp, fn = (p.alerta & real_bajo).sum(), (p.alerta & ~real_bajo).sum(), (~p.alerta & real_bajo).sum()
        filas.append({
            "Año": año, "Instituciones": len(p), "Cerraron bajo 90%": int(real_bajo.sum()),
            "Alertas": int(p.alerta.sum()), "Aciertos": int(vp), "Falsas alarmas": int(fp), "No detectadas": int(fn),
            "Precisión %": vp / max(vp + fp, 1) * 100, "Cobertura %": vp / max(vp + fn, 1) * 100,
            "Error patrón (pp)": (p.pct_proy - p.pct_final).abs().median() * 100,
            "Error lineal (pp)": (p.pct_proy_lineal - p.pct_final).abs().median() * 100,
            "Atípico": año in AÑOS_ATIPICOS,
        })
        detalle.append(p.assign(real_bajo=real_bajo))
    return pd.DataFrame(filas).round(1), pd.concat(detalle, ignore_index=True)


def regla_simple(ia: pd.DataFrame, det: pd.DataFrame) -> dict:
    """Punto de comparación: alertar a quien ya cerró bajo 90% el año anterior. El modelo debe ganarle."""
    prev = (ia[["año"] + K + ["pct_final"]].assign(año=lambda d: d.año + 1)
            .rename(columns={"pct_final": "pct_prev"}))
    d = det.merge(prev, on=["año"] + K, how="left")
    d = d[~d.año.isin(AÑOS_ATIPICOS)]
    alerta = d.pct_prev < UMBRAL_SUBEJECUCION
    vp = int((alerta & d.real_bajo).sum())
    return {"alertas": int(alerta.sum()), "aciertos": vp, "precision": round(vp / alerta.sum() * 100, 1),
            "cobertura": round(vp / d.real_bajo.sum() * 100, 1)}


def resumen_validacion(v: pd.DataFrame) -> dict:
    normal = v[~v.Atípico]
    s = normal[["Cerraron bajo 90%", "Alertas", "Aciertos", "Falsas alarmas", "No detectadas"]].sum()
    return {"años": [int(a) for a in normal.Año], "aciertos": int(s.Aciertos), "alertas": int(s.Alertas),
            "casos": int(s["Cerraron bajo 90%"]), "falsas": int(s["Falsas alarmas"]),
            "precision": round(s.Aciertos / s.Alertas * 100, 1), "cobertura": round(s.Aciertos / s["Cerraron bajo 90%"] * 100, 1),
            "error_patron_pp": round(normal["Error patrón (pp)"].median(), 1),
            "error_lineal_pp": round(normal["Error lineal (pp)"].median(), 1)}


# ------------------------------------------------------------------
#  5. Año en curso
# ------------------------------------------------------------------
def año_en_curso(m, inst, ia, año, corte) -> pd.DataFrame:
    foto = (m[(m.año == año) & (m.mes == corte)][K + ["ejec_acum", "ley_vigente", "ley_inicial"]]
            .rename(columns={"ejec_acum": "ejec_corte", "ley_vigente": "vig_corte"}).merge(inst, on=K))
    foto = foto[(foto.vig_corte > MIN_PRESUPUESTO) & (foto.ejec_corte > 0)]
    return proyectar(ia[ia.año < año], foto, corte).sort_values("sin_ejecutar_proy", ascending=False)


def main():
    OUT.mkdir(exist_ok=True)
    m, inst, sub = cargar()
    año, corte = corte_actual(m)
    print(f"Datos hasta {MESES[corte - 1]}-{año} (corte de análisis: {MESES[corte - 1]})\n")

    pan = panorama_anual(m, corte)
    print("Ejecución anual del Gobierno Central (sin Tesoro Público, MM$):")
    print(pan.to_string(), "\n")

    curva = curva_mensual(m)
    print(f"% del gasto anual ejecutado a {MESES[corte - 1]}: "
          + ", ".join(f"{a}: {curva.loc[corte, a]}" for a in curva.columns), "\n")

    ia = institucion_año(m, inst, corte)
    ia_ok = ia[ia.vig_corte > MIN_PRESUPUESTO]
    sub_ej = ia_ok.assign(bajo=ia_ok.pct_final < UMBRAL_SUBEJECUCION, bajo_of=ia_ok.pct_oficial < UMBRAL_SUBEJECUCION)
    brecha = sub_ej.groupby("año")[["bajo", "bajo_of"]].mean().mul(100).round(1)
    brecha.columns = [f"% bajo 90% vs vigente a {MESES[corte - 1]}", "% bajo 90% vs vigente final (oficial)"]
    print("Instituciones que no ejecutan su presupuesto: depende contra qué presupuesto se mida")
    print(brecha.to_string(), "\n")

    dic_inst = (ia_ok[ia_ok.año >= ia_ok.año.max() - 2].groupby(K + ["ministerio", "institucion"])
                .agg(peso_diciembre=("peso_diciembre", "median"), presupuesto=("vig_final", "mean"))
                .reset_index().query("presupuesto > 20000").sort_values("peso_diciembre", ascending=False))

    val, det = validar(ia, corte)
    rv = resumen_validacion(val)
    print(f"Validación de la proyección a {MESES[corte - 1]} (cada año usa solo los anteriores):")
    print(val.to_string(index=False))
    print(f"\nAños normales: {rv['aciertos']} de {rv['alertas']} alertas acertaron (precisión {rv['precision']}%), "
          f"detectó {rv['aciertos']} de {rv['casos']} casos (cobertura {rv['cobertura']}%). "
          f"Error típico: patrón {rv['error_patron_pp']} pp vs lineal {rv['error_lineal_pp']} pp")
    rv["regla_simple"] = regla_simple(ia, det)
    rs = rv["regla_simple"]
    print(f"Regla simple (repetir el año anterior): precisión {rs['precision']}%, cobertura {rs['cobertura']}%\n")

    actual = año_en_curso(m, inst, ia, año, corte)
    alertas = actual[actual.alerta]
    print(f"{año} a {MESES[corte - 1]}: {len(alertas)} instituciones proyectan cerrar bajo 90% "
          f"(${alertas.sin_ejecutar_proy.sum():,.0f} MM sin ejecutar)")
    print(alertas.head(12)[["ministerio", "institucion", "vig_corte", "ejec_corte", "pct_proy", "sin_ejecutar_proy"]]
          .round(3).to_string(index=False))

    nac = actual.agg({"ejec_corte": "sum", "vig_corte": "sum", "proy_patron": "sum"})
    resumen = {
        "año": año, "corte": corte, "mes_corte": MESES[corte - 1],
        "avance_nacional_pct": round(nac.ejec_corte / nac.vig_corte * 100, 1),
        "proyeccion_nacional_pct": round(nac.proy_patron / nac.vig_corte * 100, 1),
        "alertas": int(len(alertas)), "sin_ejecutar_MM": round(float(alertas.sin_ejecutar_proy.sum()), 0),
        "validacion": rv, "instituciones": int(len(actual)),
    }
    (OUT / "resumen.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")

    with pd.ExcelWriter(OUT / "informe_ejecucion.xlsx") as xw:
        pan.to_excel(xw, sheet_name="Panorama anual")
        curva.to_excel(xw, sheet_name="Curva mensual")
        brecha.to_excel(xw, sheet_name="Subejecución")
        val.to_excel(xw, sheet_name="Validación", index=False)
        actual[["ministerio", "institucion", "ley_inicial", "vig_corte", "ejec_corte", "avance_tipico", "proy_patron",
                "pct_proy", "alerta", "sin_ejecutar_proy", "usa_historia"]].to_excel(
            xw, sheet_name=f"Proyección {año}", index=False)
        dic_inst.to_excel(xw, sheet_name="Peso de diciembre", index=False)
    print(f"\nInforme: {OUT / 'informe_ejecucion.xlsx'}")


if __name__ == "__main__":
    main()
