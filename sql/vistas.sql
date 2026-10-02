-- Vistas de análisis sobre la tabla hechos (año, mes, partida, capitulo, subtitulo, montos en MM$).
-- ejec_acum y ley_vigente son "a la fecha": en el mes m traen lo acumulado/vigente a ese mes.

-- Institución x mes: ejecución acumulada contra el presupuesto vigente a ese mes
DROP VIEW IF EXISTS v_institucion_mes;
CREATE VIEW v_institucion_mes AS
SELECT h.año, h.mes, h.partida, h.capitulo,
       SUM(h.ejec_mes)    AS ejec_mes,
       SUM(h.ejec_acum)   AS ejec_acum,
       SUM(h.ley_inicial) AS ley_inicial,
       SUM(h.ley_vigente) AS ley_vigente,
       SUM(h.ejec_acum) / NULLIF(SUM(h.ley_vigente), 0) AS pct_ejecucion
FROM hechos h
GROUP BY h.año, h.mes, h.partida, h.capitulo;

-- Institución x año (solo años cerrados: con dato de diciembre)
DROP VIEW IF EXISTS v_institucion_año;
CREATE VIEW v_institucion_año AS
SELECT m.año, m.partida, m.capitulo, i.ministerio, i.institucion,
       m.ley_inicial,
       m.ley_vigente                                   AS ley_vigente_final,
       m.ejec_acum                                     AS ejecutado,
       m.ejec_acum / NULLIF(m.ley_vigente, 0)          AS pct_ejecucion,
       m.ley_vigente / NULLIF(m.ley_inicial, 0) - 1    AS pct_modificacion,
       m.ejec_mes / NULLIF(m.ejec_acum, 0)             AS peso_diciembre
FROM v_institucion_mes m
JOIN instituciones i USING (partida, capitulo)
WHERE m.mes = 12;

-- Ministerio x mes (para la curva de ejecución)
DROP VIEW IF EXISTS v_ministerio_mes;
CREATE VIEW v_ministerio_mes AS
SELECT h.año, h.mes, h.partida, i.ministerio,
       SUM(h.ejec_mes) AS ejec_mes, SUM(h.ejec_acum) AS ejec_acum, SUM(h.ley_vigente) AS ley_vigente
FROM hechos h
JOIN (SELECT DISTINCT partida, ministerio FROM instituciones) i USING (partida)
GROUP BY h.año, h.mes, h.partida, i.ministerio;
