#!/usr/bin/env python3
"""Exporta matriz investigador-keyword y matches temáticos del piloto."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from src.db import Database
from src.matching import write_matching_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Exportar matches temáticos de LCDA Searcher")
    parser.add_argument("--config", default="config.yaml", help="Ruta a config.yaml")
    parser.add_argument("--recent-since", type=int, default=2021, help="Año mínimo para matches recientes")
    args = parser.parse_args()

    with Path(args.config).open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    db = Database(Path(cfg.get("data_dir", "data")) / "lcda.db")
    outputs = write_matching_outputs(
        db,
        Path(cfg.get("output_dir", "output")),
        recent_since=args.recent_since,
    )
    for name, path in outputs.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
