"""Extracción acotada de papers citantes (top-N, anti-bloqueo)."""

from __future__ import annotations

import random
import time
from typing import Any

from src.db import Database


def _pause(min_sec: float = 3, max_sec: float = 7) -> None:
    time.sleep(random.uniform(min_sec, max_sec))


def fetch_citantes_for_paper(
    paper: dict,
    max_citantes: int,
    use_proxies: bool = False,
) -> list[dict[str, Any]]:
    """Intenta obtener citantes via scholarly.citedby; retorna lista vacía si falla."""
    try:
        if use_proxies:
            from scholarly import ProxyGenerator

            pg = ProxyGenerator()
            pg.FreeProxies()
            from scholarly import scholarly

            scholarly.use_proxy(pg)
        else:
            from scholarly import scholarly

        pub_stub = {"title": paper["titulo"], "num_citations": paper.get("citado_por", 0)}
        if paper.get("scholar_pub_id"):
            pub_stub["pub_url"] = paper["scholar_pub_id"]

        _pause()
        cited = scholarly.citedby(pub_stub)
        results = []
        for i, c in enumerate(cited):
            if i >= max_citantes:
                break
            bib = c.get("bib", {}) or {}
            results.append(
                {
                    "titulo_citante": bib.get("title") or c.get("title") or "Sin título",
                    "autores_citante": ", ".join(bib.get("author", []) or []),
                    "anio_citante": _safe_int(bib.get("pub_year")),
                    "venue_citante": bib.get("venue") or bib.get("journal") or "",
                }
            )
            if i % 5 == 0:
                _pause(1, 3)
        return results
    except Exception:
        return []


def _safe_int(val, default=None):
    try:
        return int(val) if val else default
    except (TypeError, ValueError):
        return default


def run_citations(
    db: Database,
    investigadores: list[dict],
    top_papers: int = 5,
    max_citantes: int = 50,
    use_proxies: bool = False,
) -> dict[str, Any]:
    t0 = time.time()
    total = 0
    for inv in investigadores:
        sid = inv["scholar_id"]
        papers = db.get_top_papers_por_investigador(sid, limit=top_papers)
        for paper in papers:
            existing = db.query(
                "SELECT COUNT(*) AS n FROM citas WHERE paper_citado_id = ?",
                (paper["id"],),
            )
            if existing and existing[0]["n"] >= max_citantes:
                continue

            citantes = fetch_citantes_for_paper(paper, max_citantes, use_proxies)
            for c in citantes:
                db.upsert_cita(
                    paper_citado_id=paper["id"],
                    titulo_citante=c["titulo_citante"],
                    autores_citante=c.get("autores_citante"),
                    anio_citante=c.get("anio_citante"),
                    venue_citante=c.get("venue_citante"),
                )
                total += 1

    dur = time.time() - t0
    db.log_metrica("citations", dur, f"{total} citantes extraídos")
    return {"citantes": total, "duracion_seg": dur}
