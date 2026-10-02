# ¿Quién no alcanzará a gastar su presupuesto? Ejecución presupuestaria del Estado de Chile

Seguimiento de la ejecución presupuestaria del **Gobierno Central de Chile** con datos **oficiales y públicos** de la
Dirección de Presupuestos (DIPRES), desde 2017 hasta agosto de 2026: 298 servicios públicos, mes a mes.

**[Ver el tablero web en vivo](https://tomasmoyaaraya.github.io/ejecucion-presupuestaria-chile/)** · [Informe en Excel](resultados/informe_riesgo.xlsx) · [Proyecto de Power BI](powerbi/) · [Guía para implementarlo en una empresa](GUIA_IMPLEMENTACION.md)

> Los datos son oficiales. El análisis, las proyecciones y las alertas son míos, no de la DIPRES.

![Tablero web](docs/img/tablero_web.png)

---

## De qué trata

Cada año el Congreso aprueba la **Ley de Presupuestos**: cuánto puede gastar cada ministerio y cada servicio público.
Durante el año ese presupuesto se va modificando, y cada mes la DIPRES publica cuánto lleva gastado cada institución.

Que una institución no gaste su presupuesto no es una buena noticia: suele significar obras que no avanzaron,
programas que no llegaron a la gente o compras que se atrasaron. Y como el año fiscal termina el 31 de diciembre,
lo que no se gastó a tiempo se pierde o se reasigna.

En el sector público, el control de gestión hace cada mes las mismas preguntas que en una empresa:

1. **¿Cuánto llevamos ejecutado** y es normal para esta fecha?
2. **¿Quién va atrasado y por qué?**
3. **¿Cómo vamos a cerrar el año?**

## Qué quería averiguar

> **En agosto, con cuatro meses por delante, ¿se puede anticipar qué instituciones no van a ejecutar su presupuesto?**

A diferencia de un proyecto con datos inventados, aquí la respuesta se puede comprobar con la realidad: tengo los
años 2017 a 2025 completos. Para cada año hice la predicción "como si estuviera en agosto de ese año", usando
**solo la información disponible en ese momento**, y después la comparé con lo que realmente pasó en diciembre.

Y para que la comparación sea justa, dividí los años en dos grupos:

- **Años de ajuste (2019 a 2022):** los que usé para probar ideas y elegir parámetros.
- **Años de prueba (2023 a 2025):** los que **no toqué** hasta tener el modelo terminado. Todos los resultados que
  muestro como "resultado" salen de aquí.

## Cómo lo hice, paso a paso

**1. Descargar los datos** (`descargar.py`).
El programa consulta el portal de datos abiertos del Estado (datos.gob.cl) y descarga, para cada mes desde 2017, dos
archivos oficiales: cuánto gastó cada servicio y cuál era su presupuesto (el aprobado al inicio y el vigente a esa
fecha). Son 232 archivos y no se necesita ninguna clave. Los archivos cambian de formato entre años (nombres de
columna, separadores, un `;` sobrante al final de algunas filas), así que el programa los normaliza.

**2. Ordenar los datos** (`modelo.py`, `sql/vistas.sql`).
Uní gasto y presupuesto en una base de datos, por servicio, tipo de gasto y mes. Para medir el gasto real de cada
servicio dejé fuera las operaciones financieras (compra de bonos, pago de deuda) y las cuentas del Tesoro Público, que
no son gasto de un servicio. Comprobé que los datos cuadran: la suma de los doce meses da exactamente el acumulado
anual que informa la DIPRES.

**3. Un primer método simple** (`analisis.py`).
Si una institución suele llevar el 55% de su gasto anual a fines de agosto y hoy lleva $100, se proyecta que cerrará
en $100 ÷ 0,55 = $182. El "suele" es la mediana de sus últimos tres años. Si la proyección queda bajo el 90% del
presupuesto, se marca una alerta. Este es el punto de partida contra el que se compara todo lo demás.

**4. Un modelo de riesgo** (`mejoras.py`).
Combina cinco señales en una regresión logística, que es una fórmula que pondera varias señales y entrega un puntaje de
riesgo:
- la proyección del método simple,
- cuánto lleva ejecutado respecto de su presupuesto,
- cómo cerró el año anterior y su peor cierre de los últimos tres años,
- el ritmo de gasto de los últimos tres meses,
- cuánto pesa la inversión en su presupuesto (es el gasto más irregular).

El umbral para alertar se eligió **solo con los años de ajuste**, dejando un año fuera cada vez para no hacer trampa.
Para las alertas de 2026, una vez validado, el modelo se vuelve a ajustar con todos los años cerrados (2019 a 2025),
manteniendo ese mismo umbral.

**5. Explicar el porqué** (`mejoras.py`).
Para cada alerta, el programa proyecta por separado cada tipo de gasto (personal, bienes y servicios, transferencias,
inversión…) y muestra cuál explica el atraso: por ejemplo, "Transferencias de capital: 97% del atraso".

**6. Mostrar los resultados** (`docs/`, `powerbi/`).
Un tablero web con filtro por ministerio y un informe de Power BI con las mismas cifras, ambos con el estándar de
reportes de gestión IBCS (lo actual en oscuro, la historia en gris y el color solo para lo que está mal o bien).

## Qué encontré

**1. El 99% oficial esconde la subejecución.**
Cada año el Estado reporta haber ejecutado cerca del 99% de su presupuesto. Pero ese porcentaje se calcula contra el
presupuesto *final*, que se recorta a fin de año a quienes no alcanzan a gastar. Si se mide contra el presupuesto que
cada institución tenía en agosto, el panorama cambia:

| Año | Instituciones bajo 90% (cifra oficial, vs presupuesto final) | Instituciones bajo 90% (vs presupuesto que tenían en agosto) |
|---|---|---|
| 2023 | 8,7% | **20,0%** |
| 2024 | 5,2% | **16,7%** |
| 2025 | 3,6% | **12,8%** |

**2. El modelo detecta más casos que los métodos simples, pero se equivoca bastante.**
Resultados en los años de prueba (2023 a 2025: 693 casos de institución-año, de los cuales 97 cerraron bajo el 90%):

| Método | Alertas | Aciertos | Precisión (de las alertas, cuántas acertaron) | Cobertura (de los casos, cuántos detectó) |
|---|---|---|---|---|
| Regla simple: alertar a quien ya cerró bajo 90% el año anterior | 111 | 60 | 54% | 62% |
| Proyección simple (paso 3) | 100 | 62 | 62% | 64% |
| **Modelo de riesgo (paso 4)** | 122 | 72 | 59% | **74%** |

Matices que importan:
- **Con pocas alertas, la proyección simple es igual o mejor.** Con las 100 instituciones de mayor riesgo, ambos
  encuentran 62 casos. El modelo gana cuando se revisan más: con 150 alertas encuentra 82 casos y la proyección 73.
- **No gana todos los años.** En 2025 la proyección simple tuvo mejor resultado global (F1 de 70 contra 63). El modelo
  sí ordena mejor de más a menos riesgo en los tres años (AUC entre 0,90 y 0,95 contra 0,72 a 0,87).
- **Casi la mitad de las alertas no se cumple.** Es una señal para revisar, no un veredicto.

**3. Las probabilidades del modelo no deben leerse al pie de la letra.**
Cuando el modelo decía "96% de riesgo", en los años de prueba solo el 74% cerró realmente bajo el 90%. Por eso el
tablero muestra niveles de riesgo con su tasa de acierto observada, no porcentajes:

| Nivel | Instituciones | El modelo decía | Terminó bajo 90% |
|---|---|---|---|
| Bajo el umbral | 571 | 4% | 4% |
| Moderado | 34 | 37% | 47% |
| Alto | 39 | 62% | 51% |
| Muy alto | 49 | 96% | 74% |

**4. En 2026, a agosto, 32 instituciones tienen riesgo de dejar sin gastar $1,6 billones.**
El monto está muy concentrado: **cinco instituciones suman el 77%**: los Gobiernos Regionales ($510 mil millones), la
Subsecretaría de Redes Asistenciales de Salud ($302 mil millones), la Dirección General de Concesiones de Obras
Públicas ($217 mil millones), el SERVIU de Valparaíso ($120 mil millones) y la Corporación Nacional de Desarrollo
Indígena ($66 mil millones). En las cinco, el atraso viene sobre todo de **transferencias de capital** (platas que se
traspasan a otros para obras o proyectos) y no de sueldos ni de gasto corriente.
Otras 10 alertas son Servicios Locales de Educación, instituciones chicas que en conjunto suman solo $7.800 millones.

**5. El gasto se concentra a fin de año.** A fines de agosto, el Estado lleva entre el 61% y el 65% de su gasto anual (un
ritmo parejo daría 67%), y diciembre concentra alrededor del 13%. En algunos servicios, más de un tercio del gasto del
año ocurre en diciembre.

## Qué probé y no funcionó

Parte de un buen análisis es decir lo que no sirvió. En los años de ajuste y de prueba:

| Idea | Resultado |
|---|---|
| Proyectar cada tipo de gasto por separado para mejorar la proyección | Peor (error de 5,3 y 6,6 puntos contra 4,1 y 4,7) |
| Ignorar 2020 y 2021 (pandemia) al calcular el ritmo típico | Igual o peor |
| Más o menos años de historia (2, 4 o 5 en vez de 3) | Sin cambios relevantes en el modelo |

Y para saber cuánto depende el resultado de mis decisiones, las cambié una por una (hoja "Sensibilidad" del informe):
la cobertura del modelo se mantiene en 74% con 2, 3, 4 o 5 años de historia, sube a 83% si se usa un umbral de 95% y
baja a 57% con uno de 85%. El orden (AUC) se mantiene entre 0,91 y 0,94 en todos los casos.

## Límites y qué no debes creer de este proyecto

- **No sé por qué se recorta el presupuesto.** Un recorte después de agosto puede ser un ahorro, una reasignación
  deliberada o un problema de ejecución. Los datos muestran *que* pasa, no *por qué*.
- **La causa del atraso es una pista, no un diagnóstico.** Dice qué tipo de gasto va más atrasado respecto de su ritmo
  histórico; no sabe si hay un problema real, un cambio de calendario o un proyecto postergado a propósito.
- **Los totales no son el gasto oficial del Estado.** Sumo el gasto de cada servicio sin descontar las transferencias
  entre servicios, así que algunas platas se cuentan dos veces. Sirve para comparar a cada institución con su propio
  presupuesto, no para citar "el Estado gastó X".
- **Tres años de prueba son pocos.** Con 97 casos en total, una diferencia de unos pocos aciertos puede ser azar. Por eso
  insisto en el orden (AUC) y en que el resultado se sostiene al cambiar mis decisiones, más que en un número exacto.
- **Los años de ajuste incluyen la pandemia** (2020 y 2021), que tuvo un comportamiento atípico. Las instituciones
  nuevas o reestructuradas tampoco tienen historia propia: para ellas el modelo es menos preciso.
- **Los parámetros los fijé yo** (umbral de 90%, tres años de historia, mínimo de $1.000 millones de presupuesto). El
  umbral de probabilidad y los pesos del modelo salen de los años de ajuste; el resto son decisiones mías, y por eso
  las someto a la prueba de sensibilidad.
- **Montos en pesos nominales**, sin ajustar por inflación, y sin los presupuestos en dólares (menos del 3% de los registros).

## Power BI

El proyecto se genera por código (`powerbi/generar_pbip.py`) en formato `.pbip`: modelo semántico con 25 medidas DAX
y un informe de tres páginas (Resumen, Alertas y Validación). Las medidas las verifiqué contra los resultados de
Python con el servidor MCP de Power BI.

| Resumen | Alertas |
|---|---|
| ![Resumen](docs/img/powerbi_resumen.jpg) | ![Alertas](docs/img/powerbi_alertas.jpg) |

![Validación](docs/img/powerbi_validacion.jpg)

## Cómo reproducirlo

```bash
pip install pandas numpy openpyxl
python descargar.py              # ~90 MB desde datos.gob.cl (queda en caché en datos/crudos/)
python modelo.py                 # presupuesto.db
python analisis.py               # resultados/informe_ejecucion.xlsx (método simple)
python mejoras.py                # resultados/informe_riesgo.xlsx, datos/riesgo.csv (modelo, prueba y causa del atraso)
python exportar_dashboard.py     # docs/data.js (tablero web)
python powerbi/generar_pbip.py   # powerbi/Ejecucion.pbip (abrir con Power BI Desktop y pulsar Actualizar)
```

Cuando la DIPRES publique un mes nuevo, basta con volver a correr los seis pasos.

El último paso también crea el archivo con la ruta de los datos en tu computador (parámetro `CarpetaDatos`
del modelo de Power BI), que no se publica en el repositorio. Por eso hay que correrlo antes de abrir el `.pbip`.

**Glosario rápido**
- **Ley de Presupuestos inicial:** lo que aprobó el Congreso para el año.
- **Presupuesto vigente:** el inicial más las modificaciones hechas durante el año por decreto.
- **Ejecución:** lo efectivamente gastado (devengado).
- **Servicio o institución:** cada "capítulo" de la Ley de Presupuestos (por ejemplo, un SERVIU regional o una subsecretaría).
- **Punto porcentual (pp):** diferencia entre dos porcentajes (de 60% a 64% hay 4 pp).
- **AUC:** mide qué tan bien un método ordena los casos de más a menos riesgo: 0,5 es lo mismo que sortear, 1 es perfecto.
- **Precisión y cobertura:** de las alertas que se emiten, cuántas acertaron (precisión); de los casos reales, cuántos se detectaron (cobertura).

---

Hecho por [Tomás Moya Araya](https://github.com/tomasmoyaaraya), ingeniero civil industrial. Ver también el
[proyecto de control presupuestario en retail](https://github.com/tomasmoyaaraya/control-presupuestario-retail).
