#!/usr/bin/env python3
"""tools/migrar.py — content/posts/*.md → base de datos SQLite de Chyrp Lite.

Filosofía Unix, como tools/index.py: hace UNA cosa. Lee los Markdown de tu
blog (P1.1), los convierte a las filas que espera Chyrp Lite y las inserta.

  extraer     (disco)  → lista de dicts con lo que dice cada .md
  transformar (PURA)   → dict con la fila de `posts` y sus atributos
  cargar      (SQL)    → INSERT dentro de UNA transacción, sin duplicar

Chyrp Lite guarda cada entrada en dos tablas:
  posts(id, feather, clean, url, pinned, status, user_id, created_at, updated_at)
  post_attributes(post_id, name, value)   ← título, cuerpo y etiquetas (EAV)

Solo biblioteca estándar. Reutiliza split_front_matter y parse_meta de index.py.
Límites (a propósito, KISS): solo entradas de blog (no `pages`), etiquetas
con letras y números, y el cuerpo se copia tal cual (Chyrp Lite entiende Markdown).

Uso:
  python3 tools/migrar.py --destino ~/chyrp-datos/copia.sqlite --simulacro
  python3 tools/migrar.py --destino ~/chyrp-datos/copia.sqlite
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from index import parse_meta, split_front_matter

# Valor que usa Chyrp Lite para "esta entrada nunca se ha editado".
SQL_DATETIME_ZERO = "1000-01-01 00:00:00"

# --- funciones puras ---------------------------------------------------------

def slugify(texto):
    """'Árbol B' → 'arbol-b' (algo parecido al campo `clean` de Chyrp Lite)."""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", sin_tildes.lower()).strip("-")


def fecha_chyrp(valor, por_defecto):
    """'2026-10-27' o '2026-10-27T09:30' → '2026-10-27 09:30:00'."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::(\d{2}))?)?", str(valor))
    if not m:
        return por_defecto
    dia, hora_min, seg = m.groups()
    return f"{dia} {hora_min or '00:00'}:{seg or '00'}"


def etiquetas_json(nombres):
    """['sql', 'Árbol B'] → '{"sql":"sql","\u00c1rbol B":"arbol-b"}'.

    Es el formato exacto con el que el módulo Tags guarda las etiquetas en
    post_attributes: un objeto JSON {nombre: slug}. Sin etiquetas guarda '[]'.
    Para etiquetas numéricas (como '2026'), Chyrp Lite antepone U+FE0E a la
    clave y usa md5(clave) como slug para evitar conversión a índice en PHP.
    """
    if not nombres:
        return "[]"
    asociacion = {}
    for nombre in nombres:
        clave = str(nombre)
        if re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)", clave):
            clave = "\ufe0e" + clave
        slug = slugify(clave)
        if not re.search(r"[^-0-9]", slug):
            slug = hashlib.md5(clave.encode("utf-8")).hexdigest()
        asociacion[clave] = slug
    # PHP escribe "/" como "\/" y Python no: igualamos los bytes.
    return json.dumps(asociacion, separators=(",", ":")).replace("/", "\\/")



def leer_entrada(path):
    """Un .md → dict con lo que nos interesa. Pura salvo por leer el fichero."""
    front, cuerpo = split_front_matter(path.read_text(encoding="utf-8"))
    meta = parse_meta(front)
    tags = meta.get("tags", [])
    return {
        "slug": path.stem,
        "titulo": meta.get("title", path.stem),
        "fecha": str(meta.get("date", "")),
        "cuerpo": cuerpo.strip(),
        "tags": tags if isinstance(tags, list) else [tags],
        "draft": meta.get("draft", False) is True,
    }


def transformar(entrada, user_id, ahora):
    """Entrada del prototipo → filas para Chyrp Lite. Sin disco ni SQL."""
    slug = slugify(entrada["slug"])
    return {
        "post": {
            "feather": "text",
            "clean": slug,
            "url": slug,
            "pinned": 0,
            "status": "draft" if entrada["draft"] else "public",
            "user_id": user_id,
            "created_at": fecha_chyrp(entrada["fecha"], ahora),
            "updated_at": SQL_DATETIME_ZERO,
        },
        "attrs": {
            "title": entrada["titulo"],
            "body": entrada["cuerpo"],
            "tags": etiquetas_json(entrada["tags"]),
        },
    }



def transformar_pagina(entrada, user_id, ahora):
    """Pagina del prototipo (p. ej. content/about.md) -> fila para tabla pages."""
    slug = slugify(entrada["slug"])
    return {
        "title": entrada["titulo"],
        "body": entrada["cuerpo"],
        "public": 0 if entrada["draft"] else 1,
        "show_in_list": 1,
        "list_order": 0,
        "clean": slug,
        "url": slug,
        "user_id": user_id,
        "parent_id": 0,
        "created_at": fecha_chyrp(entrada["fecha"], ahora),
        "updated_at": SQL_DATETIME_ZERO,
    }

# --- efectos: disco y SQL ----------------------------------------------------

def extraer(origen):
    ficheros = (p for p in sorted(Path(origen).rglob("*.md")) if p.name != "_index.md")
    return [leer_entrada(p) for p in ficheros]


