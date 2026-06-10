#!/usr/bin/env python3
"""Descarga PDFs IEEE Xplore usando perfil Chrome persistente (sesión institucional)."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "lcda.db"
PDF_DIR = ROOT / "data" / "pdfs"
PROFILE_DIR = ROOT / "data" / "ieee_browser_profile"
MANIFEST_PATH = ROOT / "data" / "ieee_manifest.csv"


def arnumber_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"/document/(\d+)", url)
    return m.group(1) if m else None


def pdf_candidate_urls(arnumber: str) -> list[str]:
    return [
        f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arnumber}",
        f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={arnumber}",
    ]


def get_paper(conn: sqlite3.Connection, paper_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, titulo, doi, url_ieee, url_doi FROM papers WHERE id = ?",
        (paper_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "titulo": row[1],
        "doi": row[2],
        "url_ieee": row[3],
        "url_doi": row[4],
    }


def resolve_arnumber(paper: dict) -> str | None:
    ar = arnumber_from_url(paper.get("url_ieee"))
    if ar:
        return ar
    doi = paper.get("doi") or ""
    if not doi.startswith("10.1109"):
        return None
    import subprocess

    proc = subprocess.run(
        ["curl", "-sI", "-L", f"https://doi.org/{doi}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    for line in proc.stdout.splitlines():
        if line.lower().startswith("location:"):
            loc = line.split(":", 1)[1].strip()
            ar = arnumber_from_url(loc)
            if ar:
                return ar
    return None


def append_manifest(paper_id: int, doi: str, status: str, path: str, notes: str = "") -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = "paper_id,doi,status,local_path,notes\n"
    if not MANIFEST_PATH.exists():
        MANIFEST_PATH.write_text(header, encoding="utf-8")
    safe_notes = notes.replace('"', "'")[:300]
    line = f'{paper_id},"{doi}",{status},"{path}","{safe_notes}"\n'
    with MANIFEST_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


def is_pdf_bytes(data: bytes) -> bool:
    return len(data) > 10_000 and data[:4] == b"%PDF"


def connect_context(playwright, headless: bool, cdp_url: str | None):
    if cdp_url:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            raise RuntimeError("Chrome CDP sin contextos — abre al menos una pestaña")
        return browser.contexts[0], browser
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        channel="chrome",
        headless=headless,
        accept_downloads=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    return context, None


def fetch_pdf_from_context(page, urls: list[str]) -> bytes | None:
    for url in urls:
        try:
            resp = page.request.get(url, timeout=60_000)
            body = resp.body()
            if is_pdf_bytes(body):
                return body
        except Exception:
            continue
    # Visor stamp.jsp: iframe con getPDF
    stamp = urls[-1]
    try:
        page.goto(stamp, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2000)
        for frame in page.frames:
            src = frame.url or ""
            if "getPDF" in src or src.endswith(".pdf"):
                resp = page.request.get(src, timeout=60_000)
                body = resp.body()
                if is_pdf_bytes(body):
                    return body
        iframe = page.locator('iframe[src*="getPDF"], iframe[src*=".pdf"]').first
        if iframe.count():
            src = iframe.get_attribute("src")
            if src:
                if src.startswith("/"):
                    src = f"https://ieeexplore.ieee.org{src}"
                resp = page.request.get(src, timeout=60_000)
                body = resp.body()
                if is_pdf_bytes(body):
                    return body
    except Exception:
        pass
    return None


def login_institutional(headless: bool, wait_sec: int) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Instala Playwright: pip install playwright && playwright install chrome")
        sys.exit(1)

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("Abriendo IEEE Xplore — inicia sesión institucional UdeC en la ventana Chrome.")
    print(f"Esperando {wait_sec}s para que completes el login...")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",
            headless=headless,
            accept_downloads=True,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://ieeexplore.ieee.org", wait_until="domcontentloaded", timeout=60_000)
        time.sleep(wait_sec)
        context.close()
    print("Perfil guardado en data/ieee_browser_profile/")


def download_with_playwright(
    paper_id: int, headless: bool = False, cdp_url: str | None = None
) -> Path | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Instala Playwright: pip install playwright && playwright install chrome")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    paper = get_paper(conn, paper_id)
    conn.close()
    if not paper:
        print(f"Paper id={paper_id} no encontrado")
        return None

    arnumber = resolve_arnumber(paper)
    if not arnumber:
        print(f"No se pudo resolver arnumber para paper {paper_id}")
        append_manifest(paper_id, paper.get("doi") or "", "no_arnumber", "", "sin document id")
        return None

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PDF_DIR / f"{paper_id}.pdf"
    urls = pdf_candidate_urls(arnumber)

    print(f"Paper {paper_id}: {paper['titulo'][:70]}...")
    print(f"  DOI: {paper.get('doi')}")
    print(f"  arnumber: {arnumber}")

    with sync_playwright() as p:
        context, browser = connect_context(p, headless, cdp_url)
        page = context.pages[0] if context.pages else context.new_page()
        doc_url = f"https://ieeexplore.ieee.org/document/{arnumber}/"
        page.goto(doc_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2000)

        pdf_bytes = fetch_pdf_from_context(page, urls)
        if cdp_url:
            print("  (Chrome CDP sigue abierto — no se cierra la sesión)")
        elif browser:
            browser.close()
        else:
            context.close()

    if not pdf_bytes:
        print("  Sin acceso PDF — ejecuta: python scripts/download_ieee_pdf.py --login")
        append_manifest(
            paper_id,
            paper.get("doi") or "",
            "login_required",
            "",
            "sin PDF; login institucional en perfil playwright",
        )
        return None

    out_path.write_bytes(pdf_bytes)
    print(f"  OK → {out_path} ({out_path.stat().st_size // 1024} KB)")
    append_manifest(paper_id, paper.get("doi") or "", "downloaded", str(out_path))
    return out_path


def papers_sin_pdf(conn: sqlite3.Connection, limit: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT id FROM papers
        WHERE doi LIKE '10.1109%'
        ORDER BY citado_por DESC
        """
    ).fetchall()
    pending = []
    for (pid,) in rows:
        if not (PDF_DIR / f"{pid}.pdf").exists():
            pending.append(pid)
        if len(pending) >= limit:
            break
    return pending


