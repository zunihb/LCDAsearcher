"""Enriquecimiento de abstracts vía Google Scholar con Playwright.

Usa un navegador Chrome real para evitar los bloqueos que sufre `scholarly`.
Extrae el snippet de abstract que aparece en los resultados de búsqueda.
"""

from __future__ import annotations

import re
import time
import random
from typing import Any

from src.db import Database


def _clean_text(text: str) -> str:
    """Limpia texto extraído de HTML."""
    text = re.sub(r"\s+", " ", text).strip()
    # Quitar "..." al final (snippet truncado)
    text = text.rstrip("…").rstrip("...").strip()
    return text


def _scholar_search_abstract_playwright(
    titulo: str,
    page,
) -> dict[str, Any] | None:
    """Busca un paper en Scholar y extrae el abstract del snippet."""
    try:
        # Construir URL de búsqueda
        query = titulo.replace('"', '').replace("'", "")
        url = f"https://scholar.google.com/scholar?q=%22{query}%22"

        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # Esperar a que carguen los resultados
        try:
            page.wait_for_selector(".gs_r.gs_or.gs_scl", timeout=10000)
        except Exception:
            # Sin resultados o bloqueado
            return None

        # Verificar si hay CAPTCHA
        if page.query_selector("#gs_captcha_ccl"):
            return None

        # Tomar el primer resultado
        result = page.query_selector(".gs_r.gs_or.gs_scl")
        if not result:
            return None

        # Extraer título del resultado para verificar match
        title_el = result.query_selector(".gs_rt a")
        if not title_el:
            # Puede ser un resultado sin link
            title_el = result.query_selector(".gs_rt")

        result_title = title_el.inner_text() if title_el else ""

        # Verificar que el título sea similar (al menos 50% de overlap)
        t1 = set(titulo.lower().split())
        t2 = set(result_title.lower().split())
        if t1 and t2:
            overlap = len(t1 & t2) / max(len(t1), len(t2))
            if overlap < 0.3:
                return None

        # Extraer snippet (abstract)
        snippet_el = result.query_selector(".gs_rs")
        abstract = ""
        if snippet_el:
            abstract = _clean_text(snippet_el.inner_text())

        # Extraer link del paper
        link = ""
        if title_el:
            link = title_el.get_attribute("href") or ""

        # Extraer año
        year = None
        info_el = result.query_selector(".gs_a")
        if info_el:
            info_text = info_el.inner_text()
            year_match = re.search(r"\b(19|20)\d{2}\b", info_text)
            if year_match:
                year = int(year_match.group())

        if not abstract or len(abstract) < 50:
            return None

        return {
            "abstract": abstract,
            "url_scholar": link,
            "year": year,
        }

    except Exception:
        return None


def enrich_paper_scholar_playwright(
    db: Database,
    paper: dict[str, Any],
    page,
) -> bool:
    """ Enriquece un paper con abstract de Scholar vía Playwright. """
    result = _scholar_search_abstract_playwright(paper["titulo"], page)
    if not result:
        return False

    abstract = result["abstract"]
    if not abstract:
        return False

    # Solo actualizar si no tenemos abstract o si el nuevo es más largo
    existing = paper.get("abstract") or ""
    if existing and len(existing) >= len(abstract):
        return False

    paper_id = db.upsert_paper(
        titulo=paper["titulo"],
        abstract=abstract,
        url_scholar=result.get("url_scholar") or paper.get("url_scholar"),
        scholar_pub_id=paper.get("scholar_pub_id"),
        anio=result.get("year") or paper.get("anio"),
        citado_por=paper.get("citado_por") or 0,
        autores_texto=paper.get("autores_texto"),
        venue=paper.get("venue"),
    )
    return paper_id > 0


def run_abstracts_playwright(
    db: Database,
    limit: int | None = None,
    batch_size: int = 5,
    batch_pause_sec: float = 10.0,
    min_delay: float = 3.0,
    max_delay: float = 8.0,
) -> dict[str, Any]:
    """Pipeline de abstracts vía Playwright + Google Scholar."""
    import os
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

    from playwright.sync_api import sync_playwright

    t0 = time.time()
    papers = db.get_papers_sin_abstract()
    if limit:
        papers = papers[:limit]
    total = len(papers)
    ok = 0
    fail = 0

    print(f"      {total} papers sin abstract · fuente scholarly-playwright", flush=True)
    print(f"      pausa entre papers: {min_delay}-{max_delay}s, lote cada {batch_size} ({batch_pause_sec}s)", flush=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = context.new_page()

        for i, paper in enumerate(papers, 1):
            titulo_corto = (paper["titulo"] or "")[:60]
            print(f"      [{i}/{total}] {titulo_corto}...", end="", flush=True)

            success = enrich_paper_scholar_playwright(db, paper, page)

            if success:
                ok += 1
                print(" ✓", flush=True)
            else:
                fail += 1
                print(" ✗", flush=True)

            # Delay aleatorio entre papers
            delay = random.uniform(min_delay, max_delay)
            time.sleep(delay)

            # Pausa de lote
            if batch_size > 0 and i < total and i % batch_size == 0:
                print(f"      -- pausa de lote ({batch_pause_sec}s) --", flush=True)
                time.sleep(batch_pause_sec)

        browser.close()

    dur = time.time() - t0
    db.log_metrica("abstracts_playwright", dur, f"{ok}/{total} con abstract vía Scholar Playwright")
    return {
        "pendientes": total,
        "enriquecidos": ok,
        "sin_match": fail,
        "duracion_seg": dur,
    }
