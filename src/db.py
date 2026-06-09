"""Esquema SQLite y helpers de inserción/consulta."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS investigadores (
    scholar_id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    afiliacion TEXT,
    citas_total INTEGER DEFAULT 0,
    indice_h INTEGER DEFAULT 0,
    indice_i10 INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scholar_pub_id TEXT,
    titulo TEXT NOT NULL,
    abstract TEXT,
    anio INTEGER,
    venue TEXT,
    citado_por INTEGER DEFAULT 0,
    autores_texto TEXT,
    UNIQUE(scholar_pub_id),
    UNIQUE(titulo)
);

CREATE TABLE IF NOT EXISTS autorias (
    scholar_id TEXT NOT NULL,
    paper_id INTEGER NOT NULL,
    PRIMARY KEY (scholar_id, paper_id),
    FOREIGN KEY (scholar_id) REFERENCES investigadores(scholar_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    termino TEXT NOT NULL UNIQUE,
    termino_canonico TEXT
);

CREATE TABLE IF NOT EXISTS paper_keywords (
    paper_id INTEGER NOT NULL,
    keyword_id INTEGER NOT NULL,
    PRIMARY KEY (paper_id, keyword_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id),
    FOREIGN KEY (keyword_id) REFERENCES keywords(id)
);

CREATE TABLE IF NOT EXISTS coautores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scholar_id TEXT NOT NULL,
    nombre TEXT NOT NULL,
    afiliacion TEXT,
    coautor_scholar_id TEXT,
    FOREIGN KEY (scholar_id) REFERENCES investigadores(scholar_id),
    UNIQUE(scholar_id, nombre)
);

CREATE TABLE IF NOT EXISTS citas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_citado_id INTEGER NOT NULL,
    titulo_citante TEXT NOT NULL,
    autores_citante TEXT,
    anio_citante INTEGER,
    venue_citante TEXT,
    FOREIGN KEY (paper_citado_id) REFERENCES papers(id),
    UNIQUE(paper_citado_id, titulo_citante)
);

CREATE TABLE IF NOT EXISTS tendencias_globales (
    keyword_id INTEGER NOT NULL,
    anio INTEGER NOT NULL,
    conteo_global INTEGER DEFAULT 0,
    fuente TEXT DEFAULT 'openalex',
    fecha_consulta TEXT,
    PRIMARY KEY (keyword_id, anio),
    FOREIGN KEY (keyword_id) REFERENCES keywords(id)
);

CREATE TABLE IF NOT EXISTS pipeline_metricas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paso TEXT NOT NULL,
    duracion_seg REAL,
    detalle TEXT,
    ejecutado_en TEXT DEFAULT (datetime('now'))
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def upsert_investigador(
        self,
        scholar_id: str,
        nombre: str,
        afiliacion: str | None = None,
        citas_total: int = 0,
        indice_h: int = 0,
        indice_i10: int = 0,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO investigadores (scholar_id, nombre, afiliacion, citas_total, indice_h, indice_i10)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scholar_id) DO UPDATE SET
                    nombre = excluded.nombre,
                    afiliacion = excluded.afiliacion,
                    citas_total = excluded.citas_total,
                    indice_h = excluded.indice_h,
                    indice_i10 = excluded.indice_i10
                """,
                (scholar_id, nombre, afiliacion, citas_total, indice_h, indice_i10),
            )

    def upsert_paper(
        self,
        titulo: str,
        scholar_pub_id: str | None = None,
        abstract: str | None = None,
        anio: int | None = None,
        venue: str | None = None,
        citado_por: int = 0,
        autores_texto: str | None = None,
    ) -> int:
        with self.connect() as conn:
            if scholar_pub_id:
                row = conn.execute(
                    "SELECT id FROM papers WHERE scholar_pub_id = ?", (scholar_pub_id,)
                ).fetchone()
                if row:
                    conn.execute(
                        """
                        UPDATE papers SET abstract=?, anio=?, venue=?, citado_por=?,
                        autores_texto=?, titulo=?
                        WHERE id=?
                        """,
                        (abstract, anio, venue, citado_por, autores_texto, titulo, row["id"]),
                    )
                    return row["id"]

            row = conn.execute("SELECT id FROM papers WHERE titulo = ?", (titulo,)).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE papers SET scholar_pub_id=COALESCE(?, scholar_pub_id),
                    abstract=?, anio=?, venue=?, citado_por=?, autores_texto=?
                    WHERE id=?
                    """,
                    (scholar_pub_id, abstract, anio, venue, citado_por, autores_texto, row["id"]),
                )
                return row["id"]

            cur = conn.execute(
                """
                INSERT INTO papers (scholar_pub_id, titulo, abstract, anio, venue, citado_por, autores_texto)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (scholar_pub_id, titulo, abstract, anio, venue, citado_por, autores_texto),
            )
            return cur.lastrowid

    def add_autoria(self, scholar_id: str, paper_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO autorias (scholar_id, paper_id) VALUES (?, ?)",
                (scholar_id, paper_id),
            )

    def upsert_coautor(
        self,
        scholar_id: str,
        nombre: str,
        afiliacion: str | None = None,
        coautor_scholar_id: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO coautores (scholar_id, nombre, afiliacion, coautor_scholar_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scholar_id, nombre) DO UPDATE SET
                    afiliacion = excluded.afiliacion,
                    coautor_scholar_id = COALESCE(excluded.coautor_scholar_id, coautores.coautor_scholar_id)
                """,
                (scholar_id, nombre, afiliacion, coautor_scholar_id),
            )

    def upsert_cita(
        self,
        paper_citado_id: int,
        titulo_citante: str,
        autores_citante: str | None = None,
        anio_citante: int | None = None,
        venue_citante: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO citas (paper_citado_id, titulo_citante, autores_citante, anio_citante, venue_citante)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(paper_citado_id, titulo_citante) DO NOTHING
                """,
                (paper_citado_id, titulo_citante, autores_citante, anio_citante, venue_citante),
            )

    def upsert_keyword(self, termino: str, termino_canonico: str | None = None) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO keywords (termino, termino_canonico)
                VALUES (?, ?)
                ON CONFLICT(termino) DO UPDATE SET
                    termino_canonico = COALESCE(excluded.termino_canonico, keywords.termino_canonico)
                """,
                (termino, termino_canonico or termino),
            )
            row = conn.execute("SELECT id FROM keywords WHERE termino = ?", (termino,)).fetchone()
            return row["id"]

    def link_paper_keyword(self, paper_id: int, keyword_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO paper_keywords (paper_id, keyword_id) VALUES (?, ?)",
                (paper_id, keyword_id),
            )

    def update_keyword_canonical(self, keyword_id: int, termino_canonico: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE keywords SET termino_canonico = ? WHERE id = ?",
                (termino_canonico, keyword_id),
            )

    def upsert_tendencia_global(
        self, keyword_id: int, anio: int, conteo: int, fuente: str = "openalex"
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tendencias_globales (keyword_id, anio, conteo_global, fuente, fecha_consulta)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(keyword_id, anio) DO UPDATE SET
                    conteo_global = excluded.conteo_global,
                    fecha_consulta = datetime('now')
                """,
                (keyword_id, anio, conteo, fuente),
            )

    def log_metrica(self, paso: str, duracion_seg: float, detalle: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO pipeline_metricas (paso, duracion_seg, detalle) VALUES (?, ?, ?)",
                (paso, duracion_seg, detalle),
            )

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def get_papers_sin_keywords(self) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT p.* FROM papers p
            LEFT JOIN paper_keywords pk ON p.id = pk.paper_id
            WHERE pk.paper_id IS NULL
            """
        )

    def get_investigadores(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM investigadores ORDER BY nombre")

    def get_top_papers_por_investigador(self, scholar_id: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT p.* FROM papers p
            JOIN autorias a ON p.id = a.paper_id
            WHERE a.scholar_id = ?
            ORDER BY p.citado_por DESC
            LIMIT ?
            """,
            (scholar_id, limit),
        )

    def get_sinergias(self) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT
                COALESCE(k.termino_canonico, k.termino) AS keyword,
                i1.nombre AS inv1,
                i2.nombre AS inv2,
                COUNT(DISTINCT CASE WHEN a1.scholar_id IS NOT NULL THEN p.id END) AS papers_inv1,
                COUNT(DISTINCT CASE WHEN a2.scholar_id IS NOT NULL THEN p.id END) AS papers_inv2
            FROM keywords k
            JOIN paper_keywords pk ON k.id = pk.keyword_id
            JOIN papers p ON pk.paper_id = p.id
            JOIN autorias a1 ON p.id = a1.paper_id
            JOIN autorias a2 ON p.id = a2.paper_id AND a1.scholar_id < a2.scholar_id
            JOIN investigadores i1 ON a1.scholar_id = i1.scholar_id
            JOIN investigadores i2 ON a2.scholar_id = i2.scholar_id
            GROUP BY keyword, i1.nombre, i2.nombre
            HAVING papers_inv1 > 0 AND papers_inv2 > 0
            ORDER BY (papers_inv1 + papers_inv2) DESC
            """
        )

    def get_keywords_internas_por_anio(self) -> list[dict[str, Any]]:
        return self.query(
            """
            SELECT
                COALESCE(k.termino_canonico, k.termino) AS keyword,
                k.id AS keyword_id,
                p.anio,
                COUNT(*) AS conteo
            FROM keywords k
            JOIN paper_keywords pk ON k.id = pk.keyword_id
            JOIN papers p ON pk.paper_id = p.id
            WHERE p.anio IS NOT NULL
            GROUP BY keyword, k.id, p.anio
            ORDER BY keyword, p.anio
            """
        )

    def get_all_keywords(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM keywords ORDER BY termino")

    def get_tendencias_globales(self, keyword_id: int) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM tendencias_globales WHERE keyword_id = ? ORDER BY anio",
            (keyword_id,),
        )

    def get_metricas_totales(self) -> float:
        row = self.query_one("SELECT SUM(duracion_seg) AS total FROM pipeline_metricas")
        return row["total"] or 0.0 if row else 0.0
