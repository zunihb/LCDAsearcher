"""Tools (function calling) para el chat de LCDA Searcher.

En lugar de enviar todo el contexto gigante al LLM, el modelo pide datos
bajo demanda via tool calls. Esto reduce el prompt de ~5000 tokens a ~500,
y el tiempo de respuesta de ~60s a ~3-5s.

Tools de investigación (en TOOLS, visibles al agente): 9
Tools de admin (/calidad, /fuentes): get_data_quality_report, get_suspicious_records, get_db_stats
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from src.db import Database
from src.data_quality import get_data_quality_report, get_suspicious_records
from src.topic_search import search_keywords_hybrid, normalize_keyword
from src.trends import _slope
from src.search import _topic_potential


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
            "description": (
                "Busca papers por tema o keyword usando normalización y aliases. "
                "Retorna títulos, año, citas y autores ordenados por relevancia. "
                "Útil para preguntas como '¿qué papers hay sobre control predictivo?' o '¿cuáles son los trabajos recientes en fotovoltaica?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Tema o keyword a buscar (ej: 'control predictivo', 'matrix converter', 'fotovoltaica'). Acepta español e inglés.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Número máximo de papers a retornar (default: 10)",
                    },
                    "year_from": {
                        "type": "integer",
                        "description": "Año mínimo (ej: 2022). Úsalo si el usuario menciona un período.",
                    },
                    "year_to": {
                        "type": "integer",
                        "description": "Año máximo (ej: 2026). Úsalo si el usuario menciona un período.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "researchers_by_topic",
            "description": (
                "Devuelve qué investigadores trabajan en un tema, cuántos papers tienen y cuántas citas acumularon. "
                "Útil para preguntas como '¿quién trabaja en convertidores multinivel?' o "
                "'¿cuántos papers tiene cada investigador en control predictivo?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Tema o keyword a consultar (ej: 'convertidor multinivel', 'control predictivo', 'photovoltaic')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de investigadores a retornar (default: 15)",
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "topic_evidence",
            "description": "Devuelve los papers que evidencian que un investigador trabaja en un tema específico. Incluye título, año, citas y abstract parcial.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Nombre o parte del nombre del investigador",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Tema a evidenciar (ej: 'control predictivo', 'inversor multinivel')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de papers a retornar (default: 5)",
                    },
                },
                "required": ["name", "topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_collaborations",
            "description": (
                "Encuentra pares de investigadores que trabajan en temas similares y podrían colaborar. "
                "Útil para preguntas sobre sinergias, colaboraciones potenciales o quién comparte líneas de investigación."
            ),
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
            "name": "compare_researchers",
            "description": "Compara dos o más investigadores por temas, cantidad de papers y actividad reciente. Opcionalmente enfocado en un tema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "description": "Lista de nombres (o partes de nombres) a comparar",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Tema opcional para enfocar la comparación (ej: 'convertidores', 'redes eléctricas')",
                    },
                },
                "required": ["names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trending_topics",
            "description": (
                "Devuelve los temas con mayor crecimiento en la red usando series temporales de papers. "
                "Útil para preguntas como '¿qué temas están creciendo?' o '¿cuáles son las tendencias recientes?'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "year_from": {
                        "type": "integer",
                        "description": "Año inicial del análisis (default: 2021)",
                    },
                    "year_to": {
                        "type": "integer",
                        "description": "Año final del análisis (default: año actual)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de temas a retornar (default: 15)",
                    },
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
        "search_papers": lambda db, args: _search_papers(
            db, args["query"], args.get("limit", 10), args.get("year_from"), args.get("year_to")
        ),
        "researchers_by_topic": lambda db, args: _researchers_by_topic(
            db, args["topic"], args.get("limit", 15)
        ),
        "topic_evidence": lambda db, args: _topic_evidence(
            db, args["name"], args["topic"], args.get("limit", 5)
        ),
        "find_collaborations": lambda db, args: _find_collaborations(db, args.get("keyword")),
        "compare_researchers": lambda db, args: _compare_researchers(
            db, args["names"], args.get("topic")
        ),
        "get_trending_topics": lambda db, args: _get_trending_topics(
            db, args.get("year_from", 2021), args.get("year_to", datetime.now().year), args.get("limit", 15)
        ),
        # Admin tools (usados por /calidad y /fuentes, no en TOOLS del agente)
        "get_data_quality_report": lambda db, args: get_data_quality_report(db),
        "get_suspicious_records": lambda db, args: get_suspicious_records(db, limit=args.get("limit", 50)),
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

    recent = db.query(
        """
        SELECT p.titulo, p.anio, p.citado_por
        FROM papers p JOIN autorias a ON p.id = a.paper_id
        WHERE a.scholar_id = ? ORDER BY p.anio DESC LIMIT 5
        """,
        (sid,),
    )

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


def _search_papers(
    db: Database,
    query: str,
    limit: int = 10,
    year_from: int | None = None,
    year_to: int | None = None,
) -> list[dict]:
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


def _researchers_by_topic(db: Database, topic: str, limit: int = 15) -> list[dict]:
    """Investigadores activos en un tema con paper count y citas."""
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


def _topic_evidence(db: Database, name: str, topic: str, limit: int = 5) -> list[dict]:
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


def _find_collaborations(db: Database, keyword: str | None = None) -> list[dict]:
    import math
    from itertools import combinations

    if keyword:
        # Consulta directa sobre el tema pedido — la caché solo guarda top-30 global
        norm = normalize_keyword(keyword)
        rows = db.query(
            """
            SELECT
                i.scholar_id, i.nombre,
                COALESCE(k.termino_canonico, k.termino) AS kw_display,
                k.keyword_norm AS kw_norm,
                COUNT(DISTINCT p.id) AS papers,
                SUM(COALESCE(p.citado_por, 0)) AS citas,
                MAX(p.anio) AS ultimo_anio
            FROM investigadores i
            JOIN autorias a ON i.scholar_id = a.scholar_id
            JOIN papers p ON p.id = a.paper_id
            JOIN paper_keywords pk ON pk.paper_id = p.id
            JOIN keywords k ON k.id = pk.keyword_id
            WHERE k.keyword_norm LIKE ?
            GROUP BY i.scholar_id, k.keyword_norm
            HAVING papers >= 2
            ORDER BY papers DESC, citas DESC
            """,
            (f"%{norm}%",),
        )
        by_kw: dict[str, list] = {}
        for r in rows:
            by_kw.setdefault(r["kw_norm"], []).append(r)

        matches = []
        for kw_norm_key, inv_rows in by_kw.items():
            if len(inv_rows) < 2:
                continue
            kw_display = inv_rows[0]["kw_display"]
            for left, right in combinations(inv_rows, 2):
                floor_shared = min(left["papers"], right["papers"])
                total_citas = (left["citas"] or 0) + (right["citas"] or 0)
                score = floor_shared * 5.0 + (left["papers"] + right["papers"]) * 0.25 + math.log1p(total_citas) * 0.5
                matches.append({
                    "keyword": kw_display,
                    "investigador_1": left["nombre"],
                    "papers_inv1": left["papers"],
                    "investigador_2": right["nombre"],
                    "papers_inv2": right["papers"],
                    "potencial": _topic_potential(score),
                    "score": score,
                })
        matches.sort(key=lambda r: r["score"], reverse=True)
    else:
        from src.search import _get_cached_matches
        matches = [
            {
                "keyword": m["keyword"],
                "investigador_1": m["investigador_1"],
                "papers_inv1": m["papers_inv1"],
                "investigador_2": m["investigador_2"],
                "papers_inv2": m["papers_inv2"],
                "potencial": m["potencial"],
                "score": m["score"],
            }
            for m in _get_cached_matches(db)
        ]

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


def _get_trending_topics(
    db: Database,
    year_from: int = 2021,
    year_to: int | None = None,
    limit: int = 15,
) -> list[dict]:
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
