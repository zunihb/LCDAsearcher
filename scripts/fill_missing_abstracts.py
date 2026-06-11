#!/usr/bin/env python3
"""Completa abstracts faltantes usando Scholar Playwright.

Ejecutar:
  .venv/bin/python scripts/fill_missing_abstracts.py [--limit N]

Manejo:
  - Ctrl+C para detener limpiamente
  - Resume automático: solo procesa papers sin abstract
  - Pausas anti-bloqueo: 4-7s entre papers, 20s cada 10 papers
"""

from __future__ import annotations

import argparse
import random
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import Database
from src.abstracts_pw import enrich_paper_scholar_playwright

STOP = False


def _handle_sigint(sig, frame):
    global STOP
    STOP = True
    print("\n  [!] Deteniendo después del paper actual...")


signal.signal(signal.SIGINT, _handle_sigint)


def main() -> int:
    parser = argparse.ArgumentParser(description="Completa abstracts faltantes con Scholar Playwright")
    parser.add_argument("--limit", type=int, default=None, help="Máximo de papers a procesar")
    parser.add_argument("--batch-size", type=int, default=10, help="Papers por lote")
    parser.add_argument("--batch-pause", type=float, default=20.0, help="Pausa entre lotes (seg)")
    parser.add_argument("--min-delay", type=float, default=4.0, help="Delay mínimo entre papers")
    parser.add_argument("--max-delay", type=float, default=7.0, help="Delay máximo entre papers")
    args = parser.parse_args()

    db = Database(ROOT / "data" / "lcda.db")
    papers = db.get_papers_sin_abstract()
    if args.limit:
        papers = papers[: args.limit]
    total = len(papers)
    print(f"  {total} papers sin abstract · fuente scholarly-playwright")
    print(f"  delay: {args.min_delay}-{args.max_delay}s · lote: {args.batch_size} ({args.batch_pause}s)")

    if total == 0:
        print("  Sin papers pendientes.")
        return 0

    from playwright.sync_api import sync_playwright

    filled = 0
    failed = 0
    t0 = time.time()

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
            if STOP:
                print(f"\n  [!] Detenido por usuario en paper {i}/{total}")
                break

            titulo_corto = (paper["titulo"] or "")[:60]
            try:
                ok = enrich_paper_scholar_playwright(db, paper, page)
                if ok:
                    filled += 1
                    row = db.query_one("select abstract from papers where id=?", (paper["id"],))
                    alen = len((row or {}).get("abstract") or "")
                    print(f"  [{i}/{total}] ✓ len={alen} :: {titulo_corto}")
                else:
                    failed += 1
                    print(f"  [{i}/{total}] ✗ :: {titulo_corto}")
            except Exception as e:
                failed += 1
                print(f"  [{i}/{total}] ERR {e} :: {titulo_corto}")
                # If it's a browser crash, try to recover
                if "Target closed" in str(e) or "Browser" in str(e):
                    print("  [!] Browser error — reiniciando...")
                    try:
                        page.close()
                    except Exception:
                        pass
                    try:
                        page = context.new_page()
                    except Exception:
                        print("  [!] No se pudo reiniciar. Abortando.")
                        break

            time.sleep(random.uniform(args.min_delay, args.max_delay))

            if args.batch_size > 0 and i < total and i % args.batch_size == 0:
                elapsed = time.time() - t0
                rate = filled / elapsed * 60 if elapsed > 0 else 0
                remaining = total - i
                eta_min = remaining / (rate / 60) if rate > 0 else 0
                print(
                    f"  --- pausa {args.batch_pause}s | {filled} completados | "
                    f"{rate:.1f}/min | ETA ~{eta_min:.0f} min ---"
                )
                time.sleep(args.batch_pause)

        browser.close()

    dur = time.time() - t0
    db.log_metrica(
        "abstracts_scholar_pw",
        dur,
        f"{filled}/{total} completados, {failed} fallidos",
    )
    print(f"\n  Resumen: {filled} completados, {failed} fallidos en {dur/60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
