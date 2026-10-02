"""Fase 3 - Exporta los datos del tablero web a docs/data.js (lo lee docs/index.html).

Uso:  python exportar_dashboard.py
Reutiliza las funciones de analisis.py: el tablero muestra exactamente los mismos números que el informe Excel.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

import analisis as A

BASE = Path(__file__).parent
DOCS = BASE / "docs"
TODOS = "Todos"


CONECTORES = {"de", "del", "la", "las", "los", "el", "y", "e", "para", "en", "a"}


def nombre(s: str) -> str:
    """'SUBSECRETARÍA DE REDES ASISTENCIALES' -> 'Subsecretaría de Redes Asistenciales'."""
    p = s.strip().lower().split()
    return " ".join(w if (i and w in CONECTORES) else w[:1].upper() + w[1:] for i, w in enumerate(p))


def r(v, d=1):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), d)


def curvas(m: pd.DataFrame, año: int, corte: int, actual: pd.DataFrame, grupo: str | None) -> dict:
    """% del presupuesto vigente ejecutado a cada mes: años normales (banda), año en curso y su proyección."""
    if grupo is not None:
        m = m[m.partida == int(grupo)]
        actual = actual[actual.partida == int(grupo)]
    n = m.groupby(["año", "mes"])[["ejec_acum", "ley_vigente"]].sum()
    # Todo contra el presupuesto vigente AL CORTE de cada año: así la historia y la proyección son comparables
    vig_corte = n.xs(corte, level="mes").ley_vigente
    pct = (n.ejec_acum.unstack().div(vig_corte, axis=0) * 100)
    normales = [a for a in pct.index if a < año and a not in A.AÑOS_ATIPICOS]
    h = pct.loc[normales]
    # Trayectoria proyectada: del ejecutado al corte al cierre proyectado, con la forma típica del año
    fin = n.loc[(año, 12)] if (año, 12) in n.index else None
    cerr = n.reset_index()
    cerr = cerr[cerr.año.isin(normales)]
    tot = cerr[cerr.mes == 12].set_index("año").ejec_acum
    forma = (cerr.assign(s=cerr.ejec_acum / cerr.año.map(tot)).groupby("mes").s.median())
    ej, vig = actual.ejec_corte.sum(), actual.vig_corte.sum()
    proy = actual.proy_patron.sum()
    tray = [None] * 12
    for mes in range(corte, 13):
        avance = (forma[mes] - forma[corte]) / (1 - forma[corte]) if forma[corte] < 1 else 1
        tray[mes - 1] = r((ej + (proy - ej) * avance) / vig * 100)
    return {
        "actual": [r(pct.loc[año, mes]) if mes <= corte else None for mes in range(1, 13)],
        "proyeccion": tray,
        "mediana": [r(h[mes].median()) for mes in range(1, 13)],
        "min": [r(h[mes].min()) for mes in range(1, 13)],
        "max": [r(h[mes].max()) for mes in range(1, 13)],
        "años_banda": [int(a) for a in normales],
    }


def kpis(m: pd.DataFrame, actual: pd.DataFrame, ia: pd.DataFrame, año: int, corte: int, grupo: str | None) -> dict:
    """Totales (vigente, inicial, ejecutado) con TODAS las instituciones; proyección y alertas con las que
    superan el presupuesto mínimo (igual que el modelo de Power BI)."""
    a = actual if grupo is None else actual[actual.partida == int(grupo)]
    t = m[(m.año == año) & (m.mes == corte)]
    h = ia[(ia.año < año) & ~ia.año.isin(A.AÑOS_ATIPICOS)]
    if grupo is not None:
        t, h = t[t.partida == int(grupo)], h[h.partida == int(grupo)]
    hist = h.groupby("año").apply(lambda g: g.ejec_corte.sum() / g.vig_corte.sum(), include_groups=False).median()
    al = a[a.alerta]
    return {
        "vigente": r(t.ley_vigente.sum(), 0), "inicial": r(t.ley_inicial.sum(), 0), "ejecutado": r(t.ejec_acum.sum(), 0),
        "pct_ejec": r(t.ejec_acum.sum() / t.ley_vigente.sum() * 100),
        "pct_hist": r(hist * 100),
        "pct_proy": r(a.proy_patron.sum() / a.vig_corte.sum() * 100),
        "n_inst": int(len(a)), "n_alertas": int(len(al)), "sin_ejecutar": r(al.sin_ejecutar_proy.sum(), 0),
    }


def main():
    m, inst, sub = A.cargar()
    año, corte = A.corte_actual(m)
    ia = A.institucion_año(m, inst, corte)
    actual = A.año_en_curso(m, inst, ia, año, corte)
    val, det = A.validar(ia, corte)
    rv = A.resumen_validacion(val)
    rv["regla_simple"] = A.regla_simple(ia, det)

    ministerios = (actual.groupby(["partida", "ministerio"]).vig_corte.sum().reset_index()
                   .sort_values("vig_corte", ascending=False))
    grupos = {TODOS: None} | {f"{p.ministerio}": str(p.partida) for p in ministerios.itertuples()}

    # Tabla por ministerio: avance al corte vs su avance típico, cierre proyectado y alertas
    h = ia[(ia.año < año) & ~ia.año.isin(A.AÑOS_ATIPICOS) & (ia.vig_corte > A.MIN_PRESUPUESTO)]
    hist_min = (h.groupby(["año", "partida"]).apply(lambda g: g.ejec_corte.sum() / g.vig_corte.sum(), include_groups=False)
                .groupby("partida").median())
    tmin = actual.groupby(["partida", "ministerio"]).agg(
        vigente=("vig_corte", "sum"), ejecutado=("ejec_corte", "sum"), proy=("proy_patron", "sum"),
        alertas=("alerta", "sum"), sin_ejecutar=("sin_ejecutar_proy", lambda s: s[actual.loc[s.index, "alerta"]].sum()),
    ).reset_index()
    tmin["pct_ejec"] = tmin.ejecutado / tmin.vigente * 100
    tmin["pct_hist"] = tmin.partida.map(hist_min) * 100
    tmin["pct_proy"] = tmin.proy / tmin.vigente * 100
    tmin = tmin.sort_values("vigente", ascending=False)

    al = actual[actual.alerta].sort_values("sin_ejecutar_proy", ascending=False)
    hist_inst = ia[ia.año < año].sort_values("año")
    trayect = hist_inst.groupby(A.K).apply(
        lambda g: [[int(a), r(p * 100)] for a, p in zip(g.año.tail(4), g.pct_final.tail(4))], include_groups=False)

    brecha = (ia[ia.vig_corte > A.MIN_PRESUPUESTO]
              .assign(c=lambda d: d.pct_final < A.UMBRAL_SUBEJECUCION, o=lambda d: d.pct_oficial < A.UMBRAL_SUBEJECUCION)
              .groupby("año")[["c", "o"]].mean() * 100)

    ultimo = ia[(ia.año >= año - 3) & (ia.año < año) & (ia.vig_final > 20000)]
    dic = (ultimo.groupby(A.K + ["ministerio", "institucion"])
           .agg(peso=("peso_diciembre", "median"), presupuesto=("vig_final", "last")).reset_index()
           .sort_values("peso", ascending=False).head(12))

    k_all = kpis(m, actual, ia, año, corte, None)
    top = al.head(3)
    mm_a_bn = lambda v: v / 1e6  # millones -> billones
    MES_LARGO = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                 "noviembre", "diciembre"]
    n = lambda v, dec=1: f"{v:,.{dec}f}".replace(",", "§").replace(".", ",").replace("§", ".")  # formato es-CL
    titular = (f"A {MES_LARGO[corte - 1]}, {k_all['n_alertas']} instituciones van camino a dejar sin gastar "
               f"${n(mm_a_bn(k_all['sin_ejecutar']))} billones de su presupuesto vigente. Las de mayor monto: "
               + ", ".join(nombre(t.institucion) for t in top.itertuples()) + ".")
    d = {
        "año": año, "corte": corte, "mes_corte": A.MESES[corte - 1], "mes_corte_largo": MES_LARGO[corte - 1],
        "titular": titular,
        "grupos": grupos,
        "kpis": {g: kpis(m, actual, ia, año, corte, p) for g, p in grupos.items()},
        "curvas": {g: curvas(m, año, corte, actual, p) for g, p in grupos.items()},
        "ministerios": [{"ministerio": x.ministerio, "partida": int(x.partida), "vigente": r(x.vigente, 0),
                         "pct_ejec": r(x.pct_ejec), "pct_hist": r(x.pct_hist), "pct_proy": r(x.pct_proy),
                         "alertas": int(x.alertas), "sin_ejecutar": r(x.sin_ejecutar, 0)} for x in tmin.itertuples()],
        "alertas": [{"partida": int(x.partida), "ministerio": x.ministerio, "institucion": nombre(x.institucion),
                     "vigente": r(x.vig_corte, 0), "pct_ejec": r(x.ejec_corte / x.vig_corte * 100),
                     "pct_proy": r(x.pct_proy * 100), "sin_ejecutar": r(x.sin_ejecutar_proy, 0),
                     "historia": trayect.get((x.partida, x.capitulo), []), "usa_historia": bool(x.usa_historia)}
                    for x in al.itertuples()],
        "brecha": [{"año": int(a), "corte": r(x.c), "oficial": r(x.o)} for a, x in brecha.iterrows()],
        "validacion": {"años": val.assign(Atípico=val.Atípico.astype(bool)).to_dict("records"), "resumen": rv},
        "diciembre": [{"ministerio": x.ministerio, "institucion": nombre(x.institucion), "peso": r(x.peso * 100),
                       "presupuesto": r(x.presupuesto, 0)} for x in dic.itertuples()],
        "umbral": A.UMBRAL_SUBEJECUCION * 100,
    }
    DOCS.mkdir(exist_ok=True)
    txt = json.dumps(d, ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    (DOCS / "data.js").write_text(f"window.DATA = {txt};\n", encoding="utf-8")
    print(f"docs/data.js: {len(txt) / 1024:.0f} KB · {len(grupos) - 1} ministerios · {len(al)} alertas")
    print(titular)


if __name__ == "__main__":
    main()
