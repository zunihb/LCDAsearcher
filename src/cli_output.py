"""Renderizado de CLI con Rich: markdown, paneles, spinners, colores."""

from __future__ import annotations

import json
import re
import sys

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.spinner import Spinner

console = Console()

# Paleta de colores para keywords (rota entre estos)
KW_COLORS = ["yellow", "cyan", "green", "magenta", "bright_yellow", "bright_cyan", "bright_green"]


def _colorize_keywords(text: str) -> str:
    """Responde con keywords en colores alternados dentro del markdown."""
    # Rich Markdown ya渲染iza bien, pero podemos pre-procesar
    # para darle color a las keywords entre comillas o backticks
    return text


def print_welcome(title: str, commands: list[tuple[str, str]], examples: list[str]) -> None:
    """Muestra banner de bienvenida con comandos y ejemplos."""
    lines = Text()
    lines.append("Preguntá sobre investigadores, papers, keywords o matches\n\n", style="dim")

    lines.append("Comandos:\n", style="bold white")
    for cmd, desc in commands:
        lines.append(f"  {cmd:<22}", style="bold cyan")
        lines.append(f"{desc}\n", style="dim")

    lines.append("\nEjemplos:\n", style="bold white")
    for ex in examples:
        lines.append(f"  {ex}\n", style="dim italic")

    console.print(Panel(
        lines,
        title=f"[bold bright_white]{title}[/bold bright_white]",
        border_style="bright_blue",
        padding=(1, 2),
    ))


def print_sources(keywords: list[str], papers: int, matches: int) -> None:
    """Muestra las fuentes detectadas en la búsqueda."""
    kw_parts = []
    for i, k in enumerate(keywords[:5]):
        color = KW_COLORS[i % len(KW_COLORS)]
        kw_parts.append(f"[{color}]{k}[/{color}]")
    kw_str = ", ".join(kw_parts)
    if len(keywords) > 5:
        kw_str += f" [dim]+{len(keywords) - 5}[/dim]"

    parts = [f"  [dim]keywords:[/dim] {kw_str}"]
    if papers > 0:
        parts.append(f"[bold green]{papers}[/bold green] [dim]papers[/dim]")
    if matches > 0:
        parts.append(f"[bold yellow]{matches}[/bold yellow] [dim]matches[/dim]")

    console.print(f"  {parts[0]}  [dim]│[/dim]  {'  [dim]│[/dim]  '.join(parts[1:])}", highlight=False)


def _strip_inline_thinking(text: str) -> str:
    """Fallback conservador para respuestas legacy con reasoning dentro de content."""
    if not text:
        return text

    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    clean = re.sub(r"<thinking>.*?</thinking>", "", clean, flags=re.DOTALL | re.IGNORECASE).strip()

    if clean.startswith("```"):
        json_candidate = clean.removeprefix("```json").removeprefix("```").strip()
        json_candidate = json_candidate.removesuffix("```").strip()
    else:
        json_candidate = clean

    try:
        data = json.loads(json_candidate, strict=False)
    except json.JSONDecodeError:
        match = re.search(r'^\s*\{\s*"answer"\s*:\s*"(.*)"\s*\}\s*$', json_candidate, re.DOTALL)
        if match:
            return match.group(1).replace(r"\n", "\n").replace(r"\"", '"').strip()
        return clean

    if isinstance(data, dict) and isinstance(data.get("answer"), str):
        answer = data["answer"].strip()
        return answer or clean
    return clean


def print_response(text: str, reasoning: str | None = None) -> None:
    """Renderiza la respuesta del LLM con markdown coloreado."""
    clean = _strip_inline_thinking(text) if text else text

    if clean:
        console.print()
        console.print(Markdown(clean))
        console.print()


class StreamRenderer:
    """Renderiza la respuesta del LLM en streaming.

    Acumula chunks y muestra un spinner durante la generación.
    Al finalizar, renderiza todo con markdown formateado.
    """

    def __init__(self) -> None:
        self.buffer = ""

    def start(self) -> None:
        pass

    def add(self, chunk: str) -> None:
        self.buffer += chunk

    def stop(self) -> str:
        if self.buffer:
            clean = _strip_inline_thinking(self.buffer)
            if clean:
                console.print()
                console.print(Markdown(clean))
                console.print()
        return self.buffer


def print_error(msg: str) -> None:
    console.print(Panel(
        f"[red]{msg}[/red]",
        title="[bold red]Error[/bold red]",
        border_style="red",
        padding=(0, 2),
    ))


