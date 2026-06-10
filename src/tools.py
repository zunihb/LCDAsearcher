"""Tools (function calling) para el chat de LCDA Searcher.

En lugar de enviar todo el contexto gigante al LLM, el modelo pide datos
bajo demanda via tool calls. Esto reduce el prompt de ~5000 tokens a ~500,
y el tiempo de respuesta de ~60s a ~3-5s.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.db import Database


# ── Definiciones de tools (JSON Schema) ──────────────────────────────

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_date",
            "description": "Retorna la fecha y hora actual.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_researchers",
            "description": "Lista todos los investigadores con sus métricas básicas (nombre, afiliación, citas, h-index, cantidad de papers).",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_researcher_profile",
            "description": "Obtiene el perfil detallado de un investigador: top keywords, papers recientes, papers más citados. Usa el nombre o parte del nombre para buscar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nombre o parte del nombre del investigador (ej: 'Espinoza', 'Rodriguez', 'Morán')",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "Busca papers por tema/keyword. Retorna títulos, año, citas y autores. Útil para preguntas como '¿qué papers hay sobre control predictivo?'",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Tema o keyword a buscar (ej: 'control predictivo', 'matrix converter', 'photovoltaic')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Número máximo de papers a retornar (default: 10)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_topic_matches",
            "description": "Obtiene los matches temáticos entre investigadores (qué investigadores trabajan en temas similares y podrían colaborar). Útil para preguntas sobre sinergias o colaboraciones potenciales.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Filtrar por keyword específica (opcional). Si se omite, retorna los top matches generales.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_keywords",
            "description": "Busca keywords en la base de datos que coincidan con un término. Retorna las keywords encontradas con su cantidad de papers y citas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {
                        "type": "string",
                        "description": "Término a buscar en keywords (ej: 'predictivo', 'potencia', 'fotovoltaico')",
                    },
                },
                "required": ["term"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_db_stats",
            "description": "Retorna estadísticas generales de la base de datos: cantidad de investigadores, papers, keywords, rango de años, etc.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ── Implementación de tools ──────────────────────────────────────────


def execute_tool(db: Database, name: str, arguments: dict[str, Any]) -> str:
    """Ejecuta un tool y retorna el resultado como string JSON."""
    handlers = {
        "get_current_date": _get_current_date,
        "list_researchers": lambda db, args: _list_researchers(db),
        "get_researcher_profile": lambda db, args: _get_researcher_profile(db, args["name"]),
        "search_papers": lambda db, args: _search_papers(db, args["query"], args.get("limit", 10)),
        "get_topic_matches": lambda db, args: _get_topic_matches(db, args.get("keyword")),
        "search_keywords": lambda db, args: _search_keywords(db, args["term"]),
        "get_db_stats": lambda db, args: _get_db_stats(db),
    }

    handler = handlers.get(name)
    if not handler:
        return json.dumps({"error": f"Tool desconocido: {name}"})

    try:
        result = handler(db, arguments)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _get_current_date(_db: Database, _args: dict) -> dict:
    now = datetime.now()
    return {
        "fecha": now.strftime("%Y-%m-%d"),
        "dia_semana": now.strftime("%A"),
        "hora": now.strftime("%H:%M"),
    }


def _list_researchers(db: Database) -> list[dict]:
    invs = db.get_investigadores()
    paper_counts = db.query("""
        SELECT a.scholar_id, COUNT(DISTINCT a.paper_id) AS papers
        FROM autorias a GROUP BY a.scholar_id
    """)
    counts = {r["scholar_id"]: r["papers"] for r in paper_counts}

    return [
        {
            "nombre": i["nombre"],
            "afiliacion": i.get("afiliacion", ""),
            "citas_total": i.get("citas_total", 0),
            "indice_h": i.get("indice_h", 0),
            "papers": counts.get(i["scholar_id"], 0),
        }
        for i in invs
    ]


def _get_researcher_profile(db: Database, name: str) -> dict:
    invs = db.get_investigadores()
    target = None
    for inv in invs:
        if name.lower() in inv["nombre"].lower():
            target = inv
            break

    if not target:
        disponibles = [i["nombre"] for i in invs]
        return {"error": f"No encontré '{name}'", "disponibles": disponibles}

    sid = target["scholar_id"]

    # Top keywords
    kws = db.query(
        """
        SELECT COALESCE(k.termino_canonico, k.termino) AS keyword,
               COUNT(DISTINCT p.id) AS papers,
               SUM(COALESCE(p.citado_por, 0)) AS citas
        FROM autorias a
        JOIN papers p ON p.id = a.paper_id
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE a.scholar_id = ?
        GROUP BY keyword ORDER BY papers DESC LIMIT 10
        """,
        (sid,),
    )

    # Papers recientes
    recent = db.query(
        """
        SELECT p.titulo, p.anio, p.citado_por
        FROM papers p JOIN autorias a ON p.id = a.paper_id
        WHERE a.scholar_id = ? ORDER BY p.anio DESC LIMIT 5
        """,
        (sid,),
    )

    # Papers más citados
    top = db.query(
        """
        SELECT p.titulo, p.anio, p.citado_por
        FROM papers p JOIN autorias a ON p.id = a.paper_id
        WHERE a.scholar_id = ? ORDER BY p.citado_por DESC LIMIT 5
        """,
        (sid,),
    )

    return {
        "nombre": target["nombre"],
        "afiliacion": target.get("afiliacion", ""),
        "citas_total": target.get("citas_total", 0),
        "indice_h": target.get("indice_h", 0),
        "indice_i10": target.get("indice_i10", 0),
        "top_keywords": [
            {"keyword": k["keyword"], "papers": k["papers"], "citas": k["citas"]}
            for k in kws
        ],
        "papers_recientes": [
            {"titulo": p["titulo"], "anio": p["anio"], "citas": p["citado_por"]}
            for p in recent
        ],
        "papers_mas_citados": [
            {"titulo": p["titulo"], "anio": p["anio"], "citas": p["citado_por"]}
            for p in top
        ],
    }


def _search_papers(db: Database, query: str, limit: int = 10) -> list[dict]:
    # Buscar por keyword exacta primero
    rows = db.query(
        """
        SELECT p.titulo, p.anio, p.citado_por, p.autores_texto
        FROM papers p
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE COALESCE(k.termino_canonico, k.termino) LIKE ?
        ORDER BY p.citado_por DESC LIMIT ?
        """,
        (f"%{query}%", limit),
    )

    if not rows:
        # Fallback: buscar por título
        rows = db.query(
            """
            SELECT p.titulo, p.anio, p.citado_por, p.autores_texto
            FROM papers p
            WHERE p.titulo LIKE ?
            ORDER BY p.citado_por DESC LIMIT ?
            """,
            (f"%{query}%", limit),
        )

    return [
        {
            "titulo": r["titulo"],
            "anio": r["anio"],
            "citas": r["citado_por"],
            "autores": r.get("autores_texto", ""),
        }
        for r in rows
    ]


def _get_topic_matches(db: Database, keyword: str | None = None) -> list[dict]:
    from src.search import _get_cached_matches

    matches = _get_cached_matches(db)

    if keyword:
        kw_lower = keyword.lower()
        matches = [m for m in matches if kw_lower in m["keyword"].lower()]

    return [
        {
            "keyword": m["keyword"],
            "investigador_1": m["investigador_1"],
            "papers_inv1": m["papers_inv1"],
            "investigador_2": m["investigador_2"],
            "papers_inv2": m["papers_inv2"],
            "potencial": m["potencial"],
        }
        for m in matches[:15]
    ]


def _search_keywords(db: Database, term: str) -> list[dict]:
    rows = db.query(
        """
        SELECT COALESCE(k.termino_canonico, k.termino) AS keyword,
               COUNT(DISTINCT pk.paper_id) AS papers,
               SUM(COALESCE(p.citado_por, 0)) AS citas
        FROM keywords k
        JOIN paper_keywords pk ON k.id = pk.keyword_id
        JOIN papers p ON p.id = pk.paper_id
        WHERE COALESCE(k.termino_canonico, k.termino) LIKE ?
        GROUP BY keyword ORDER BY papers DESC LIMIT 15
        """,
        (f"%{term}%",),
    )
    return [
        {"keyword": r["keyword"], "papers": r["papers"], "citas": r["citas"]}
        for r in rows
    ]


def _get_db_stats(db: Database) -> dict:
    stats = db.query("""
        SELECT
            (SELECT COUNT(*) FROM investigadores) AS investigadores,
            (SELECT COUNT(*) FROM papers) AS papers,
            (SELECT COUNT(*) FROM keywords) AS keywords,
            (SELECT COUNT(*) FROM autorias) AS autorias,
            (SELECT MIN(anio) FROM papers WHERE anio IS NOT NULL) AS anio_min,
            (SELECT MAX(anio) FROM papers) AS anio_max
    """)
    if not stats:
        return {"error": "No se pudieron obtener estadísticas"}
    s = stats[0]
    return {
        "investigadores": s["investigadores"],
        "papers": s["papers"],
        "keywords": s["keywords"],
        "autorias": s["autorias"],
        "rango_anios": f"{s['anio_min']}-{s['anio_max']}",
    }
