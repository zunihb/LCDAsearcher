"""Enriquecimiento de abstracts vía IEEE Xplore con Playwright.

El flujo intenta primero llegar a la página del paper en IEEE Xplore,
extraer abstract/DOI/venue/PDF desde la ficha y, si no puede, usa la lista de
resultados como respaldo.
"""

from __future__ import annotations

import random
import re
import time
from typing import Any

from src.db import Database


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = text.replace("[::", "").replace("::]", "")
    return text


def _normalize_title(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9áéíóúñü\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _search_title(text: str) -> str:
    text = (text or "").strip()
    # Strip IEEE suffix (journal name at end)
    text = re.sub(r",?\s*IEEE\s+Trans\b.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r",?\s*IEEE\s+(Industrial|Power|Journal|Access|Trans)\b.*$", "", text, flags=re.IGNORECASE)
    # If IEEE is at the beginning, keep it (conference name)
    # If IEEE is in the middle or end as a suffix, strip it
    if not re.match(r"^\d{4}\s+IEEE\s+", text, flags=re.IGNORECASE):
        text = re.sub(r",?\s*IEEE\b.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bIn:\s*.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bVolume:\s*.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bVol\.?\s*\d+.*$", "", text, flags=re.IGNORECASE)
    # Strip trailing year
    text = re.sub(r"[,:]\s*\d{4}\s*$", "", text)
    text = re.sub(r"\b\d{4}\s*$", "", text)
    text = text.replace(chr(0x201c), "").replace(chr(0x201d), "").replace(chr(34), "")
    text = re.sub(r"\s+", " ", text).strip(" ,.;:-")
    return text


def _looks_like_noise(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return True
    noisy = [
        "index ieee",
        "call for papers",
        "society officers",
        "organizing committee",
        "paper index",
        "fellow, ieee",
        "ieee fellows",
        "editor's column",
    ]
    return any(x in t for x in noisy)


def _title_score(a: str, b: str) -> float:
    a_norm = _normalize_title(a)
    b_norm = _normalize_title(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0
    a_words = set(a_norm.split())
    b_words = set(b_norm.split())
    stop = {"a", "an", "the", "of", "for", "and", "or", "in", "on", "to", "with", "by"}
    a_words -= stop
    b_words -= stop
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / max(len(a_words), len(b_words))


def _meta_content(page, names: list[str]) -> str:
    for name in names:
        try:
            loc = page.locator(f'meta[name="{name}"]')
            if loc.count():
                value = loc.first.get_attribute("content") or ""
                if value.strip():
                    return value.strip()
        except Exception:
            continue
    return ""


def _extract_article_metadata(page) -> dict[str, str]:
    abstract = _meta_content(page, ["citation_abstract", "dc.Description", "description"])
    doi = _meta_content(page, ["citation_doi", "dc.Identifier", "prism.doi"])
    venue = _meta_content(
        page,
        ["citation_journal_title", "citation_conference_title", "citation_publication_title"],
    )
    pdf_url = _meta_content(page, ["citation_pdf_url"])
    article_url = page.url or ""
    if not article_url:
        article_url = _meta_content(page, ["citation_abstract_html_url", "citation_fulltext_html_url"])

    if not abstract:
        selectors = [
            "div.abstract-text",
            "div#abstract-text",
            "section#abstract",
            "div.abstract-text-row",
            "div.u-mb-1.abstract-text",
            "div[aria-label*='abstract' i]",
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                if loc.count():
                    abstract = _clean_text(loc.first.inner_text())
                    if abstract:
                        break
            except Exception:
                continue

    if not abstract:
        try:
            body_text = _clean_text(page.locator("body").inner_text())
            match = re.search(
                r"Abstract:\s*(.+?)(?:\s+(?:Published in:|Document Sections|Authors|Figures|References|Citations|Keywords|Metrics)\b)",
                body_text,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if match:
                abstract = _clean_text(match.group(1))
        except Exception:
            pass

    if not pdf_url:
        try:
            for selector in ["a[href*='stampPDF']", "a[href*='pdf']", "button[title*='PDF']"]:
                loc = page.locator(selector)
                if loc.count():
                    href = loc.first.get_attribute("href") or ""
                    if href:
                        if href.startswith("/"):
                            href = f"https://ieeexplore.ieee.org{href}"
                        pdf_url = href
                        break
        except Exception:
            pass

    return {
        "abstract": _clean_text(abstract),
        "doi": doi.strip(),
        "venue": venue.strip(),
        "url_pdf": pdf_url.strip(),
        "url_ieee": article_url.strip(),
    }


def _search_candidates(page, titulo: str) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    try:
        anchors = page.locator("a[href*='/document/']")
        count = min(anchors.count(), 40)
        for i in range(count):
            try:
                a = anchors.nth(i)
                href = a.get_attribute("href") or ""
                text = _clean_text(a.inner_text())
                if not href or not text:
                    continue
                if href.startswith("/"):
                    href = f"https://ieeexplore.ieee.org{href}"
                candidates.append({"href": href, "title": text})
            except Exception:
                continue
    except Exception:
        return []

    scored = sorted(
        candidates,
        key=lambda c: _title_score(titulo, c["title"]),
        reverse=True,
    )
    return [c for c in scored if _title_score(titulo, c["title"]) >= 0.5]


def _search_ieee_paper_playwright(titulo: str, page) -> dict[str, Any] | None:
    try:
        if _looks_like_noise(titulo):
            return None
        query = _search_title(titulo).replace('"', '').replace("'", "")
        if _looks_like_noise(query) or len(query) < 12:
            return None
        search_url = f"https://ieeexplore.ieee.org/search/searchresult.jsp?newsearch=true&queryText={query}"
        page.goto(search_url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2500)

        if page.locator("#captcha").count() or page.locator("iframe[title*='captcha' i]").count():
            return None

        candidates = _search_candidates(page, titulo)
        if candidates:
            page.goto(candidates[0]["href"], wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2000)
            meta = _extract_article_metadata(page)
            if meta["abstract"] and len(meta["abstract"]) >= 50:
                meta["title_match"] = candidates[0]["title"]
                return meta

        # Fallback: usar lo que aparezca en la página de búsqueda.
        text = _clean_text(page.locator("body").inner_text())
        if text and len(text) > 200:
            return {
                "abstract": "",
                "doi": "",
                "venue": "",
                "url_pdf": "",
                "url_ieee": page.url,
                "title_match": candidates[0]["title"] if candidates else "",
                "search_snippet": text[:600],
            }
        return None
    except Exception:
        return None


def enrich_paper_ieee_playwright(db: Database, paper: dict[str, Any], page) -> bool:
    result = _search_ieee_paper_playwright(paper["titulo"], page)
    if not result:
        return False

    abstract = result.get("abstract") or ""
    existing = paper.get("abstract") or ""
    if abstract and existing and len(existing) >= len(abstract):
        return False

    doi = result.get("doi") or paper.get("doi")
    url_doi = f"https://doi.org/{doi}" if doi else paper.get("url_doi")
    url_ieee = result.get("url_ieee") or paper.get("url_ieee")
    url_pdf = result.get("url_pdf") or paper.get("url_pdf")
    venue = result.get("venue") or paper.get("venue")

    if not abstract:
        return False

    db.upsert_paper(
        titulo=paper["titulo"],
        abstract=abstract or None,
        doi=doi,
        url_doi=url_doi,
        url_ieee=url_ieee,
        url_pdf=url_pdf,
        scholar_pub_id=paper.get("scholar_pub_id"),
        anio=paper.get("anio"),
        citado_por=paper.get("citado_por") or 0,
        autores_texto=paper.get("autores_texto"),
        venue=venue,
        url_scholar=paper.get("url_scholar"),
    )
    return True


def run_abstracts_ieee_playwright(
    db: Database,
    limit: int | None = None,
    batch_size: int = 10,
    batch_pause_sec: float = 5.0,
    min_delay: float = 1.5,
    max_delay: float = 3.0,
) -> dict[str, Any]:
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

    print(f"      {total} papers sin abstract · fuente ieee-playwright", flush=True)
    print(f"      pausa entre papers: {min_delay}-{max_delay}s, lote cada {batch_size} ({batch_pause_sec}s)", flush=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = context.new_page()

        for i, paper in enumerate(papers, 1):
            titulo_corto = (paper["titulo"] or "")[:60]
            print(f"      [{i}/{total}] {titulo_corto}...", end="", flush=True)
            success = enrich_paper_ieee_playwright(db, paper, page)
            if success:
                ok += 1
                print(" ✓", flush=True)
            else:
                fail += 1
                print(" ✗", flush=True)

            time.sleep(random.uniform(min_delay, max_delay))
            if batch_size > 0 and i < total and i % batch_size == 0:
                print(f"      -- pausa de lote ({batch_pause_sec}s) --", flush=True)
                time.sleep(batch_pause_sec)

        browser.close()

    dur = time.time() - t0
    db.log_metrica("abstracts_ieee_pw", dur, f"{ok}/{total} con abstract vía IEEE Playwright")
    return {
        "pendientes": total,
        "enriquecidos": ok,
        "sin_match": fail,
        "duracion_seg": dur,
    }