def print_info(msg: str) -> None:
    console.print(f"  [dim]{msg}[/dim]")


def print_match_table(matches: list[dict], limit: int = 15) -> None:
    """Muestra matches temáticos en tabla formateada."""
    from rich.table import Table

    table = Table(
        title="[bold bright_white]Matches temáticos[/bold bright_white]",
        show_lines=False,
        header_style="bold bright_cyan",
        border_style="bright_blue",
        title_style="bold",
    )
    table.add_column("#", style="dim", width=3)
    table.add_column("Keyword", style="bold bright_yellow")
    table.add_column("Investigador 1", style="bright_green")
    table.add_column("Papers", justify="right", style="cyan")
    table.add_column("Investigador 2", style="bright_green")
    table.add_column("Papers", justify="right", style="cyan")
    table.add_column("Score", justify="right", style="bold yellow")
    table.add_column("Potencial", style="bold")

    pot_style = {
        "ALTA": "bold bright_green on dark_green",
        "MEDIA": "bold bright_yellow",
        "EXPLORATORIA": "dim italic",
    }
    for i, m in enumerate(matches[:limit], 1):
        table.add_row(
            str(i),
            m["keyword"],
            m["investigador_1"],
            str(m["papers_inv1"]),
            m["investigador_2"],
            str(m["papers_inv2"]),
            f"{m['score']:.1f}",
            f"[{pot_style.get(m['potencial'], 'dim')}]{m['potencial']}",
        )
    console.print(table)
    console.print()


def print_researcher_profile(profile: dict, keywords: list[dict]) -> None:
    """Muestra perfil de investigador formateado."""
    from rich.table import Table

    nombre = profile['nombre']
    afil = profile.get('afiliacion') or 'N/A'
    h = profile.get('indice_h', '?')
    i10 = profile.get('indice_i10', '?')
    citas = profile.get('citas_total', '?')

    console.print(Panel(
        f"[bold bright_white]{nombre}[/bold bright_white]\n"
        f"[dim]{afil}[/dim]\n"
        f"[bright_yellow]h-index[/bright_yellow]: [bold cyan]{h}[/bold cyan]  [dim]│[/dim]  "
        f"[bright_yellow]i10[/bright_yellow]: [bold cyan]{i10}[/bold cyan]  [dim]│[/dim]  "
        f"[bright_yellow]Citas[/bright_yellow]: [bold green]{citas}[/bold green]",
        title="[bold bright_cyan]Perfil[/bold bright_cyan]",
        border_style="bright_cyan",
        padding=(0, 2),
    ))

    if keywords:
        table = Table(show_lines=False, header_style="bold bright_white", border_style="dim")
        table.add_column("Keyword", style="bright_cyan")
        table.add_column("Papers", justify="right", style="bold yellow")
        table.add_column("Citas", justify="right", style="bold green")
        table.add_column("Rango", style="dim")
        for kw in keywords:
            table.add_row(kw["keyword"], str(kw["papers"]), str(kw["citas"]), kw["rango"])
        console.print(table)
    console.print()


def print_db_stats(stats: dict) -> None:
    """Muestra estadísticas de la DB en panel."""
    lines = (
        f"[bright_yellow]Investigadores[/bright_yellow]:  [bold cyan]{stats['investigadores']}[/bold cyan]\n"
        f"[bright_yellow]Papers[/bright_yellow]:          [bold cyan]{stats['papers']}[/bold cyan] [dim]({stats['con_abstract']} con abstract)[/dim]\n"
        f"[bright_yellow]Keywords[/bright_yellow]:        [bold cyan]{stats['keywords']}[/bold cyan] [dim]únicas, {stats['vinculos_kw']} vínculos[/dim]\n"
        f"[bright_yellow]Autorías[/bright_yellow]:        [bold cyan]{stats['autorias']}[/bold cyan]\n"
        f"[bright_yellow]Rango de años[/bright_yellow]:   [bold green]{stats['min_anio']}[/bold green] [dim]–[/dim] [bold green]{stats['max_anio']}[/bold green]"
    )
    console.print(Panel(
        lines,
        title="[bold bright_white]Base de datos[/bold bright_white]",
        border_style="bright_green",
        padding=(0, 2),
    ))


def make_spinner(text: str = "Pensando...") -> Spinner:
    return Spinner("dots", text=f" [dim]{text}[/dim]")
