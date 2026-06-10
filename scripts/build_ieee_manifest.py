#!/usr/bin/env python3
"""Genera output/ieee_pdfs_manifest.csv desde lcda.db y data/pdfs/."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "lcda.db"
PDF_DIR = ROOT / "data" / "pdfs"
OUT = ROOT / "output" / "ieee_pdfs_manifest.csv"

NOTAS = {
    299: "libro IEEE (ISBN 9780470546840), sin arnumber en Xplore",
}


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        """
        SELECT id, titulo, doi, anio, citado_por
        FROM papers WHERE doi LIKE '10.1109%'
        ORDER BY citado_por DESC
        """
    ).fetchall()
    conn.close()

    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["paper_id", "titulo", "doi", "anio", "citado_por", "estado", "ruta_pdf", "notas"])
        for pid, titulo, doi, anio, citas in rows:
            path = PDF_DIR / f"{pid}.pdf"
            if path.exists():
                w.writerow([pid, titulo, doi, anio, citas, "descargado", f"data/pdfs/{pid}.pdf", ""])
            elif pid in NOTAS:
                w.writerow([pid, titulo, doi, anio, citas, "sin_acceso", "", NOTAS[pid]])
            else:
                w.writerow([pid, titulo, doi, anio, citas, "pendiente", "", ""])

    desc = sum(1 for line in OUT.read_text().splitlines() if ",descargado," in line)
    print(f"{OUT}: {len(rows)} papers, {desc} descargados")


if __name__ == "__main__":
    main()