def comprobar_destino(destino):
    """Falla con un mensaje claro si no es una base de datos de Chyrp Lite."""
    if not Path(destino).is_file():
        sys.exit(f"migrar.py: no existe {destino} (instala Chyrp Lite antes)")
    con = sqlite3.connect(destino)
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    if not {"posts", "post_attributes", "users"} <= tablas:
        sys.exit(f"migrar.py: {destino} no parece una base de datos de Chyrp Lite")


def buscar_autor(con, login):
    sql, params = (("SELECT id FROM users WHERE login = ?", (login,)) if login
                   else ("SELECT MIN(id) FROM users", ()))
    fila = con.execute(sql, params).fetchone()
    if fila is None or fila[0] is None:
        sys.exit("migrar.py: no encuentro ese autor en la tabla users")
    return fila[0]


def existe(con, url):
    return con.execute("SELECT 1 FROM posts WHERE url = ?", (url,)).fetchone() is not None


def insertar(con, fila):
    columnas = list(fila["post"])
    cur = con.execute(
        f"INSERT INTO posts({', '.join(columnas)}) VALUES ({', '.join('?' * len(columnas))})",
        [fila["post"][c] for c in columnas])
    con.executemany(
        "INSERT INTO post_attributes(post_id, name, value) VALUES (?, ?, ?)",
        [(cur.lastrowid, nombre, valor) for nombre, valor in fila["attrs"].items()])
    return cur.lastrowid


def cargar(destino, filas, simulacro=False):
    """Inserta lo que falte. Devuelve [(estado, url)]. Una sola transacción."""
    con = sqlite3.connect(destino)
    try:
        with con:  # commit si todo va bien, rollback si algo falla
            resultado = []
            for fila in filas:
                url = fila["post"]["url"]
                if existe(con, url):
                    resultado.append(("omitida", url))
                    continue
                if not simulacro:
                    insertar(con, fila)
                resultado.append(("simulada" if simulacro else "creada", url))
            return resultado
    finally:
        con.close()




def extraer_paginas(origen):
    """Lee los .md en la raiz de origen (sin bajar a subcarpetas ni _index.md)."""
    ficheros = (p for p in sorted(Path(origen).glob("*.md")) if p.name != "_index.md")
    return [leer_entrada(p) for p in ficheros]


def existe_pagina(con, url):
    return con.execute("SELECT 1 FROM pages WHERE url = ?", (url,)).fetchone() is not None


def insertar_pagina(con, fila):
    columnas = list(fila)
    cur = con.execute(
        f"INSERT INTO pages({", ".join(columnas)}) VALUES ({", ".join("?" * len(columnas))})",
        [fila[c] for c in columnas])
    return cur.lastrowid


def cargar_paginas(destino, filas, simulacro=False):
    """Inserta paginas que falten en la tabla pages dentro de una transaccion."""
    con = sqlite3.connect(destino)
    try:
        with con:
            resultado = []
            for fila in filas:
                url = fila["url"]
                if existe_pagina(con, url):
                    resultado.append(("omitida", url))
                    continue
                if not simulacro:
                    insertar_pagina(con, fila)
                resultado.append(("simulada" if simulacro else "creada", url))
            return resultado
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="Markdown (P1.1) → SQLite de Chyrp Lite")
    ap.add_argument("--origen", default="content/posts", help="carpeta con los .md")
    ap.add_argument("--destino", required=True, help="fichero .sqlite de Chyrp Lite (¡una COPIA!)")
    ap.add_argument("--autor", help="login del autor en Chyrp Lite (por defecto, el primer usuario)")
    ap.add_argument("--paginas", help="carpeta con paginas .md raiz (p. ej. content)")
    ap.add_argument("--simulacro", action="store_true", help="muestra qué haría, sin escribir")
    args = ap.parse_args()

    comprobar_destino(args.destino)
    entradas = extraer(args.origen)
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    con = sqlite3.connect(args.destino)
    user_id = buscar_autor(con, args.autor)
    con.close()

    filas = [transformar(e, user_id, ahora) for e in entradas]
    resultado = cargar(args.destino, filas, args.simulacro)

    for estado, url in resultado:
        print(f"{estado:9} {url}")
    nuevas = sum(1 for estado, _ in resultado if estado != "omitida")
    print(f"migrar: {len(entradas)} entradas leídas, {nuevas} nuevas, "
          f"{len(entradas) - nuevas} ya estaban")



    if args.paginas:
        paginas = extraer_paginas(args.paginas)
        filas_pag = [transformar_pagina(p, user_id, ahora) for p in paginas]
        res_pag = cargar_paginas(args.destino, filas_pag, args.simulacro)
        for estado, url in res_pag:
            print(f"{estado:9} pagina:{url}")
        nuevas_p = sum(1 for estado, _ in res_pag if estado != "omitida")
        print(f"migrar paginas: {len(paginas)} leídas, {nuevas_p} nuevas, "
              f"{len(paginas) - nuevas_p} ya estaban")


if __name__ == "__main__":
    main()
