#!/usr/bin/env python3
"""Consolidación de keywords fragmentadas.

El LLM generó keywords con capitalización y acentos inconsistentes, así que
"Eficiencia Energética", "eficiencia energética" y "eficiencia energetica"
son 3 filas distintas pero apuntan al mismo concepto (mismo keyword_norm).

Este script:
1. Agrupa todas las keywords por keyword_norm.
2. Para cada grupo, elige un canónico (la variante con más papers).
3. Actualiza termino_canonico para todas las variantes del grupo.
4. Registra aliases en keyword_aliases.
5. (Opcional) --merge-pk: reescribe paper_keywords para que cada paper use
   un solo keyword_id por concepto (evita doble conteo).

Uso:
    python scripts/fix_keywords.py            # solo consolida canónicos
    python scripts/fix_keywords.py --merge-pk # + merge paper_keywords
    python scripts/fix_keywords.py --dry-run  # muestra cambios sin aplicar
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from src.db import Database

load_dotenv()


def run(db_path: str, merge_pk: bool = False, dry_run: bool = False) -> None:
    db = Database(db_path)

    # 1. Encontrar grupos con 2+ variantes
    groups = db.query(
        "SELECT keyword_norm, COUNT(*) n FROM keywords GROUP BY keyword_norm HAVING n > 1 ORDER BY n DESC"
    )
    print(f"Grupos con 2+ variantes: {len(groups)}")
    total_fixed = 0
    total_merged_links = 0

    for group in groups:
        norm = group["keyword_norm"]

        # Keywords del grupo ordenadas por # papers (la que más papers tiene = canónico)
        variants = db.query(
            """
            SELECT k.id, k.termino, k.termino_canonico,
                   COUNT(DISTINCT pk.paper_id) AS paper_count
            FROM keywords k
            LEFT JOIN paper_keywords pk ON k.id = pk.keyword_id
            WHERE k.keyword_norm = ?
            GROUP BY k.id
            ORDER BY paper_count DESC, length(k.termino) ASC
            """,
            (norm,),
        )
        if not variants:
            continue

        # El canónico: entre los de mayor paper_count, preferir:
        # 1. Más papers
        # 2. Tiene letras acentuadas (forma española bien escrita)
        # 3. Más larga (evita siglas como "FCS-MPC" sobre "control predictivo de conjunto finito")
        max_papers = variants[0]["paper_count"]
        top = [v for v in variants if v["paper_count"] == max_papers]

        def _score(v: dict) -> tuple:
            t = v["termino"]
            has_accent = any(c in t for c in "áéíóúñüÁÉÍÓÚÑÜ")
            all_upper = t.isupper() or (len(t) <= 8 and t.replace("-", "").isupper())
            return (has_accent, not all_upper, len(t))

        canonical_term = max(top, key=_score)["termino"]

        changed = False
        for v in variants:
            if v["termino_canonico"] != canonical_term:
                changed = True
                if not dry_run:
                    db.update_keyword_canonical(v["id"], canonical_term)
                    db.upsert_keyword_alias(v["termino"], canonical_term, fuente="consolidation")

        if changed:
            total_fixed += 1
            if dry_run:
                print(
                    f"  [DRY] {norm!r} → canónico={canonical_term!r}  ({len(variants)} variantes)"
                )

    print(f"Canónicos actualizados: {total_fixed} grupos")

    # 2. (Opcional) Merge paper_keywords para evitar doble conteo
    if merge_pk:
        print("\nMerging paper_keywords (elimina duplicados por keyword_norm)...")
        # Para cada (paper_id, keyword_norm), conservar solo el keyword_id del canónico
        dup_links = db.query(
            """
            SELECT pk.paper_id, k.keyword_norm,
                   MIN(k.id) AS keep_id,
                   COUNT(DISTINCT pk.keyword_id) AS n_kw
            FROM paper_keywords pk
            JOIN keywords k ON k.id = pk.keyword_id
            GROUP BY pk.paper_id, k.keyword_norm
            HAVING n_kw > 1
            """
        )
        print(f"  Paper-keywords con duplicados por norm: {len(dup_links)}")

        for row in dup_links:
            paper_id = row["paper_id"]
            norm2 = row["keyword_norm"]
            keep_id = row["keep_id"]

            if dry_run:
                total_merged_links += 1
                continue

            # Obtener todos los keyword_ids del grupo para este paper
            kw_ids = db.query(
                """
                SELECT pk.keyword_id FROM paper_keywords pk
                JOIN keywords k ON k.id = pk.keyword_id
                WHERE pk.paper_id = ? AND k.keyword_norm = ?
                """,
                (paper_id, norm2),
            )
            ids_to_remove = [r["keyword_id"] for r in kw_ids if r["keyword_id"] != keep_id]

            with db.connect() as conn:
                for kid in ids_to_remove:
                    conn.execute(
                        "DELETE FROM paper_keywords WHERE paper_id=? AND keyword_id=?",
                        (paper_id, kid),
                    )
            total_merged_links += len(ids_to_remove)

        print(f"  Links duplicados eliminados: {total_merged_links}")

    # 3. Estadísticas finales
    stats = db.query_one(
        """
        SELECT
            (SELECT COUNT(*) FROM keywords) AS total_kw,
            (SELECT COUNT(*) FROM keywords WHERE termino_canonico IS NOT NULL AND termino_canonico != termino) AS con_canonico,
            (SELECT COUNT(DISTINCT keyword_norm) FROM keywords) AS normas_unicas,
            (SELECT COUNT(*) FROM keyword_aliases) AS aliases
        """
    )
    print("\n=== Estadísticas finales ===")
    if stats:
        print(f"  Total keywords:      {stats['total_kw']}")
        print(f"  Con canónico propio: {stats['con_canonico']}")
        print(f"  Normas únicas:       {stats['normas_unicas']}")
        print(f"  Aliases:             {stats['aliases']}")


def rebuild_norms(db_path: str) -> None:
    """Recalcula keyword_norm para todos los registros con la función actualizada."""
    from src.topic_search import normalize_keyword as nk

    db = Database(db_path)
    keywords = db.query("SELECT id, termino FROM keywords")
    print(f"Recalculando keyword_norm para {len(keywords)} keywords...")
    updated = 0
    with db.connect() as conn:
        for kw in keywords:
            new_norm = nk(kw["termino"])
            conn.execute(
                "UPDATE keywords SET keyword_norm=? WHERE id=?",
                (new_norm, kw["id"]),
            )
            updated += 1
        if updated % 5000 == 0 and updated:
            print(f"  {updated}/{len(keywords)}...")
    print(f"keyword_norm actualizado para {updated} keywords")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Consolida keywords fragmentadas")
    parser.add_argument("--db", default="data/lcda.db", help="Ruta a la BD")
    parser.add_argument("--merge-pk", action="store_true", help="Merge paper_keywords duplicadas")
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra cambios, no aplica")
    parser.add_argument("--rebuild-norms", action="store_true", help="Recalcular keyword_norm con la función actualizada")
    args = parser.parse_args()
    if args.rebuild_norms:
        rebuild_norms(args.db)
    run(args.db, merge_pk=args.merge_pk, dry_run=args.dry_run)
