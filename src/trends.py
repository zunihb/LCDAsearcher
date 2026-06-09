"""Análisis de tendencias internas vs globales (OpenAlex) + brecha."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import requests
from plotly.subplots import make_subplots

from src.db import Database


def _slope(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs) or 1
    return num / den


def fetch_openalex_counts(keyword: str, mailto: str) -> dict[int, int]:
    """Conteos mundiales por año desde OpenAlex."""
    url = "https://api.openalex.org/works"
    params = {
        "search": keyword,
        "group_by": "publication_year",
        "per_page": 200,
        "mailto": mailto,
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        counts: dict[int, int] = {}
        for g in data.get("group_by", []):
            year = g.get("key")
            if year and str(year).isdigit():
                counts[int(year)] = g.get("count", 0)
        return counts
    except Exception:
        return {}


def _categoria(cobertura: float, crecimiento_global: float) -> str:
    if crecimiento_global > 0 and cobertura < 0.3:
        return "Oportunidad"
    if crecimiento_global > 0 and cobertura >= 0.3:
        return "Fortaleza al alza"
    if crecimiento_global <= 0 and cobertura >= 0.3:
        return "Madura"
    return "Nicho"


def compute_trends(
    db: Database,
    mailto: str,
    ventana_anios: int = 6,
    top_n: int = 15,
) -> list[dict[str, Any]]:
    internas = db.get_keywords_internas_por_anio()
    if not internas:
        return []

    by_kw: dict[str, dict] = {}
    for row in internas:
        kw = row["keyword"]
        if kw not in by_kw:
            by_kw[kw] = {"keyword_id": row["keyword_id"], "por_anio": {}}
        by_kw[kw]["por_anio"][row["anio"]] = row["conteo"]

    max_anio = max(r["anio"] for r in internas)
    min_anio = max_anio - ventana_anios + 1
    results = []

    sorted_kws = sorted(
        by_kw.items(),
        key=lambda x: sum(v for y, v in x[1]["por_anio"].items() if y >= min_anio),
        reverse=True,
    )[:top_n]

    for kw, data in sorted_kws:
        kid = data["keyword_id"]
        serie_int = [data["por_anio"].get(y, 0) for y in range(min_anio, max_anio + 1)]
        slope_int = _slope([float(v) for v in serie_int])
        total_int = sum(serie_int)
        max_int = max(sum(d["por_anio"].get(y, 0) for y in range(min_anio, max_anio + 1)) for d in by_kw.values()) or 1
        cobertura = total_int / max_int

        cached = db.get_tendencias_globales(kid)
        if not cached:
            global_counts = fetch_openalex_counts(kw, mailto)
            for anio, cnt in global_counts.items():
                db.upsert_tendencia_global(kid, anio, cnt)
            cached = db.get_tendencias_globales(kid)
            time.sleep(0.5)

        global_by_year = {r["anio"]: r["conteo_global"] for r in cached}
        serie_glob = [global_by_year.get(y, 0) for y in range(min_anio, max_anio + 1)]
        slope_glob = _slope([float(v) for v in serie_glob])

        cat = _categoria(cobertura, slope_glob)
        results.append(
            {
                "keyword": kw,
                "keyword_id": kid,
                "crecimiento_interno": round(slope_int, 4),
                "crecimiento_global": round(slope_glob, 4),
                "cobertura_grupo": round(cobertura, 3),
                "papers_grupo": total_int,
                "categoria": cat,
                "serie_interna": dict(zip(range(min_anio, max_anio + 1), serie_int)),
                "serie_global": dict(zip(range(min_anio, max_anio + 1), serie_glob)),
            }
        )

    return results


def write_trends_outputs(db: Database, trends: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_lines = ["keyword,papers_grupo,crecimiento_interno,crecimiento_global,cobertura_grupo,categoria"]
    for t in trends:
        csv_lines.append(
            f"{t['keyword']},{t['papers_grupo']},{t['crecimiento_interno']},"
            f"{t['crecimiento_global']},{t['cobertura_grupo']},{t['categoria']}"
        )
    (output_dir / "tendencias.csv").write_text("\n".join(csv_lines), encoding="utf-8")

    if not trends:
        (output_dir / "tendencias.html").write_text("<p>Sin datos de tendencias.</p>", encoding="utf-8")
        return

    years = sorted({y for t in trends for y in t["serie_interna"]})
    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=("Tendencias internas (grupo)", "Cuadrante: cobertura vs crecimiento global"),
        row_heights=[0.55, 0.45],
        specs=[[{"type": "scatter"}], [{"type": "scatter"}]],
    )

    for t in trends[:8]:
        ys = [t["serie_interna"].get(y, 0) for y in years]
        fig.add_trace(
            go.Scatter(x=years, y=ys, mode="lines+markers", name=t["keyword"][:30]),
            row=1, col=1,
        )

    colors = {"Oportunidad": "#c4620a", "Fortaleza al alza": "#1a7a4a", "Madura": "#0077a8", "Nicho": "#9e9488"}
    for t in trends:
        fig.add_trace(
            go.Scatter(
                x=[t["crecimiento_global"]],
                y=[t["cobertura_grupo"]],
                mode="markers+text",
                text=[t["keyword"][:20]],
                textposition="top center",
                marker=dict(size=10 + t["papers_grupo"], color=colors.get(t["categoria"], "#666")),
                name=t["keyword"],
                showlegend=False,
            ),
            row=2, col=1,
        )

    fig.update_layout(
        title="Análisis de tendencias — LCDA Searcher",
        template="plotly_white",
        height=800,
        legend=dict(orientation="h", y=1.02),
    )
    fig.update_xaxes(title_text="Año", row=1, col=1)
    fig.update_yaxes(title_text="Papers del grupo", row=1, col=1)
    fig.update_xaxes(title_text="Crecimiento global (pendiente)", row=2, col=1)
    fig.update_yaxes(title_text="Cobertura del grupo", row=2, col=1)

    fig.write_html(str(output_dir / "tendencias.html"), include_plotlyjs="cdn")


def run_trends(
    db: Database,
    mailto: str,
    output_dir: Path,
    ventana_anios: int = 6,
    top_n: int = 15,
) -> dict[str, Any]:
    t0 = time.time()
    trends = compute_trends(db, mailto, ventana_anios, top_n)
    write_trends_outputs(db, trends, output_dir)
    dur = time.time() - t0
    db.log_metrica("trends", dur, f"{len(trends)} keywords analizadas")
    return {"keywords_analizadas": len(trends), "duracion_seg": dur, "trends": trends}