def main() -> None:
    parser = argparse.ArgumentParser(description="Descargar PDF IEEE para un paper de lcda.db")
    parser.add_argument("paper_id", type=int, nargs="?", default=None, help="ID en tabla papers")
    parser.add_argument("--batch", type=int, default=0, help="Descargar N papers IEEE sin PDF local")
    parser.add_argument("--all", action="store_true", help="Descargar todos los papers IEEE pendientes")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--login",
        action="store_true",
        help="Abre IEEE Xplore para guardar sesión institucional en el perfil local",
    )
    parser.add_argument("--wait", type=int, default=90, help="Segundos de espera en --login")
    parser.add_argument(
        "--cdp",
        default=None,
        help="URL CDP de Chrome con sesión IEEE (ej. http://127.0.0.1:9222)",
    )
    args = parser.parse_args()

    if args.login:
        login_institutional(args.headless, args.wait)
        return

    batch_n = 9999 if args.all else args.batch
    if batch_n > 0:
        conn = sqlite3.connect(DB_PATH)
        ids = papers_sin_pdf(conn, batch_n)
        conn.close()
        print(f"Lote: {len(ids)} papers → {ids}")
        ok, fail = 0, 0
        for i, pid in enumerate(ids, 1):
            print(f"\n[{i}/{len(ids)}]")
            if download_with_playwright(pid, headless=args.headless, cdp_url=args.cdp):
                ok += 1
            else:
                fail += 1
            if i < len(ids):
                time.sleep(2)
        print(f"\nResumen: {ok} ok, {fail} fallidos")
        return

    paper_id = args.paper_id if args.paper_id is not None else 4
    download_with_playwright(paper_id, headless=args.headless, cdp_url=args.cdp)


if __name__ == "__main__":
    main()
