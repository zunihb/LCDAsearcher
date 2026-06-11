#!/usr/bin/env python3
"""Limpia papers ruido de la BD: índices, society news, call for papers, etc.

Ejecutar:
  .venv/bin/python scripts/clean_noise.py          # preview (no borra)
  .venv/bin/python scripts/clean_noise.py --delete  # ejecuta limpieza
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db import Database


def _is_noise(titulo: str) -> tuple[bool, str]:
    """Retorna (es_ruido, razón)."""
    t = (titulo or "").strip()
    tl = t.lower()

    # INDEX
    if re.search(r"\bindex\b", tl) and any(
        x in tl for x in ["ieee", "trans", "magazine", "power", "sensor", "energy", "industry", "vol."]
    ):
        return True, "index"

    # CALL FOR
    if "call for" in tl:
        return True, "call_for"

    # OFFICERS
    if "officers" in tl:
        return True, "officers"

    # SOCIETY NEWS
    if "society news" in tl or "society news" in tl:
        return True, "society_news"

    # COMMITTEE
    if "committee" in tl:
        return True, "committee"

    # EDITOR
    if tl.startswith("editor") or "editor's column" in tl:
        return True, "editor"

    # NOMINATIONS
    if "nominations" in tl:
        return True, "nominations"

    # AD INDEX
    if tl == "ad index":
        return True, "ad_index"

    # LIBRARY
    if "elearning library" in tl or "e-learning library" in tl:
        return True, "library"

    # URLS
    if t.startswith("http"):
        return True, "url"

    # FRAGMENTS (very short or known garbage)
    if len(t) < 12:
        return True, "short"
    if any(x in t for x in ["Babu PC", "Casey, LF", "CERN, Geneva", "R. Zbikowski"]):
        return True, "fragment"

    # OFF-TOPIC: medical
    if any(x in tl for x in ["sleep apnea", "sleep disordered", "sleep apnea in"]):
        return True, "medical"

    # OFF-TOPIC: agriculture
    if any(x in tl for x in ["zeamais", "lucerne", "insecticidal", "agricultural supply", "crop water stress"]):
        return True, "agriculture"

    # OFF-TOPIC: anthropology/biology
    if "tribe" in tl and "australia" in tl:
        return True, "anthropology"
    if "shore fishes" in tl:
        return True, "biology"

    # OFF-TOPIC: physics/math
    if any(x in tl for x in ["leptoquarks", "cern collider"]):
        return True, "physics"
    if any(x in tl for x in ["sobolev", "extremal polynomials", "randi", "asymptotic behavior"]):
        return True, "math"

    # OFF-TOPIC: education (Spanish)
    if any(x in tl for x in ["rendimiento acad", "familia, introducci", "competencias investigativas", "desarrollar competencias"]):
        return True, "education"

    # POLISH
    if any(x in tl for x in ["przegl", "polska sekcja", "działal", "osiągni"]):
        return True, "polish"

    # DEPARTMENT NEWS
    if "process industries department" in tl and ("mining" in tl or "committee" in tl):
        return True, "dept_news"

    return False, ""


def main() -> int:
    delete_mode = "--delete" in sys.argv
    db = Database(ROOT / "data" / "lcda.db")

    papers = db.query("""
        SELECT id, titulo, anio
        FROM papers
        WHERE (abstract IS NULL OR trim(abstract) = '')
        ORDER BY titulo
    """)

    noise: list[dict] = []
    reasons: dict[str, int] = {}

    for p in papers:
        is_noise, reason = _is_noise(p["titulo"])
        if is_noise:
            noise.append(p)
            reasons[reason] = reasons.get(reason, 0) + 1

    print(f"Total papers sin abstract: {len(papers)}")
    print(f"Ruido detectado: {len(noise)}")
    print(f"\nPor categoría:")
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")

    print(f"\nEjemplos:")
    for p in noise[:30]:
        print(f"  [{p['id']}] {p['titulo'][:90]}")

    if len(noise) > 30:
        print(f"  ... y {len(noise) - 30} más")

    if not delete_mode:
        print(f"\n  [DRY RUN] Para borrar: .venv/bin/python scripts/clean_noise.py --delete")
        return 0

    # DELETE
    print(f"\n  Borrando {len(noise)} papers...")
    with db.connect() as conn:
        ids = [p["id"] for p in noise]
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM paper_keywords WHERE paper_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM paper_autores WHERE paper_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM autorias WHERE paper_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM citas WHERE paper_citado_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM papers WHERE id IN ({placeholders})", ids)

    stats = db.query_one(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN abstract IS NOT NULL AND trim(abstract) <> '' THEN 1 ELSE 0 END) AS con_abstract "
        "FROM papers"
    )
    print(f"  Papers restantes: {stats['total']}")
    print(f"  Con abstract: {stats['con_abstract']} ({100*stats['con_abstract']/stats['total']:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
