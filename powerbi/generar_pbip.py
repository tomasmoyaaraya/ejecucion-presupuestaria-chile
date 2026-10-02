"""Fase 3b - Genera el proyecto de Power BI (formato .pbip: modelo en TMDL + informe en PBIR).

Uso:  python powerbi/generar_pbip.py   ->  powerbi/Ejecucion.pbip  (abrir con Power BI Desktop y pulsar Actualizar)

1. Exporta a datos/powerbi/ los CSV que lee el modelo (hechos mensuales y resultados de analisis.py).
2. Escribe el modelo semántico (tablas, relaciones y medidas DAX) y el informe (3 páginas, tema IBCS).
La curva de ejecución y los KPIs se calculan en DAX sobre los hechos mensuales; la proyección por
institución y la validación vienen precalculadas desde Python (mismas cifras que el tablero web).
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pandas as pd

PBI = Path(__file__).parent
BASE = PBI.parent
sys.path.insert(0, str(BASE))
import analisis as A  # noqa: E402

DATOS = (BASE / "datos" / "powerbi").resolve()
NOMBRE = "Ejecucion"
SM = PBI / f"{NOMBRE}.SemanticModel"
RP = PBI / f"{NOMBRE}.Report"
T = "\t"


# ======================================================================
#  1. Datos para el modelo
# ======================================================================
def exportar_csv():
    DATOS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BASE / "presupuesto.db")
    h = pd.read_sql("SELECT año, mes, partida, capitulo, SUM(ejec_mes) ejec_mes, SUM(ejec_acum) ejec_acum, "
                    "SUM(ley_inicial) ley_inicial, SUM(ley_vigente) ley_vigente FROM hechos GROUP BY 1, 2, 3, 4", con)
    inst = pd.read_sql("SELECT * FROM instituciones", con)
    con.close()
    iid = lambda d: d.partida * 1000 + d.capitulo
    h.insert(0, "inst_id", iid(h))
    inst.insert(0, "inst_id", iid(inst))
    inst["institucion"] = inst.institucion.map(nombre)
    inst["ministerio_corto"] = inst.ministerio.str.replace(r"^Ministerio (de la |del |de las |de |)", "", regex=True).str.strip()
    inst["ministerio_corto"] = (inst.ministerio_corto.str[:1].str.upper() + inst.ministerio_corto.str[1:]).replace(
        {"Financiamiento Gobiernos Regionales": "Gobiernos Regionales"})
    h.drop(columns=["partida", "capitulo"]).to_csv(DATOS / "hechos.csv", index=False)
    inst.to_csv(DATOS / "instituciones.csv", index=False)

    m, i2, _ = A.cargar()
    año, corte = A.corte_actual(m)
    ia = A.institucion_año(m, i2, corte)
    act = A.año_en_curso(m, i2, ia, año, corte)
    act.insert(0, "inst_id", iid(act))
    act[["inst_id", "vig_corte", "ejec_corte", "proy_patron", "avance_tipico", "pct_proy", "alerta",
         "sin_ejecutar_proy"]].to_csv(DATOS / "proyeccion.csv", index=False)
    al = act[act.alerta]
    prev = ia[ia.año < año].sort_values("año").groupby(A.K).pct_final.apply(
        lambda s: " · ".join(f"{int(round(v * 100, 1) + 0.5)}%" for v in s.tail(3)))  # redondeo igual al tablero web
    al = al.assign(años_previos=[prev.get((p, c), "nueva") for p, c in zip(al.partida, al.capitulo)])
    al[["inst_id", "vig_corte", "ejec_corte", "pct_proy", "sin_ejecutar_proy", "años_previos"]].to_csv(
        DATOS / "alertas.csv", index=False)
    val, _ = A.validar(ia, corte)
    val.rename(columns={"Año": "año", "Instituciones": "instituciones", "Cerraron bajo 90%": "cerraron_bajo",
                        "Alertas": "alertas", "Aciertos": "aciertos", "Falsas alarmas": "falsas",
                        "No detectadas": "no_detectadas", "Error patrón (pp)": "error_pp", "Error lineal (pp)": "error_lineal_pp",
                        "Atípico": "atipico"}).drop(columns=["Precisión %", "Cobertura %"]).assign(
        pandemia=lambda d: d.atipico.map({True: "Sí", False: ""})).to_csv(
        DATOS / "validacion.csv", index=False)
    ok = ia[ia.vig_corte > A.MIN_PRESUPUESTO]
    (ok.assign(corte=ok.pct_final < A.UMBRAL_SUBEJECUCION, oficial=ok.pct_oficial < A.UMBRAL_SUBEJECUCION)
     .groupby("año")[["oficial", "corte"]].mean().reset_index().assign(periodo=lambda d: d.año.astype(str))
     .to_csv(DATOS / "brecha.csv", index=False))
    return año, corte, len(al), al.sin_ejecutar_proy.sum()


CONECTORES = {"de", "del", "la", "las", "los", "el", "y", "e", "para", "en", "a"}


def nombre(s: str) -> str:
    p = s.strip().lower().split()
    return " ".join(w if (i and w in CONECTORES) else w[:1].upper() + w[1:] for i, w in enumerate(p))


# ======================================================================
#  2. Modelo semántico (TMDL)
# ======================================================================
def m_csv(archivo: str, tipos: list) -> str:
    t = ", ".join(f'{{"{c}", {ty}}}' for c, ty in tipos)
    return ("let\n"
            f'    Origen = Csv.Document(File.Contents(CarpetaDatos & "\\{archivo}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n'
            "    Encabezados = Table.PromoteHeaders(Origen, [PromoteAllScalars=true]),\n"
            f'    Tipos = Table.TransformColumnTypes(Encabezados, {{{t}}}, "en-US")\n'
            "in\n    Tipos")


def columna(nombre, tipo, fmt=None, oculta=False, resumir="none", sort_by=None):
    q = f"'{nombre}'" if not nombre.isidentifier() else nombre
    s = f"{T}column {q}\n{T*2}dataType: {tipo}\n"
    if fmt:
        s += f"{T*2}formatString: {fmt}\n"
    if oculta:
        s += f"{T*2}isHidden\n"
    if sort_by:
        s += f"{T*2}sortByColumn: {sort_by}\n"
    return s + f"{T*2}summarizeBy: {resumir}\n{T*2}sourceColumn: {nombre}\n\n"


def tabla(nombre, columnas, m, medidas=""):
    s = f"table {nombre}\n\n" + medidas
    for c in columnas:
        s += columna(*c) if isinstance(c, tuple) else columna(**c)
    src = "\n".join(T * 4 + linea for linea in m.splitlines())
    return s + f"{T}partition {nombre} = m\n{T*2}mode: import\n{T*2}source =\n{src}\n\n"


def medida(nombre, expr, fmt, carpeta):
    lineas = expr.strip().splitlines()
    s = (f"{T}measure '{nombre}' = {lineas[0]}\n" if len(lineas) == 1
         else f"{T}measure '{nombre}' =\n" + "\n".join(T * 3 + l for l in lineas) + "\n")
    if fmt:
        s += f"{T*2}formatString: {fmt}\n"
    return s + f"{T*2}displayFolder: {carpeta}\n\n"


PCT = "0.0%"
BN = r'\$#,0.0" bn"'
MMF = r'\$#,0" MM"'
ROJO, VERDE, TINTA = "#D6453D", "#3B9A52", "#1A1A1A"
# Ejecutado y vigente "al corte": mes más reciente del año en curso. Se filtra la tabla de hechos directamente
# (REMOVEFILTERS de los ejes) para que las tarjetas no dependan del mes o año que muestre un gráfico.
AL_CORTE = ("VAR a = [Año en Curso]\nVAR c = [Mes de Corte]\n"
            "RETURN CALCULATE(SUM(Hechos[{col}]), REMOVEFILTERS(Meses), REMOVEFILTERS(Ejercicio), "
            "Hechos[año] = a, Hechos[mes] = c)")
MEDIDAS = [
    ("Año en Curso", "CALCULATE(MAX(Hechos[año]), REMOVEFILTERS())", "0", "1 Corte"),
    ("Mes de Corte", "VAR a = [Año en Curso]\nRETURN CALCULATE(MAX(Hechos[mes]), REMOVEFILTERS(), Hechos[año] = a)", "0", "1 Corte"),
    ("Vigente al Corte", AL_CORTE.format(col="ley_vigente"), MMF, "2 Ejecución"),
    ("Ley Inicial", AL_CORTE.format(col="ley_inicial"), MMF, "2 Ejecución"),
    ("Ejecutado al Corte", AL_CORTE.format(col="ejec_acum"), MMF, "2 Ejecución"),
    ("Vigente (bn)", "DIVIDE([Vigente al Corte], 1e6)", BN, "2 Ejecución"),
    ("% Ejecutado", "DIVIDE([Ejecutado al Corte], [Vigente al Corte])", PCT, "2 Ejecución"),
    ("Modificación vs Ley Inicial %", "DIVIDE([Vigente al Corte], [Ley Inicial]) - 1", "+0.0%;-0.0%;0.0%", "2 Ejecución"),
    # --- curva mensual: gasto acumulado / presupuesto vigente al corte de ese mismo año
    ("Curva Año en Curso %",
     "VAR a = [Año en Curso]\nVAR c = [Mes de Corte]\nVAR m = MAX(Meses[mes])\n"
     "VAR vig = CALCULATE(SUM(Hechos[ley_vigente]), REMOVEFILTERS(Meses), REMOVEFILTERS(Ejercicio), Hechos[año] = a, Hechos[mes] = c)\n"
     "RETURN IF(m <= c, DIVIDE(CALCULATE(SUM(Hechos[ejec_acum]), REMOVEFILTERS(Ejercicio), Hechos[año] = a), vig))",
     PCT, "3 Curva"),
    ("Curva Mediana Años Anteriores %",
     "VAR a = [Año en Curso]\nVAR c = [Mes de Corte]\n"
     "VAR t = ADDCOLUMNS(\n"
     "    FILTER(ALL(Ejercicio[año]), Ejercicio[año] < a && NOT Ejercicio[año] IN {2020, 2021}),\n"
     "    \"@p\", VAR y = Ejercicio[año] RETURN DIVIDE(\n"
     "        CALCULATE(SUM(Hechos[ejec_acum]), REMOVEFILTERS(Ejercicio), Hechos[año] = y),\n"
     "        CALCULATE(SUM(Hechos[ley_vigente]), REMOVEFILTERS(Ejercicio), REMOVEFILTERS(Meses), Hechos[año] = y, Hechos[mes] = c)))\n"
     "RETURN MEDIANX(t, [@p])",
     PCT, "3 Curva"),
    ("Curva Proyección %",
     "VAR c = [Mes de Corte]\nVAR m = MAX(Meses[mes])\nVAR e = SUM(Proyeccion[ejec_corte])\n"
     "VAR p = SUM(Proyeccion[proy_patron])\nVAR v = SUM(Proyeccion[vig_corte])\n"
     "RETURN IF(m >= c, DIVIDE(e + (p - e) * (m - c) / (12 - c), v))",
     PCT, "3 Curva"),
    # --- proyección y alertas (precalculadas en Python)
    ("Cierre Proyectado %", "DIVIDE(SUM(Proyeccion[proy_patron]), SUM(Proyeccion[vig_corte]))", PCT, "4 Proyección"),
    ("Instituciones", "COUNTROWS(Proyeccion)", "#,0", "4 Proyección"),
    ("Instituciones en Alerta", "COUNTROWS(Alertas)", "#,0", "4 Proyección"),
    ("Monto en Riesgo", "SUM(Alertas[sin_ejecutar_proy])", MMF, "4 Proyección"),
    ("Monto en Riesgo (bn)", "DIVIDE([Monto en Riesgo], 1e6)", BN, "4 Proyección"),
    ("% Ejecutado Alerta", "DIVIDE(SUM(Alertas[ejec_corte]), SUM(Alertas[vig_corte]))", PCT, "4 Proyección"),
    ("Cierre Proyectado Alerta %", "DIVIDE(SUMX(Alertas, Alertas[pct_proy] * Alertas[vig_corte]), SUM(Alertas[vig_corte]))", PCT, "4 Proyección"),
    ("Color Cierre", f'IF([Cierre Proyectado %] < {A.UMBRAL_SUBEJECUCION}, "{ROJO}", "{TINTA}")', "", "4 Proyección"),
    # --- validación (años normales, sin pandemia)
    ("Precisión", "DIVIDE(CALCULATE(SUM(Validacion[aciertos]), Validacion[atipico] = FALSE()), "
                  "CALCULATE(SUM(Validacion[alertas]), Validacion[atipico] = FALSE()))", PCT, "5 Validación"),
    ("Cobertura", "DIVIDE(CALCULATE(SUM(Validacion[aciertos]), Validacion[atipico] = FALSE()), "
                  "CALCULATE(SUM(Validacion[cerraron_bajo]), Validacion[atipico] = FALSE()))", PCT, "5 Validación"),
    ("Error Típico (pp)", "CALCULATE(MEDIAN(Validacion[error_pp]), Validacion[atipico] = FALSE())", "0.0", "5 Validación"),
    ("% Bajo 90% (oficial)", "SUM(Brecha[oficial])", PCT, "5 Validación"),
    ("% Bajo 90% (al corte)", "SUM(Brecha[corte])", PCT, "5 Validación"),
]


def generar_modelo():
    defn = SM / "definition"
    (defn / "tables").mkdir(parents=True, exist_ok=True)
    (SM / "definition.pbism").write_text(json.dumps({"version": "4.0", "settings": {}}, indent=2), encoding="utf-8")
    (defn / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1606\n", encoding="utf-8")
    tablas = ["Hechos", "Instituciones", "Ejercicio", "Meses", "Proyeccion", "Alertas", "Validacion", "Brecha", "Medidas"]
    (defn / "model.tmdl").write_text(
        "model Model\n\tculture: es-CL\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\tsourceQueryCulture: es-CL\n"
        "\tdataAccessOptions\n\t\tlegacyRedirects\n\t\treturnErrorValuesAsNull\n\n"
        + "".join(f"ref table {t}\n" for t in tablas) + "\nref expression CarpetaDatos\n", encoding="utf-8")
    (defn / "expressions.tmdl").write_text(
        f'expression CarpetaDatos = "{DATOS}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n',
        encoding="utf-8")
    I, N, X = "Int64.Type", "type number", "type text"
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    tb = {
        "Hechos": tabla("Hechos", [("inst_id", "int64", "0", True), ("año", "int64", "0", True), ("mes", "int64", "0", True),
                                   ("ejec_mes", "double", "#,0", False, "sum"), ("ejec_acum", "double", "#,0", False, "sum"),
                                   ("ley_inicial", "double", "#,0", False, "sum"), ("ley_vigente", "double", "#,0", False, "sum")],
                        m_csv("hechos.csv", [("inst_id", I), ("año", I), ("mes", I), ("ejec_mes", N), ("ejec_acum", N),
                                             ("ley_inicial", N), ("ley_vigente", N)])),
        "Instituciones": tabla("Instituciones", [("inst_id", "int64", "0", True), ("partida", "int64", "0", True),
                                                 ("capitulo", "int64", "0", True), ("ministerio", "string"), ("institucion", "string"),
                                                 ("ministerio_corto", "string")],
                               m_csv("instituciones.csv", [("inst_id", I), ("partida", I), ("capitulo", I), ("ministerio", X),
                                                           ("institucion", X), ("ministerio_corto", X)])),
        "Ejercicio": tabla("Ejercicio", [("año", "int64", "0")],
                           "let\n    Fuente = Table.FromList({2017..2030}, Splitter.SplitByNothing(), {\"año\"}),\n"
                           "    Tipos = Table.TransformColumnTypes(Fuente, {{\"año\", Int64.Type}})\nin\n    Tipos"),
        "Meses": tabla("Meses", [("mes", "int64", "0", True), {"nombre": "nombre_mes", "tipo": "string", "sort_by": "mes"}],
                       "let\n    Meses = #table(type table [mes = Int64.Type, nombre_mes = text], {\n"
                       + ",\n".join(f'        {{{i + 1}, "{n}"}}' for i, n in enumerate(meses)) + "\n    })\nin\n    Meses"),
        "Proyeccion": tabla("Proyeccion", [("inst_id", "int64", "0", True), ("vig_corte", "double", "#,0", False, "sum"),
                                           ("ejec_corte", "double", "#,0", False, "sum"), ("proy_patron", "double", "#,0", False, "sum"),
                                           ("avance_tipico", "double", PCT), ("pct_proy", "double", PCT), ("alerta", "boolean"),
                                           ("sin_ejecutar_proy", "double", "#,0", False, "sum")],
                            m_csv("proyeccion.csv", [("inst_id", I), ("vig_corte", N), ("ejec_corte", N), ("proy_patron", N),
                                                     ("avance_tipico", N), ("pct_proy", N), ("alerta", "type logical"),
                                                     ("sin_ejecutar_proy", N)])),
        "Alertas": tabla("Alertas", [("inst_id", "int64", "0", True), ("vig_corte", "double", "#,0", False, "sum"),
                                     ("ejec_corte", "double", "#,0", False, "sum"), ("pct_proy", "double", PCT),
                                     ("sin_ejecutar_proy", "double", "#,0", False, "sum"), ("años_previos", "string")],
                         m_csv("alertas.csv", [("inst_id", I), ("vig_corte", N), ("ejec_corte", N), ("pct_proy", N),
                                               ("sin_ejecutar_proy", N), ("años_previos", X)])),
        "Validacion": tabla("Validacion", [("año", "int64", "0"), ("instituciones", "int64", "#,0", False, "sum"),
                                           ("cerraron_bajo", "int64", "#,0", False, "sum"), ("alertas", "int64", "#,0", False, "sum"),
                                           ("aciertos", "int64", "#,0", False, "sum"), ("falsas", "int64", "#,0", False, "sum"),
                                           ("no_detectadas", "int64", "#,0", False, "sum"), ("error_pp", "double", "0.0", False, "sum"),
                                           ("error_lineal_pp", "double", "0.0", False, "sum"), ("atipico", "boolean"),
                                           ("pandemia", "string")],
                            m_csv("validacion.csv", [("año", I), ("instituciones", I), ("cerraron_bajo", I), ("alertas", I),
                                                     ("aciertos", I), ("falsas", I), ("no_detectadas", I), ("error_pp", N),
                                                     ("error_lineal_pp", N), ("atipico", "type logical"),
                                                     ("pandemia", X)])),
        "Brecha": tabla("Brecha", [("año", "int64", "0", True), ("oficial", "double", PCT), ("corte", "double", PCT),
                                   {"nombre": "periodo", "tipo": "string", "sort_by": "año"}],
                        m_csv("brecha.csv", [("año", I), ("oficial", N), ("corte", N), ("periodo", X)])),
        "Medidas": tabla("Medidas", [("x", "string", None, True)], 'let\n    Vacia = #table(type table [x = text], {})\nin\n    Vacia',
                         medidas="".join(medida(*m) for m in MEDIDAS)),
    }
    for n, txt in tb.items():
        (defn / "tables" / f"{n}.tmdl").write_text(txt, encoding="utf-8")
    rels = [("Hechos.inst_id", "Instituciones.inst_id"), ("Hechos.año", "Ejercicio.año"), ("Hechos.mes", "Meses.mes"),
            ("Proyeccion.inst_id", "Instituciones.inst_id"), ("Alertas.inst_id", "Instituciones.inst_id")]
    q = lambda ref: ".".join(f"'{p}'" if not p.isascii() else p for p in ref.split("."))
    (defn / "relationships.tmdl").write_text(
        "".join(f"relationship r{i}\n\tfromColumn: {q(a)}\n\ttoColumn: {q(b)}\n\n" for i, (a, b) in enumerate(rels, 1)),
        encoding="utf-8")


# ======================================================================
#  3. Informe (PBIR) - mismas convenciones que el proyecto Valdora
# ======================================================================
SCH = "https://developer.microsoft.com/json-schemas/fabric/item/report"
GRIS_AC, GRIS_PL, SUAVE = "#3A3A3A", "#B5B5B5", "#6B6B6B"
TEMA = {
    "name": "IBCS sobrio", "dataColors": [GRIS_AC, GRIS_PL, "#7A7A7A", "#D9D9D9", "#5A5A5A", "#9A9A9A", VERDE, ROJO],
    "background": "#FFFFFF", "foreground": TINTA, "tableAccent": GRIS_AC, "good": VERDE, "bad": ROJO, "neutral": GRIS_PL,
    "textClasses": {"title": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": TINTA},
                    "header": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": TINTA},
                    "label": {"fontFace": "Segoe UI", "fontSize": 10, "color": SUAVE},
                    "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 26, "color": TINTA}},
    "visualStyles": {"*": {"*": {"border": [{"show": False}], "dropShadow": [{"show": False}],
                                 "title": [{"fontSize": 12, "fontColor": {"solid": {"color": TINTA}}, "titleWrap": True}],
                                 "background": [{"show": False}]}}},
}


def lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def color(hexa):
    return {"solid": {"color": lit(f"'{hexa}'")}}


def campo(entidad, prop, medida=False, nombre=None):
    k = "Measure" if medida else "Column"
    d = {"field": {k: {"Expression": {"SourceRef": {"Entity": entidad}}, "Property": prop}},
         "queryRef": f"{entidad}.{prop}", "nativeQueryRef": prop}
    if nombre:
        d["displayName"] = nombre
    return d


def suma(entidad, prop, nombre=None):
    d = {"field": {"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Entity": entidad}},
                                                              "Property": prop}}, "Function": 0}},
         "queryRef": f"Sum({entidad}.{prop})", "nativeQueryRef": f"Suma de {prop}"}
    if nombre:
        d["displayName"] = nombre
    return d


def visual(nombre, tipo, pos, roles, title=None):
    v = {"$schema": f"{SCH}/definition/visualContainer/1.0.0/schema.json", "name": nombre,
         "position": {"x": pos[0], "y": pos[1], "z": 0, "width": pos[2], "height": pos[3]},
         "visual": {"visualType": tipo, "query": {"queryState": {r: {"projections": p} for r, p in roles.items()}},
                    "drillFilterOtherVisuals": True}}
    if title:
        v["visual"]["visualContainerObjects"] = {"title": [{"properties": {"show": lit("true"), "text": lit(f"'{title}'")}}]}
    return v


def texto(nombre, pos, parrafos):
    return {"$schema": f"{SCH}/definition/visualContainer/1.0.0/schema.json", "name": nombre,
            "position": {"x": pos[0], "y": pos[1], "z": 0, "width": pos[2], "height": pos[3]},
            "visual": {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": [
                {"textRuns": [{"value": t, "textStyle": {"fontSize": f"{tam}pt", "fontWeight": "bold" if neg else "normal",
                                                          "color": col, "fontFamily": "Segoe UI"}}]}
                for t, tam, neg, col in parrafos]}}]}}}


def tarjeta(nombre, medida, x, title, y=150, w=236):
    v = visual(nombre, "card", (x, y, w, 96), {"Values": [campo("Medidas", medida, True)]}, title)
    v["visual"]["objects"] = {"labels": [{"properties": {"labelDisplayUnits": lit("1D"), "fontSize": lit("22D")}}],
                              "categoryLabels": [{"properties": {"show": lit("false")}}]}
    return v


def colores_series(v, mapa):
    v["visual"].setdefault("objects", {})["dataPoint"] = [
        {"properties": {"fill": color(c)}, "selector": {"metadata": f"Medidas.{m}"}} for m, c in mapa.items()]
    return v


def color_condicional(v, medida_objetivo, medida_color):
    v["visual"].setdefault("objects", {}).setdefault("values", []).append({
        "properties": {"fontColor": {"solid": {"color": {"expr": {"Measure": {
            "Expression": {"SourceRef": {"Entity": "Medidas"}}, "Property": medida_color}}}}}},
        "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": f"Medidas.{medida_objetivo}"}})
    return v


def tabla_limpia(v, tam=9):
    o = v["visual"].setdefault("objects", {})
    o["grid"] = [{"properties": {"gridVertical": lit("false"), "gridHorizontal": lit("true"),
                                 "gridHorizontalColor": color("#E6E6E6"), "rowPadding": lit("2D"), "textSize": lit(f"{tam}D")}}]
    o["columnHeaders"] = [{"properties": {"fontSize": lit(f"{tam}D"), "bold": lit("true")}}]
    o.setdefault("values", []).insert(0, {"properties": {"fontSize": lit(f"{tam}D")}})
    return v


def ejes_limpios(v, tam_cat=9):
    o = v["visual"].setdefault("objects", {})
    o["categoryAxis"] = [{"properties": {"showAxisTitle": lit("false"), "fontSize": lit(f"{tam_cat}D")}}]
    o["valueAxis"] = [{"properties": {"showAxisTitle": lit("false"), "labelDisplayUnits": lit("1D"), "fontSize": lit("9D")}}]
    return v


def sin_encabezado(v):
    v["visual"].setdefault("objects", {})["header"] = [{"properties": {"show": lit("false")}}]
    return v


def desplegable(v):
    v["visual"].setdefault("objects", {})["data"] = [{"properties": {"mode": lit("'Dropdown'")}}]
    return sin_encabezado(v)


def ordenar(v, entidad, prop, medida=True, agregado=False):
    """Orden descendente por defecto (por una medida o por la suma de una columna)."""
    col = {"Expression": {"SourceRef": {"Entity": entidad}}, "Property": prop}
    f = ({"Measure": col} if medida else
         {"Aggregation": {"Expression": {"Column": col}, "Function": 0}} if agregado else {"Column": col})
    v["visual"]["query"]["sortDefinition"] = {"sort": [{"field": f, "direction": "Descending"}], "isDefaultSort": True}
    return v


def sin_totales(v):
    v["visual"].setdefault("objects", {})["total"] = [{"properties": {"totals": lit("false")}}]
    return v


def paginas(año, corte, n_alertas, riesgo):
    mes = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"][corte - 1]
    n = lambda v, dec=1: f"{v:,.{dec}f}".replace(",", "§").replace(".", ",").replace("§", ".")
    try:
        txt = (BASE / "docs" / "data.js").read_text(encoding="utf-8")
        titular = json.loads(txt[txt.index("=") + 1:].strip().rstrip(";"))["titular"]
    except Exception:  # noqa: BLE001
        titular = f"A {mes}, {n_alertas} instituciones van camino a dejar sin gastar ${n(riesgo / 1e6)} billones."
    encabezado = lambda pag: texto("encabezado", (20, 4, 1000, 66), [
        (f"GOBIERNO CENTRAL DE CHILE · DATOS ABIERTOS DIPRES · {pag.upper()}", 9, True, SUAVE),
        (f"Ejecución presupuestaria {año} · datos a {mes} · millones de pesos", 16, True, TINTA)])
    slicer = lambda: desplegable(visual("slicerMin", "slicer", (1000, 8, 260, 62),
                                        {"Values": [campo("Instituciones", "ministerio")]}, "Ministerio"))

    curva = ejes_limpios(colores_series(visual("gCurva", "lineChart", (20, 260, 500, 450),
        {"Category": [campo("Meses", "nombre_mes", nombre="Mes")],
         "Y": [campo("Medidas", "Curva Año en Curso %", True, str(año)),
               campo("Medidas", "Curva Proyección %", True, "Proyección"),
               campo("Medidas", "Curva Mediana Años Anteriores %", True, "Mediana años anteriores")]},
        f"Gasto acumulado como % del presupuesto vigente a {mes}"),
        {"Curva Año en Curso %": GRIS_AC, "Curva Proyección %": "#7A7A7A", "Curva Mediana Años Anteriores %": GRIS_PL}))
    curva["visual"]["objects"]["lineStyles"] = [
        {"properties": {"lineStyle": lit("'dashed'")}, "selector": {"metadata": "Medidas.Curva Proyección %"}},
        {"properties": {"lineStyle": lit("'dotted'")}, "selector": {"metadata": "Medidas.Curva Mediana Años Anteriores %"}}]

    tmin = tabla_limpia(color_condicional(sin_totales(visual("tMin", "tableEx", (540, 260, 720, 450),
        {"Values": [campo("Instituciones", "ministerio_corto", nombre="Ministerio"),
                    campo("Medidas", "Vigente (bn)", True, "Vigente"), campo("Medidas", "% Ejecutado", True, f"Ejecutado a {mes[:3]}"),
                    campo("Medidas", "Cierre Proyectado %", True, "Cierre proy."),
                    campo("Medidas", "Instituciones en Alerta", True, "Alertas"),
                    campo("Medidas", "Monto en Riesgo", True, "Monto en riesgo")]},
        "Ejecución por ministerio")), "Cierre Proyectado %", "Color Cierre"), tam=8)
    ordenar(tmin, "Medidas", "Vigente (bn)")

    t_alertas = tabla_limpia(visual("tAlertas", "tableEx", (20, 130, 900, 580),
        {"Values": [campo("Instituciones", "institucion", nombre="Institución"),
                    suma("Alertas", "vig_corte", "Vigente MM$"),
                    campo("Medidas", "% Ejecutado Alerta", True, "Ejecutado"),
                    campo("Medidas", "Cierre Proyectado Alerta %", True, "Cierre proy."),
                    suma("Alertas", "sin_ejecutar_proy", "Sin gastar MM$"),
                    campo("Alertas", "años_previos", nombre="Años previos")]},
        "Instituciones con cierre proyectado bajo 90% del presupuesto vigente"), tam=8)
    ordenar(t_alertas, "Alertas", "sin_ejecutar_proy", medida=False, agregado=True)
    t_alertas["visual"]["objects"]["values"].append({
        "properties": {"fontColor": color(ROJO)}, "selector": {"metadata": "Medidas.Cierre Proyectado Alerta %"}})
    riesgo_min = ejes_limpios(colores_series(visual("gRiesgo", "clusteredBarChart", (940, 130, 320, 580),
        {"Category": [campo("Instituciones", "ministerio_corto")], "Y": [campo("Medidas", "Monto en Riesgo", True)]},
        "Monto en riesgo por ministerio (MM$)"), {"Monto en Riesgo": ROJO}), tam_cat=8)
    ordenar(riesgo_min, "Medidas", "Monto en Riesgo")
    riesgo_min["visual"]["objects"]["labels"] = [{"properties": {"show": lit("true"), "fontSize": lit("8D")}}]

    t_val = tabla_limpia(sin_totales(visual("tVal", "tableEx", (20, 200, 600, 390),
        {"Values": [campo("Validacion", "año", nombre="Año"), suma("Validacion", "cerraron_bajo", "Cerraron bajo 90%"),
                    suma("Validacion", "alertas", "Alertas"), suma("Validacion", "aciertos", "Aciertos"),
                    suma("Validacion", "falsas", "Falsas alarmas"), suma("Validacion", "no_detectadas", "No detectadas"),
                    suma("Validacion", "error_pp", "Error (pp)"), campo("Validacion", "pandemia", nombre="Pandemia")]},
        f"Proyección hecha cada año en {mes} con los años anteriores, contra el cierre real")))
    brecha = ejes_limpios(colores_series(visual("gBrecha", "clusteredColumnChart", (660, 200, 600, 390),
        {"Category": [campo("Brecha", "periodo", nombre="Año")],
         "Y": [campo("Medidas", "% Bajo 90% (oficial)", True, "vs vigente final (oficial)"),
               campo("Medidas", "% Bajo 90% (al corte)", True, f"vs presupuesto que tenía en {mes}")]},
        "Instituciones que cerraron bajo 90%: depende contra qué presupuesto se mida"),
        {"% Bajo 90% (oficial)": GRIS_PL, "% Bajo 90% (al corte)": GRIS_AC}))

    return {
        "resumen": ("Resumen", [
            encabezado("Resumen"), slicer(),
            texto("mensaje", (20, 72, 1240, 70), [(titular, 13, True, TINTA)]),
            tarjeta("kVig", "Vigente (bn)", 20, "Presupuesto vigente"),
            tarjeta("kEjec", "% Ejecutado", 268, f"Ejecutado a {mes}"),
            tarjeta("kProy", "Cierre Proyectado %", 516, "Cierre proyectado"),
            tarjeta("kAl", "Instituciones en Alerta", 764, "Instituciones en alerta"),
            tarjeta("kRiesgo", "Monto en Riesgo (bn)", 1012, "Monto en riesgo"),
            curva, tmin,
        ]),
        "alertas": ("Alertas", [
            encabezado("Instituciones en alerta"), slicer(),
            texto("explica", (20, 70, 960, 50), [(
                "Cierre proyectado = ejecutado al corte ÷ avance típico de cada institución a esa fecha en sus últimos 3 años. "
                "Alerta si queda bajo 90% del presupuesto vigente al corte. Años previos: % ejecutado al cierre de los 3 años anteriores.",
                10, False, SUAVE)]),
            t_alertas, riesgo_min,
        ]),
        "validacion": ("Validación", [
            encabezado("¿La proyección acierta?"),
            tarjeta("kCob", "Cobertura", 20, "Casos detectados (años normales)", y=80, w=300),
            tarjeta("kPrec", "Precisión", 340, "Alertas correctas (años normales)", y=80, w=300),
            tarjeta("kErr", "Error Típico (pp)", 660, "Error típico del cierre (pp)", y=80, w=300),
            t_val, brecha,
            texto("nota", (20, 610, 1240, 80), [(
                "Regla simple de comparación (alertar a quien ya cerró bajo 90% el año anterior): detecta menos casos y con menor "
                "precisión. 2020 y 2021 (pandemia) se muestran pero no entran en el resumen. La cifra oficial (~99% de ejecución) "
                "se mide contra el presupuesto final, que se recorta después del corte a quienes no alcanzan a gastar.",
                10, False, SUAVE)]),
        ]),
    }


def generar_informe(pags):
    d = RP / "definition"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    (RP / "definition.pbir").write_text(json.dumps({
        "$schema": f"{SCH}/definitionProperties/2.0.0/schema.json", "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{NOMBRE}.SemanticModel"}}}, indent=2), encoding="utf-8")
    (d / "version.json").write_text(json.dumps({"$schema": f"{SCH}/definition/versionMetadata/1.0.0/schema.json",
                                                "version": "2.0.0"}, indent=2), encoding="utf-8")
    (d / "report.json").write_text(json.dumps({
        "$schema": f"{SCH}/definition/report/1.0.0/schema.json",
        "themeCollection": {"baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.61", "type": "SharedResources"},
                            "customTheme": {"name": "IBCS.json", "reportVersionAtImport": "5.61", "type": "RegisteredResources"}},
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                              "items": [{"name": "IBCS.json", "path": "IBCS.json", "type": "CustomTheme"}]}],
        "layoutOptimization": "None"}, indent=2), encoding="utf-8")
    rr = RP / "StaticResources" / "RegisteredResources"
    rr.mkdir(parents=True, exist_ok=True)
    (rr / "IBCS.json").write_text(json.dumps(TEMA, indent=2, ensure_ascii=False), encoding="utf-8")
    (d / "pages" / "pages.json").write_text(json.dumps({
        "$schema": f"{SCH}/definition/pagesMetadata/1.0.0/schema.json", "pageOrder": list(pags),
        "activePageName": "resumen"}, indent=2), encoding="utf-8")
    for pid, (nom, visuales) in pags.items():
        p = d / "pages" / pid
        (p / "visuals").mkdir(parents=True, exist_ok=True)
        (p / "page.json").write_text(json.dumps({
            "$schema": f"{SCH}/definition/page/1.0.0/schema.json", "name": pid, "displayName": nom,
            "displayOption": "FitToPage", "height": 720, "width": 1280}, indent=2, ensure_ascii=False), encoding="utf-8")
        for v in visuales:
            (p / "visuals" / v["name"]).mkdir(exist_ok=True)
            (p / "visuals" / v["name"] / "visual.json").write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    año, corte, n_al, riesgo = exportar_csv()
    for carpeta in (SM, RP):
        if carpeta.exists():
            shutil.rmtree(carpeta)
    generar_modelo()
    pags = paginas(año, corte, n_al, riesgo)
    generar_informe(pags)
    (PBI / f"{NOMBRE}.pbip").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": f"{NOMBRE}.Report"}}],
        "settings": {"enableAutoRecovery": True}}, indent=2), encoding="utf-8")
    print(f"Proyecto listo: {PBI / (NOMBRE + '.pbip')} ({len(MEDIDAS)} medidas DAX, "
          f"{sum(len(v) for _, v in pags.values())} visuales en {len(pags)} páginas)")


if __name__ == "__main__":
    main()
