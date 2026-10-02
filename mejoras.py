"""Fase 4 - Modelo de riesgo de subejecución, medido con una regla estricta: afinar con unos años y probar en otros.

Uso:  python mejoras.py   ->  resultados/mejoras.json, resultados/sensibilidad.csv, datos/riesgo.csv

Años de ajuste (se pueden mirar y afinar):   2019, 2020, 2021 y 2022
Años de prueba (NO se usan para elegir nada): 2023, 2024 y 2025
Cada año se evalúa usando solo información de años anteriores.

Se compara, en los años de prueba:
  1. Regla simple:        alertar a quien cerró bajo 90% el año anterior
  2. Proyección simple:   alertar si (ejecutado ÷ avance típico) queda bajo 90%   [modelo de la fase 2]
  3. Modelo de riesgo:    regresión logística que combina la proyección con el nivel de ejecución al corte, el historial
                          propio, el ritmo de los últimos 3 meses y el peso de la inversión. Entrega una probabilidad.

Qué se probó y NO ayudó (se informa igual):
  - proyectar cada tipo de gasto por separado (personal, bienes y servicios, inversión...)
  - ignorar 2020 y 2021 al calcular el avance típico
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

import analisis as A

BASE = Path(__file__).parent
OUT = BASE / "resultados"
K = A.K
AÑOS_AJUSTE = [2019, 2020, 2021, 2022]
AÑOS_PRUEBA = [2023, 2024, 2025]
PANDEMIA = A.AÑOS_ATIPICOS
INVERSION = [29, 31, 33]
TIPOS = {21: "Personal", 22: "Bienes y servicios", 23: "Prestaciones sociales", 24: "Transferencias corrientes",
         26: "Otros corrientes", 29: "Activos no financieros", 31: "Inversión", 33: "Transferencias de capital"}
VARIABLES = ["pct_proy", "pct_ejec", "pf1", "pfmin", "ritmo", "sh_inv", "lvig", "tiene_hist"]
NOMBRES_VAR = {"pct_proy": "Cierre proyectado (% del vigente)", "pct_ejec": "Ejecutado a la fecha (% del vigente)",
               "pf1": "Cierre del año anterior", "pfmin": "Peor cierre de los últimos 3 años",
               "ritmo": "Ritmo de los últimos 3 meses", "sh_inv": "Peso de la inversión en el presupuesto",
               "lvig": "Tamaño del presupuesto", "tiene_hist": "Tiene historia propia"}
LAMBDA = 1.0   # regularización fija (no se afinó)
M3 = 3         # meses para medir el ritmo reciente


# ------------------------------------------------------------------
#  Datos base
# ------------------------------------------------------------------
class Datos:
    """Paneles anuales por institución y tipo de gasto, y ritmo reciente."""

    def __init__(self, corte: int):
        self.corte = corte
        con = sqlite3.connect(A.DB)
        h = pd.read_sql("SELECT año, mes, partida, capitulo, subtitulo, ejec_acum, ley_vigente FROM hechos", con)
        con.close()
        c = h[h.mes == corte].rename(columns={"ejec_acum": "ejec_corte", "ley_vigente": "vig_corte"}).drop(columns="mes")
        f = h[h.mes == 12].rename(columns={"ejec_acum": "ejec_final", "ley_vigente": "vig_final"}).drop(columns="mes")
        self.p = c.merge(f, on=["año"] + K + ["subtitulo"], how="outer").fillna(0.0)
        ac = h.groupby(["año", "mes"] + K).ejec_acum.sum().reset_index()
        a1 = ac[ac.mes == corte].rename(columns={"ejec_acum": "e_c"}).drop(columns="mes")
        a0 = ac[ac.mes == corte - M3].rename(columns={"ejec_acum": "e_c0"}).drop(columns="mes")
        r = a1.merge(a0, on=["año"] + K, how="left").fillna(0.0)
        r["ritmo"] = (r.e_c - r.e_c0) / r.e_c.replace(0, np.nan)
        self.ritmo = r[["año"] + K + ["ritmo"]]
        inv = h[(h.mes == corte) & h.subtitulo.isin(INVERSION)].groupby(["año"] + K).ley_vigente.sum()
        self.inversion = inv.rename("v_inv").reset_index()


def avance_tipico(hist: pd.DataFrame, claves: list, k: int, sin_pandemia: bool = False) -> pd.Series:
    """Mediana, en los últimos k años, de (ejecutado al corte ÷ ejecutado al cierre) por grupo."""
    h = hist[~hist.año.isin(PANDEMIA)] if sin_pandemia else hist
    h = h[h.año.isin(sorted(h.año.unique())[-k:])]
    g = h.groupby(claves + ["año"])[["ejec_corte", "ejec_final"]].sum()
    g = g[g.ejec_final > 0]
    return (g.ejec_corte / g.ejec_final).groupby(claves).median()


def proyectar_año(d: Datos, año: int, variante: str = "base", k: int = 3):
    """Proyección de cierre por institución para `año` con información de años anteriores.
    Devuelve (por institución, por institución x tipo de gasto)."""
    hist, hoy = d.p[d.p.año < año], d.p[d.p.año == año]
    sin_p = variante != "base"
    nac = avance_tipico(hist.assign(g=1), ["g"], k, sin_p).iloc[0]
    if variante in ("base", "limpia"):
        propio = avance_tipico(hist, K, k, sin_p).rename("av")
        i = hoy.groupby(K)[["ejec_corte", "vig_corte"]].sum().reset_index().merge(propio, on=K, how="left")
        i["av"] = i["av"].fillna(nac)
        i["proy"] = i.ejec_corte / i.av.clip(lower=0.05)
        return i, None
    nac_t = avance_tipico(hist, ["subtitulo"], k, True).rename("av_nac")
    propio = avance_tipico(hist, K + ["subtitulo"], k, True).rename("av_prop")
    t = hoy.merge(propio, on=K + ["subtitulo"], how="left").merge(nac_t, on="subtitulo", how="left")
    t["av"] = t.av_prop.fillna(t.av_nac).fillna(nac)
    t["proy_t"] = t.ejec_corte / t.av.clip(lower=0.05)
    i = t.groupby(K).agg(ejec_corte=("ejec_corte", "sum"), vig_corte=("vig_corte", "sum"), proy=("proy_t", "sum")).reset_index()
    return i, t


# ------------------------------------------------------------------
#  Variables del modelo
# ------------------------------------------------------------------
def variables(d: Datos, año: int, k: int = 3) -> pd.DataFrame:
    """Una fila por institución con las señales disponibles al corte de `año` (sin mirar el futuro)."""
    base, _ = proyectar_año(d, año, "base", k)
    real = d.p[d.p.año == año].groupby(K).ejec_final.sum().reset_index()
    x = base.merge(real, on=K, how="left")
    x = x[(x.vig_corte > A.MIN_PRESUPUESTO) & (x.ejec_corte > 0)].copy()
    x["pct_proy"], x["pct_ejec"], x["pct_real"] = x.proy / x.vig_corte, x.ejec_corte / x.vig_corte, x.ejec_final / x.vig_corte
    prev = d.p[(d.p.año < año) & ~d.p.año.isin(PANDEMIA)].groupby(K + ["año"])[["ejec_final", "vig_corte"]].sum().reset_index()
    prev["pf"] = prev.ejec_final / prev.vig_corte.replace(0, np.nan)
    prev = prev.sort_values("año")
    x = x.merge(prev.groupby(K).pf.last().rename("pf1"), on=K, how="left")
    x = x.merge(prev.groupby(K).pf.apply(lambda s: s.tail(3).min()).rename("pfmin"), on=K, how="left")
    x = x.merge(d.ritmo[d.ritmo.año == año][K + ["ritmo"]], on=K, how="left")
    x = x.merge(d.inversion[d.inversion.año == año][K + ["v_inv"]], on=K, how="left")
    x["sh_inv"] = x.v_inv.fillna(0) / x.vig_corte
    x["tiene_hist"] = x.pf1.notna().astype(float)
    x["pf1"], x["pfmin"] = x.pf1.fillna(1.0), x.pfmin.fillna(1.0)
    x["ritmo"] = x.ritmo.fillna(x.ritmo.median())
    x["lvig"] = np.log(x.vig_corte)
    x["año"] = año
    return x


def panel_variables(d: Datos, años: list, k: int = 3) -> pd.DataFrame:
    return pd.concat([variables(d, a, k) for a in años], ignore_index=True)


# ------------------------------------------------------------------
#  Regresión logística (numpy, con regularización)
# ------------------------------------------------------------------
class Logistica:
    def ajustar(self, X: pd.DataFrame, y: np.ndarray, lam: float = LAMBDA, iteraciones: int = 50):
        self.mu, self.sd = X.mean(axis=0), X.std(axis=0).replace(0, 1)
        Z = np.c_[np.ones(len(X)), (X - self.mu) / self.sd]
        w, pen = np.zeros(Z.shape[1]), lam * np.diag(np.r_[0, np.ones(Z.shape[1] - 1)])
        for _ in range(iteraciones):
            pr = 1 / (1 + np.exp(-Z @ w))
            w -= np.linalg.solve((Z.T * (pr * (1 - pr))) @ Z + pen, Z.T @ (pr - y) + pen @ w)
        self.w = w
        return self

    def probabilidad(self, X: pd.DataFrame) -> np.ndarray:
        return 1 / (1 + np.exp(-(np.c_[np.ones(len(X)), (X - self.mu) / self.sd] @ self.w)))


def auc(y: np.ndarray, s: np.ndarray) -> float:
    r = pd.Series(s).rank().values
    n1, n0 = (y == 1).sum(), (y == 0).sum()
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def metricas(y: np.ndarray, alerta: np.ndarray) -> dict:
    vp = int((alerta & (y == 1)).sum())
    pr, co = vp / max(alerta.sum(), 1), vp / max((y == 1).sum(), 1)
    return {"alertas": int(alerta.sum()), "casos": int((y == 1).sum()), "aciertos": vp,
            "precision": round(pr * 100, 1), "cobertura": round(co * 100, 1),
            "f1": round(2 * pr * co / max(pr + co, 1e-9) * 100, 1)}


def umbral_probabilidad(X: pd.DataFrame, y: np.ndarray, años: np.ndarray, anios_unicos: list) -> float:
    """Umbral de probabilidad que maximiza F1, elegido dejando un año fuera cada vez (sin mirar el año evaluado)."""
    oof = np.zeros(len(X))
    for a in anios_unicos:
        tr = años != a
        oof[~tr] = Logistica().ajustar(X[tr], y[tr]).probabilidad(X[~tr])
    mejor = max(((metricas(y, oof > t)["f1"], t) for t in np.arange(0.15, 0.80, 0.01)))
    return round(float(mejor[1]), 2)


# ------------------------------------------------------------------
#  Evaluación completa en un conjunto de años de prueba
# ------------------------------------------------------------------
def evaluar_modelos(d: Datos, umbral: float = A.UMBRAL_SUBEJECUCION, k: int = 3, lam: float = LAMBDA,
                    ajuste=AÑOS_AJUSTE, prueba=AÑOS_PRUEBA) -> dict:
    X = panel_variables(d, ajuste + prueba, k)
    X["y"] = (X.pct_real < umbral).astype(float)
    aj, te = X[X.año.isin(ajuste)], X[X.año.isin(prueba)]
    t_prob = umbral_probabilidad(aj[VARIABLES], aj.y.values, aj.año.values, ajuste)
    mod = Logistica().ajustar(aj[VARIABLES], aj.y.values, lam)
    p_te = mod.probabilidad(te[VARIABLES])
    y = te.y.values
    # regla simple: cerró bajo el umbral el año anterior
    prev = d.p.groupby(K + ["año"])[["ejec_final", "vig_corte"]].sum().reset_index()
    prev = prev.assign(pp=prev.ejec_final / prev.vig_corte.replace(0, np.nan), año=prev.año + 1)[K + ["año", "pp"]]
    reg = te.merge(prev, on=K + ["año"], how="left").pp.lt(umbral).values
    proy = (te.pct_proy < umbral).values
    n_ref = int(proy.sum())
    top = lambda s: np.isin(np.arange(len(s)), np.argsort(-s)[:n_ref])
    curva = {}
    for n in (30, 50, 100, 150, 200):
        hp, hm = int(y[np.argsort(te.pct_proy.values)[:n]].sum()), int(y[np.argsort(-p_te)[:n]].sum())
        curva[n] = {"proyeccion": hp, "modelo": hm}
    bins = [(0, t_prob, "Bajo el umbral"), (t_prob, 0.5, "Riesgo moderado"), (0.5, 0.8, "Riesgo alto"), (0.8, 1.01, "Riesgo muy alto")]
    calibracion = []
    for lo, hi, nombre in bins:
        s = (p_te >= lo) & (p_te < hi)
        calibracion.append({"tramo": nombre, "desde": round(lo, 2), "hasta": round(min(hi, 1.0), 2), "instituciones": int(s.sum()),
                            "prob_media": round(float(p_te[s].mean() * 100), 1) if s.any() else None,
                            "cerraron_bajo": round(float(y[s].mean() * 100), 1) if s.any() else None})
    por_año = {}
    for a in prueba:
        m = (te.año == a).values
        por_año[int(a)] = {"regla": metricas(y[m], reg[m]), "proyeccion": metricas(y[m], proy[m]),
                           "modelo": metricas(y[m], p_te[m] > t_prob)}
    return {
        "umbral_subejecucion": umbral, "k": k, "umbral_probabilidad": t_prob, "años_ajuste": ajuste, "años_prueba": prueba,
        "casos_prueba": int(y.sum()), "instituciones_prueba": int(len(y)),
        "regla": metricas(y, reg), "proyeccion": metricas(y, proy), "modelo": metricas(y, p_te > t_prob),
        "mismas_alertas": {"n": n_ref, "proyeccion": metricas(y, proy), "modelo": metricas(y, top(p_te))},
        "auc": {"proyeccion": round(auc(y, -te.pct_proy.values), 3), "modelo": round(auc(y, p_te), 3)},
        "por_año": por_año, "curva": curva, "calibracion": calibracion,
        "auc_por_año": {int(a): {"proyeccion": round(auc(y[(te.año == a).values], -te.pct_proy.values[(te.año == a).values]), 3),
                                 "modelo": round(auc(y[(te.año == a).values], p_te[(te.año == a).values]), 3)} for a in prueba},
        "coeficientes": {NOMBRES_VAR[v]: round(float(c), 2) for v, c in zip(VARIABLES, mod.w[1:])},
        "_te": te.assign(prob=p_te), "_mod": mod, "_tprob": t_prob,
    }


def pruebas_que_no_ayudaron(d: Datos) -> list:
    """Variantes de proyección probadas en los años de ajuste: ninguna le gana a la base."""
    filas = []
    for var in ["base", "limpia", "tipo"]:
        for aj in [AÑOS_AJUSTE, AÑOS_PRUEBA]:
            ys, ps, errs = [], [], []
            for a in aj:
                i, _ = proyectar_año(d, a, var, 3)
                real = d.p[d.p.año == a].groupby(K).ejec_final.sum().reset_index()
                i = i.merge(real, on=K)
                i = i[(i.vig_corte > A.MIN_PRESUPUESTO) & (i.ejec_corte > 0)]
                ys.append(i.ejec_final / i.vig_corte < A.UMBRAL_SUBEJECUCION)
                ps.append(i.proy / i.vig_corte)
                errs.append((i.proy - i.ejec_final).abs() / i.vig_corte)
            y, p = pd.concat(ys).values, pd.concat(ps).values
            filas.append({"variante": {"base": "Proyección simple", "limpia": "Ignorando 2020-2021 en la historia",
                                       "tipo": "Por tipo de gasto"}[var],
                          "tramo": "ajuste" if aj is AÑOS_AJUSTE else "prueba",
                          "error_pp": round(float(pd.concat(errs).median() * 100), 1), "auc": round(auc(y.astype(int), -p), 3)})
    return filas


def sensibilidad(d: Datos) -> pd.DataFrame:
    """Qué pasa con el resultado en los años de prueba si se cambian las decisiones tomadas."""
    filas = []
    for etiqueta, kw in [("Base: 3 años de historia, umbral 90%", {}),
                         ("2 años de historia", {"k": 2}), ("4 años de historia", {"k": 4}), ("5 años de historia", {"k": 5}),
                         ("Umbral de subejecución 85%", {"umbral": 0.85}), ("Umbral de subejecución 95%", {"umbral": 0.95}),
                         ("Regularización más fuerte (x5)", {"lam": 5.0}), ("Sin regularización", {"lam": 0.001})]:
        r = evaluar_modelos(d, **kw)
        filas.append({"escenario": etiqueta, "casos": r["casos_prueba"],
                      "AUC proyección": r["auc"]["proyeccion"], "AUC modelo": r["auc"]["modelo"],
                      "Cobertura proyección %": r["proyeccion"]["cobertura"], "Precisión proyección %": r["proyeccion"]["precision"],
                      "Cobertura modelo %": r["modelo"]["cobertura"], "Precisión modelo %": r["modelo"]["precision"]})
    return pd.DataFrame(filas)


# ------------------------------------------------------------------
#  Año en curso: riesgo y causa del atraso
# ------------------------------------------------------------------
def riesgo_actual(d: Datos, año: int, ev: dict) -> pd.DataFrame:
    """Probabilidad de subejecución de cada institución en el año en curso y qué tipo de gasto explica el atraso.
    El modelo se vuelve a ajustar con todos los años cerrados (2019-2025); el umbral es el elegido solo con los años de ajuste."""
    cerrados = list(range(2019, año))
    X = panel_variables(d, cerrados)
    X["y"] = (X.pct_real < A.UMBRAL_SUBEJECUCION).astype(float)
    mod = Logistica().ajustar(X[VARIABLES], X.y.values)
    x = variables(d, año)
    x["prob"] = mod.probabilidad(x[VARIABLES])
    x["alerta"] = x.prob > ev["_tprob"]
    x["nivel"] = pd.cut(x.prob, [-1, ev["_tprob"], 0.5, 0.8, 2], labels=["Bajo", "Moderado", "Alto", "Muy alto"]).astype(str)
    x["sin_ejecutar"] = (x.vig_corte - x.proy).clip(lower=0)
    # causa: qué tipo de gasto explica el atraso proyectado (cada tipo contra su propio ritmo histórico)
    _, t = proyectar_año(d, año, "tipo")
    t = t[t.vig_corte > 0].copy()
    t["falta"] = (t.vig_corte - t.proy_t).clip(lower=0)
    t["tipo"] = t.subtitulo.map(TIPOS)
    tot = t.groupby(K).falta.transform("sum")
    t["peso"] = np.where(tot > 0, t.falta / tot, 0.0)
    t["pct_ejec_tipo"] = t.ejec_corte / t.vig_corte
    top = (t.sort_values("falta", ascending=False).groupby(K).head(2)
           .assign(orden=lambda z: z.groupby(K).cumcount() + 1))
    def frase(g):
        return " · ".join(f"{r.tipo} ({r.peso * 100:.0f}% del atraso, ejecutado {r.pct_ejec_tipo * 100:.0f}%)" for r in g.itertuples())
    causa = top.groupby(K).apply(frase, include_groups=False).rename("causa").reset_index()
    return x.merge(causa, on=K, how="left")


# ------------------------------------------------------------------
def main():
    OUT.mkdir(exist_ok=True)
    m, inst, _ = A.cargar()
    año, corte = A.corte_actual(m)
    d = Datos(corte)
    print(f"Corte: {A.MESES[corte - 1]} · ajuste {AÑOS_AJUSTE} · prueba {AÑOS_PRUEBA} (no se tocan al ajustar)\n")

    print("1) Variantes de proyección que NO ayudaron:")
    ni = pruebas_que_no_ayudaron(d)
    print(pd.DataFrame(ni).to_string(index=False), "\n")

    ev = evaluar_modelos(d)
    print(f"2) Años de prueba {AÑOS_PRUEBA}: {ev['instituciones_prueba']} instituciones, {ev['casos_prueba']} cerraron bajo 90%")
    print(f"   Umbral de probabilidad (elegido solo con años de ajuste): {ev['umbral_probabilidad']}")
    for nombre in ("regla", "proyeccion", "modelo"):
        print(f"   {nombre:11s}", ev[nombre])
    print("   Con el mismo nº de alertas que la proyección:", ev["mismas_alertas"]["modelo"])
    print("   AUC:", ev["auc"], ev["auc_por_año"])
    print("   Aciertos según nº de alertas (proyección vs modelo):", ev["curva"])
    print("   Calibración:")
    for c in ev["calibracion"]:
        print("     ", c)
    print("   Coeficientes:", ev["coeficientes"], "\n")

    print("3) Sensibilidad (años de prueba):")
    sens = sensibilidad(d)
    print(sens.round(3).to_string(index=False), "\n")
    sens.to_csv(OUT / "sensibilidad.csv", index=False)

    r = riesgo_actual(d, año, ev)
    inst2 = inst.copy()
    r = r.merge(inst2, on=K, how="left")
    r.loc[~r.alerta, "causa"] = ""
    r.insert(0, "inst_id", r.partida * 1000 + r.capitulo)
    cols = ["inst_id", "partida", "capitulo", "ministerio", "institucion", "vig_corte", "ejec_corte", "pct_ejec", "pct_proy",
            "prob", "alerta", "nivel", "sin_ejecutar", "causa", "pf1", "ritmo", "sh_inv"]
    (BASE / "datos").mkdir(exist_ok=True)
    r[cols].to_csv(BASE / "datos" / "riesgo.csv", index=False)
    al = r[r.alerta].sort_values("sin_ejecutar", ascending=False)
    print(f"4) {año}: {len(al)} instituciones en alerta (modelo) · ${al.sin_ejecutar.sum() / 1e6:.2f} billones sin ejecutar proyectados")
    print(al.head(8)[["institucion", "prob", "pct_proy", "sin_ejecutar", "causa"]].round(2).to_string(index=False))
    comp = pd.DataFrame([{"Método": n, **{k: ev[c][k] for k in ("alertas", "aciertos", "precision", "cobertura", "f1")}}
                         for n, c in (("Regla simple (cerró bajo 90% el año anterior)", "regla"),
                                      ("Proyección simple (fase 2)", "proyeccion"), ("Modelo de riesgo", "modelo"))])
    curva = pd.DataFrame([{"Alertas revisadas": n, "Casos encontrados · proyección": v["proyeccion"],
                           "Casos encontrados · modelo": v["modelo"], "Casos totales": ev["casos_prueba"]} for n, v in ev["curva"].items()])
    with pd.ExcelWriter(OUT / "informe_riesgo.xlsx") as xw:
        comp.to_excel(xw, sheet_name="Comparación (prueba)", index=False)
        curva.to_excel(xw, sheet_name="Alertas vs casos", index=False)
        pd.DataFrame(ev["calibracion"]).to_excel(xw, sheet_name="Calibración", index=False)
        pd.DataFrame(ni).to_excel(xw, sheet_name="Lo que no ayudó", index=False)
        sens.to_excel(xw, sheet_name="Sensibilidad", index=False)
        pd.DataFrame([{"Variable": k, "Coeficiente": v} for k, v in ev["coeficientes"].items()]).to_excel(
            xw, sheet_name="Coeficientes", index=False)
        al[["ministerio", "institucion", "nivel", "vig_corte", "pct_ejec", "pct_proy", "sin_ejecutar", "causa"]].to_excel(
            xw, sheet_name=f"Alertas {año}", index=False)
    pub = {k: v for k, v in ev.items() if not k.startswith("_")}
    pub["sensibilidad"] = sens.round(3).to_dict("records")
    pub["no_ayudaron"] = ni
    pub["actual"] = {"año": año, "alertas": int(len(al)), "sin_ejecutar_MM": round(float(al.sin_ejecutar.sum()), 0),
                     "instituciones": int(len(r))}
    (OUT / "mejoras.json").write_text(json.dumps(pub, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
