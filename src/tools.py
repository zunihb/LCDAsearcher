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
from src.data_quality import get_data_quality_report, get_suspicious_records
from src.topic_search import search_keywords_hybrid, tokenize_query, normalize_keyword
from src.trends import _slope


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
                    "year_from": {
                        "type": "integer",
                        "description": "Año mínimo (ej: 2024). Úsalo si el usuario menciona un año o período.",
                    },
                    "year_to": {
                        "type": "integer",
                        "description": "Año máximo (ej: 2026). Úsalo si el usuario menciona un año o período.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_papers_by_researcher_and_topic",
            "description": "Cuenta cuántos papers tiene cada investigador en un tema específico. Retorna una tabla con investigador, papers, citas y último año. Útil para preguntas como '¿cuántos papers tiene cada investigador en convertidores multinivel?'",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Tema o keyword a buscar (ej: 'convertidores multinivel', 'control predictivo', 'photovoltaic')",
                    },
                },
                "required": ["topic"],
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
    {
        "type": "function",
        "function": {
            "name": "get_data_quality_report",
            "description": "Retorna un reporte de calidad de datos: cobertura de abstracts, DOI duplicados, OpenAlex duplicados, keywords fragmentadas y papers sospechosos.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_suspicious_records",
            "description": "Lista papers sospechosos o incompletos para revisión manual.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Máximo de registros a retornar (default: 50)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_topic_hybrid",
            "description": "Busca un tema con aliases, normalización y ranking por frecuencia/citas/recencia. Mejor que LIKE.",
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "Tema a buscar"},
                    "limit": {"type": "integer", "description": "Máximo de resultados (default: 15)"},
                },
                "required": ["term"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_researchers_by_topic",
            "description": "Devuelve investigadores ordenados por actividad en un tema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Tema a consultar"},
                    "limit": {"type": "integer", "description": "Máximo de resultados (default: 15)"},
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_topic_evidence",
            "description": "Devuelve papers que justifican que un investigador trabaja en un tema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre o parte del nombre del investigador"},
                    "topic": {"type": "string", "description": "Tema a evidenciar"},
                    "limit": {"type": "integer", "description": "Máximo de papers (default: 5)"},
                },
                "required": ["name", "topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_researchers",
            "description": "Compara dos o más investigadores por temas, papers y actividad reciente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                    "topic": {"type": "string", "description": "Tema opcional para enfocar la comparación"},
                },
                "required": ["names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trending_topics",
            "description": "Devuelve temas en tendencia dentro de la base usando series temporales internas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "year_from": {"type": "integer", "description": "Año inicial (default: 2021)"},
                    "year_to": {"type": "integer", "description": "Año final (default: actual)"},
                    "limit": {"type": "integer", "description": "Máximo de resultados (default: 15)"},
                },
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
        "search_papers": lambda db, args: _search_papers(db, args["query"], args.get("limit", 10), args.get("year_from"), args.get("year_to")),
        "get_papers_by_researcher_and_topic": lambda db, args: _get_papers_by_researcher_and_topic(db, args["topic"]),
        "get_topic_matches": lambda db, args: _get_topic_matches(db, args.get("keyword")),
        "search_keywords": lambda db, args: _search_keywords(db, args["term"]),
        "get_db_stats": lambda db, args: _get_db_stats(db),
        "get_data_quality_report": lambda db, args: _get_data_quality_report(db),
        "get_suspicious_records": lambda db, args: _get_suspicious_records(db, args.get("limit", 50)),
        "search_topic_hybrid": lambda db, args: _search_topic_hybrid(db, args["term"], args.get("limit", 15)),
        "get_researchers_by_topic": lambda db, args: _get_researchers_by_topic(db, args["topic"], args.get("limit", 15)),
        "get_topic_evidence": lambda db, args: _get_topic_evidence(db, args["name"], args["topic"], args.get("limit", 5)),
        "compare_researchers": lambda db, args: _compare_researchers(db, args["names"], args.get("topic")),
        "get_trending_topics": lambda db, args: _get_trending_topics(db, args.get("year_from", 2021), args.get("year_to", datetime.now().year), args.get("limit", 15)),
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
        SELECT k.keyword_norm AS keyword,
               COUNT(DISTINCT p.id) AS papers,
               SUM(COALESCE(p.citado_por, 0)) AS citas
        FROM autorias a
        JOIN papers p ON p.id = a.paper_id
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE a.scholar_id = ?
        GROUP BY k.keyword_norm ORDER BY papers DESC LIMIT 10
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


