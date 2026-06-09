"""Construcción del grafo de conocimiento (NetworkX + Pyvis)."""

from __future__ import annotations

import math
from pathlib import Path

import networkx as nx
from pyvis.network import Network

from src.db import Database

COLORS = {
    "investigador": "#c4620a",
    "keyword": "#0077a8",
    "paper": "#6b7f96",
}


def _paper_size(citado_por: int) -> int:
    return max(8, int(8 + math.log10(max(citado_por, 2)) * 6))


def build_graph(db: Database) -> nx.Graph:
    G = nx.Graph()

    for inv in db.get_investigadores():
        nid = f"inv:{inv['scholar_id']}"
        G.add_node(
            nid,
            label=inv["nombre"],
            tipo="investigador",
            title=f"{inv['nombre']}\n{inv.get('afiliacion', '')}\nCitas: {inv.get('citas_total', 0)}",
            size=30,
        )

    papers = db.query("SELECT * FROM papers")
    for p in papers:
        pid = f"paper:{p['id']}"
        label = (p["titulo"][:40] + "…") if len(p["titulo"]) > 40 else p["titulo"]
        G.add_node(
            pid,
            label=label,
            tipo="paper",
            title=f"{p['titulo']}\n{p.get('anio', '')} · {p.get('citado_por', 0)} citas",
            size=_paper_size(p.get("citado_por", 0)),
        )

        autorias = db.query("SELECT scholar_id FROM autorias WHERE paper_id = ?", (p["id"],))
        for a in autorias:
            G.add_edge(f"inv:{a['scholar_id']}", pid, relation="autor_de")

    kws = db.query(
        """
        SELECT k.id, COALESCE(k.termino_canonico, k.termino) AS termino
        FROM keywords k
        """
    )
    for k in kws:
        kid = f"kw:{k['id']}"
        G.add_node(kid, label=k["termino"], tipo="keyword", title=k["termino"], size=14)

    links = db.query("SELECT paper_id, keyword_id FROM paper_keywords")
    for lk in links:
        G.add_edge(f"paper:{lk['paper_id']}", f"kw:{lk['keyword_id']}", relation="trata_sobre")

    return G


def export_graph_html(G: nx.Graph, output_path: Path) -> None:
    net = Network(height="600px", width="100%", bgcolor="#eceae5", font_color="#1a1714")
    net.barnes_hut(gravity=-5000, central_gravity=0.2, spring_length=120)

    for node, data in G.nodes(data=True):
        color = COLORS.get(data.get("tipo", "paper"), "#888")
        net.add_node(
            node,
            label=data.get("label", node),
            title=data.get("title", ""),
            color=color,
            size=data.get("size", 10),
        )

    for u, v, data in G.edges(data=True):
        net.add_edge(u, v, title=data.get("relation", ""))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    net.write_html(str(output_path), local=True)


def run_graph(db: Database, output_dir: Path) -> dict:
    import time

    t0 = time.time()
    G = build_graph(db)
    path = output_dir / "grafo.html"
    export_graph_html(G, path)
    dur = time.time() - t0
    db.log_metrica("graph", dur, f"{G.number_of_nodes()} nodos, {G.number_of_edges()} aristas")
    return {
        "nodos": G.number_of_nodes(),
        "aristas": G.number_of_edges(),
        "output": str(path),
        "duracion_seg": dur,
    }
