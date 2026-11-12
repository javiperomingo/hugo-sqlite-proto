---
title: "Consultas SQL de agregación en tiempo de build"
date: 2026-11-11
description: "Cómo usar GROUP BY, COUNT y funciones JSON de SQLite para precalcular datos estáticos."
tags: [sql, sqlite, build, static]
---
Una de las mayores ventajas de incorporar **SQLite** en el proceso de construcción (*build*) es que podemos delegar las agregaciones y conteos al motor relacional mediante consultas **SQL** declarativas en lugar de programar bucles manuales en las plantillas de Hugo.

Por ejemplo, para calcular cuántas publicaciones existen por cada etiqueta y exportar el resultado directamente como un array JSON, `tools/index.py` ejecuta:

```sql
SELECT json_group_array(json_object('tag', tag, 'total', total))
FROM (
  SELECT tag, COUNT(*) AS total
  FROM tags
  GROUP BY tag
  ORDER BY total DESC, tag
);
```

De la misma forma, con una tabla `words(url, n)` podemos obtener el total de palabras de todo el sitio con una simple función de agregación `SUM(n)` en SQL y volcarlo en `data/words.json` para que Hugo lo pinte en el pie de página sin coste alguno en producción.