def _search_papers(db: Database, query: str, limit: int = 10, year_from: int | None = None, year_to: int | None = None) -> list[dict]:
    norm = normalize_keyword(query)
    year_filter = ""
    year_params: list[Any] = []

    if year_from is not None and year_to is not None:
        year_filter = "AND p.anio BETWEEN ? AND ?"
        year_params = [year_from, year_to]
    elif year_from is not None:
        year_filter = "AND p.anio >= ?"
        year_params = [year_from]
    elif year_to is not None:
        year_filter = "AND p.anio <= ?"
        year_params = [year_to]

    params: list[Any] = [f"%{norm}%"] + year_params + [limit]
    rows = db.query(
        f"""
        SELECT DISTINCT p.titulo, p.anio, p.citado_por, p.autores_texto
        FROM papers p
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE k.keyword_norm LIKE ?
        {year_filter}
        ORDER BY p.citado_por DESC LIMIT ?
        """,
        tuple(params),
    )

    if not rows:
        params = [f"%{norm}%"] + year_params + [limit]
        rows = db.query(
            f"""
            SELECT p.titulo, p.anio, p.citado_por, p.autores_texto
            FROM papers p
            WHERE p.titulo LIKE ?
            {year_filter}
            ORDER BY p.citado_por DESC LIMIT ?
            """,
            tuple(params),
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


def _get_papers_by_researcher_and_topic(db: Database, topic: str) -> list[dict]:
    """Cuenta papers por investigador para un tema específico."""
    norm = normalize_keyword(topic)
    rows = db.query(
        """
        SELECT
            i.nombre,
            i.afiliacion,
            COUNT(DISTINCT p.id) AS papers,
            SUM(COALESCE(p.citado_por, 0)) AS citas,
            MAX(p.anio) AS ultimo_anio
        FROM investigadores i
        JOIN autorias a ON i.scholar_id = a.scholar_id
        JOIN papers p ON p.id = a.paper_id
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE k.keyword_norm LIKE ?
        GROUP BY i.scholar_id, i.nombre, i.afiliacion
        ORDER BY papers DESC, citas DESC
        """,
        (f"%{norm}%",),
    )
    return [
        {
            "investigador": r["nombre"],
            "afiliacion": r.get("afiliacion", ""),
            "papers": r["papers"],
            "citas": r["citas"],
            "ultimo_anio": r["ultimo_anio"],
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
    norm = normalize_keyword(term)
    rows = db.query(
        """
        SELECT k.keyword_norm AS keyword,
               COUNT(DISTINCT pk.paper_id) AS papers,
               SUM(COALESCE(p.citado_por, 0)) AS citas
        FROM keywords k
        JOIN paper_keywords pk ON k.id = pk.keyword_id
        JOIN papers p ON p.id = pk.paper_id
        WHERE k.keyword_norm LIKE ?
        GROUP BY k.keyword_norm ORDER BY papers DESC LIMIT 15
        """,
        (f"%{norm}%",),
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


def _get_data_quality_report(db: Database) -> dict:
    return get_data_quality_report(db)


def _get_suspicious_records(db: Database, limit: int = 50) -> list[dict]:
    return get_suspicious_records(db, limit=limit)


def _search_topic_hybrid(db: Database, term: str, limit: int = 15) -> list[dict]:
    return search_keywords_hybrid(db, term, limit=limit)


def _get_researchers_by_topic(db: Database, topic: str, limit: int = 15) -> list[dict]:
    norm = normalize_keyword(topic)
    rows = db.query(
        """
        SELECT
            i.nombre,
            i.afiliacion,
            COUNT(DISTINCT p.id) AS papers,
            SUM(COALESCE(p.citado_por, 0)) AS citas,
            MAX(p.anio) AS ultimo_anio
        FROM investigadores i
        JOIN autorias a ON i.scholar_id = a.scholar_id
        JOIN papers p ON p.id = a.paper_id
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE k.keyword_norm LIKE ?
        GROUP BY i.scholar_id, i.nombre, i.afiliacion
        ORDER BY papers DESC, citas DESC
        LIMIT ?
        """,
        (f"%{norm}%", limit),
    )
    return [
        {
            "investigador": r["nombre"],
            "afiliacion": r.get("afiliacion", ""),
            "papers": r["papers"],
            "citas": r["citas"],
            "ultimo_anio": r["ultimo_anio"],
        }
        for r in rows
    ]


def _get_topic_evidence(db: Database, name: str, topic: str, limit: int = 5) -> list[dict]:
    invs = db.get_investigadores()
    target = next((inv for inv in invs if name.lower() in inv["nombre"].lower()), None)
    if not target:
        return [{"error": f"No encontré '{name}'"}]

    norm = normalize_keyword(topic)
    rows = db.query(
        """
        SELECT DISTINCT p.titulo, p.anio, p.citado_por, p.abstract, p.autores_texto,
               k.keyword_norm AS keyword
        FROM papers p
        JOIN autorias a ON p.id = a.paper_id
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE a.scholar_id = ?
          AND k.keyword_norm LIKE ?
        ORDER BY p.citado_por DESC, p.anio DESC
        LIMIT ?
        """,
        (target["scholar_id"], f"%{norm}%", limit),
    )
    return [
        {
            "titulo": r["titulo"],
            "anio": r["anio"],
            "citas": r["citado_por"],
            "keyword": r["keyword"],
            "autores": r.get("autores_texto", ""),
            "abstract": (r.get("abstract") or "")[:300],
        }
        for r in rows
    ]


def _compare_researchers(db: Database, names: list[str], topic: str | None = None) -> dict:
    invs = db.get_investigadores()
    targets = []
    for n in names:
        inv = next((i for i in invs if n.lower() in i["nombre"].lower()), None)
        if inv:
            targets.append(inv)
    if len(targets) < 2:
        return {"error": "Necesito al menos dos investigadores encontrados"}

    result = []
    for inv in targets:
        rows = db.query(
            """
            SELECT k.keyword_norm AS keyword,
                   COUNT(DISTINCT p.id) AS papers,
                   SUM(COALESCE(p.citado_por, 0)) AS citas,
                   MAX(p.anio) AS ultimo_anio
            FROM autorias a
            JOIN papers p ON p.id = a.paper_id
            JOIN paper_keywords pk ON pk.paper_id = p.id
            JOIN keywords k ON k.id = pk.keyword_id
            WHERE a.scholar_id = ?
            GROUP BY k.keyword_norm
            ORDER BY papers DESC, citas DESC
            LIMIT 10
            """,
            (inv["scholar_id"],),
        )
        if topic:
            topic_norm = normalize_keyword(topic)
            topic_words = set(topic_norm.split())
            for r in rows:
                r["match_tema"] = any(w in r["keyword"] for w in topic_words)
            rows.sort(key=lambda r: (r.get("match_tema", False), r["papers"]), reverse=True)
        result.append({"nombre": inv["nombre"], "keywords": rows})
    return {"investigadores": result}


def _get_trending_topics(db: Database, year_from: int = 2021, year_to: int | None = None, limit: int = 15) -> list[dict]:
    year_to = year_to or datetime.now().year
    rows = db.query(
        """
        SELECT
            k.keyword_norm AS keyword,
            p.anio,
            COUNT(DISTINCT p.id) AS conteo
        FROM papers p
        JOIN paper_keywords pk ON pk.paper_id = p.id
        JOIN keywords k ON k.id = pk.keyword_id
        WHERE p.anio BETWEEN ? AND ?
          AND k.keyword_norm IS NOT NULL
        GROUP BY k.keyword_norm, p.anio
        ORDER BY k.keyword_norm, p.anio
        """,
        (year_from, year_to),
    )
    by_kw: dict[str, dict[int, int]] = {}
    for r in rows:
        kw = r["keyword"]
        by_kw.setdefault(kw, {})[r["anio"]] = r["conteo"]

    results = []
    years = list(range(year_from, year_to + 1))
    for kw, year_counts in by_kw.items():
        serie = [year_counts.get(y, 0) for y in years]
        growth = _slope([float(v) for v in serie])
        total = sum(serie)
        if total <= 0:
            continue
        results.append({
            "keyword": kw,
            "papers": total,
            "crecimiento": round(growth, 4),
            "serie": dict(zip(years, serie)),
        })
    results.sort(key=lambda r: (r["crecimiento"], r["papers"]), reverse=True)
    return results[:limit]
