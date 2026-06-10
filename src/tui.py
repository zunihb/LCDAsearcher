"""TUI interactivo para LCDA Searcher con Textual."""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.widgets import Static, Input, Markdown, Header
from textual import work

from openai import OpenAI

from src.db import Database
from src.matching import get_investigador_keyword_matrix, get_matches_investigadores
from src.search import build_search_context, ask_llm
from src.cli_output import _strip_inline_thinking


# ─── Widgets ────────────────────────────────────────────────────────────────────

class StatusBar(Static):
    """Barra de estado: fuentes o thinking indicator."""

    def show_sources(self, keywords: list[str], papers: int, matches: int) -> None:
        colors = ["yellow", "cyan", "green", "magenta", "bright_yellow"]
        kw_parts = []
        for i, k in enumerate(keywords[:5]):
            c = colors[i % len(colors)]
            kw_parts.append(f"[{c}]{k}[/{c}]")
        kw_str = ", ".join(kw_parts)
        if len(keywords) > 5:
            kw_str += f" [dim]+{len(keywords) - 5}[/dim]"

        parts = [f"[dim]keywords:[/dim] {kw_str}"]
        if papers > 0:
            parts.append(f"[bold green]{papers}[/bold green] [dim]papers[/dim]")
        if matches > 0:
            parts.append(f"[bold yellow]{matches}[/bold yellow] [dim]matches[/dim]")

        self.update("  " + "  [dim]│[/dim]  ".join(parts))
        self.display = True

    def show_thinking(self) -> None:
        self.update("  [bold dark_orange]⠿ thinking...[/bold dark_orange]")
        self.display = True

    def hide(self) -> None:
        self.update("")
        self.display = False


class ResponseArea(Vertical):
    """Área scrollable para el chat."""
    pass


class ChatInput(Input):
    """Input persistente en la parte inferior."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(placeholder="Preguntá sobre investigadores, papers, keywords...", **kwargs)


# ─── App principal ──────────────────────────────────────────────────────────────

class LCDATui(App):
    """TUI de LCDA Searcher."""

    CSS = """
    Screen {
        background: $surface;
    }

    #header {
        dock: top;
        height: 1;
        background: $primary;
        color: $text;
    }

    #status-bar {
        dock: top;
        height: auto;
        margin: 0 1;
        display: none;
    }

    #response-area {
        height: 1fr;
        margin: 0 1;
        overflow-y: auto;
    }

    #response-area Markdown {
        width: 100%;
    }

    #input-bar {
        dock: bottom;
        height: 3;
        margin: 0 1 1 1;
    }

    #input-bar ChatInput {
        height: 3;
        border: tall $primary;
        background: $surface;
        color: $text;
    }

    #input-bar ChatInput:focus {
        border: tall $accent;
    }

    .user-msg {
        margin: 1 0 0 0;
        padding: 0 1;
        background: $boost;
        border-left: thick $accent;
    }

    .assistant-msg {
        margin: 0 0 1 0;
    }

    .divider {
        height: 1;
        margin: 0 0 0 0;
    }

    .error-block {
        margin: 0 0 1 0;
        border: tall red;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Salir"),
        Binding("ctrl+l", "clear", "Limpiar"),
        Binding("escape", "quit", "Salir"),
    ]

    TITLE = "LCDA Searcher"
    SUB_TITLE = "Chat de Investigación"

    def __init__(self, db: Database, client: OpenAI, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.db = db
        self.client = client
        self.historial: list[dict[str, str]] = []
        self.max_history = 10
        self.sesion_id = db.crear_sesion_chat(modo="tui")

    def compose(self) -> ComposeResult:
        yield Header(id="header")
        yield StatusBar(id="status-bar")
        yield ResponseArea(
            Markdown(self._welcome_md()),
            id="response-area",
        )
        with Horizontal(id="input-bar"):
            yield ChatInput(id="chat-input")

    def _welcome_md(self) -> str:
        return """# LCDA Searcher

**Chat de Investigación — Motor de búsqueda semántica**

**Comandos:**
- `/matches` — ver matches temáticos top
- `/perfil <nombre>` — resumen de un investigador
- `/fuentes` — estadísticas de la base de datos
- `/limpiar` — limpiar historial

**Ejemplos:**
- *"¿quién trabaja en control predictivo?"*
- *"últimos papers de Espinoza"*
- *"compará a los investigadores en electrónica de potencia"*
- *"cuáles son las keywords top 5?"*
"""

    def on_mount(self) -> None:
        self.query_one("#chat-input", ChatInput).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        self.query_one("#chat-input", ChatInput).value = ""
        self._handle_input(text)

    def _handle_input(self, text: str) -> None:
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._do_search(text)

    def _handle_command(self, cmd: str) -> None:
        lower = cmd.lower()

        if lower == "/limpiar":
            self.historial.clear()
            self.query_one("#status-bar", StatusBar).hide()
            resp_area = self.query_one("#response-area", ResponseArea)
            resp_area.remove_children()
            resp_area.mount(Markdown("*Historial limpiado.*"))
            return

        if lower == "/fuentes":
            stats = self.db.query("""
                SELECT
                    (SELECT COUNT(*) FROM investigadores) AS investigadores,
                    (SELECT COUNT(*) FROM papers) AS papers,
                    (SELECT COUNT(*) FROM papers WHERE abstract IS NOT NULL AND trim(abstract) != '') AS con_abstract,
                    (SELECT COUNT(*) FROM keywords) AS keywords,
                    (SELECT COUNT(*) FROM paper_keywords) AS vinculos_kw,
                    (SELECT COUNT(*) FROM autorias) AS autorias,
                    (SELECT MIN(anio) FROM papers WHERE anio IS NOT NULL) AS min_anio,
                    (SELECT MAX(anio) FROM papers) AS max_anio
            """)
            if stats:
                s = stats[0]
                md = f"""## Base de datos

| Metrica | Valor |
|---------|-------|
| Investigadores | {s['investigadores']} |
| Papers | {s['papers']} ({s['con_abstract']} con abstract) |
| Keywords | {s['keywords']} unicas, {s['vinculos_kw']} vinculos |
| Autorias | {s['autorias']} |
| Rango de anios | {s['min_anio']} - {s['max_anio']} |
"""
                self._append_assistant(md)
            return

        if lower.startswith("/perfil"):
            parts = cmd.split(maxsplit=1)
            nombre = parts[1] if len(parts) > 1 else ""
            if not nombre:
                self._append_assistant("*Uso: /perfil <nombre o parte del nombre>*")
                return
            self._show_perfil(nombre)
            return

        if lower == "/matches":
            self._show_matches()
            return

        self._append_assistant(f"*Comando desconocido: {cmd}*")

    def _show_perfil(self, nombre_parcial: str) -> None:
        invs = self.db.get_investigadores()
        target = None
        for inv in invs:
            if nombre_parcial.lower() in inv["nombre"].lower():
                target = inv
                break
        if not target:
            nombres = ", ".join(i["nombre"] for i in invs)
            self._append_assistant(f"*No encontre '{nombre_parcial}'. Investigadores: {nombres}*")
            return

        sid = target["scholar_id"]
        matrix = get_investigador_keyword_matrix(self.db)
        inv_kws = sorted(
            [r for r in matrix if r["scholar_id"] == sid],
            key=lambda r: r["papers"],
            reverse=True,
        )[:12]

        md = f"""## Perfil: {target['nombre']}

| Dato | Valor |
|------|-------|
| Afiliacion | {target.get('afiliacion') or 'N/A'} |
| h-index | {target.get('indice_h', '?')} |
| i10 | {target.get('indice_i10', '?')} |
| Citas | {target.get('citas_total', '?')} |

### Top keywords

| Keyword | Papers | Citas | Rango |
|---------|--------|-------|-------|
"""
        for kw in inv_kws:
            md += f"| {kw['keyword']} | {kw['papers']} | {kw['citas']} | {kw['primer_anio']}-{kw['ultimo_anio']} |\n"

        self._append_assistant(md)

    def _show_matches(self) -> None:
        matches = get_matches_investigadores(self.db, limit=15)
        if not matches:
            self._append_assistant("*No hay matches tematicos disponibles.*")
            return

        md = """## Matches tematicos

| # | Keyword | Inv 1 | Papers | Inv 2 | Papers | Score | Potencial |
|---|---------|-------|--------|-------|--------|-------|-----------|
"""
        for i, m in enumerate(matches, 1):
            md += (
                f"| {i} | {m['keyword']} | {m['investigador_1']} | {m['papers_inv1']} | "
                f"{m['investigador_2']} | {m['papers_inv2']} | {m['score']:.1f} | {m['potencial']} |\n"
            )
        self._append_assistant(md)

    # ─── Búsqueda ───────────────────────────────────────────────────────────

    def _do_search(self, text: str) -> None:
        """Muestra la pregunta del usuario y lanza la búsqueda."""
        # Mostrar la pregunta del usuario
        self._append_user(text)

        # Guardar pregunta en DB
        self.db.guardar_mensaje_chat(self.sesion_id, "user", text)

        # Mostrar thinking indicator
        sb = self.query_one("#status-bar", StatusBar)
        sb.show_thinking()

        # Lanzar búsqueda en thread
        self._run_search(text)

    @work(exclusive=True, thread=True)
    def _run_search(self, text: str) -> None:
        """Ejecuta búsqueda en thread separado."""
        try:
            context = build_search_context(self.db, text)
            trimmed = self.historial[-(self.max_history * 2):] if self.historial else None
            result = ask_llm(self.client, context, text, trimmed)
        except Exception as e:
            self.call_from_thread(self._on_error, str(e))
            return

        content = result["content"]
        reasoning = result.get("reasoning", "")
        kw = context["keywords_encontradas"]
        papers = len(context["papers_representativos"]) + len(context["papers_por_titulo"])
        matches_count = len(context["matches_tematicos"])

        content = _strip_inline_thinking(content)

        self.call_from_thread(self._on_result, content, kw, papers, matches_count)
        self.historial.append({"role": "user", "content": text})
        self.historial.append({"role": "assistant", "content": content})

    def _on_result(self, content: str, kw: list[str], papers: int, matches: int) -> None:
        """Callback en el main thread cuando la búsqueda termina."""
        # Actualizar barra de fuentes
        sb = self.query_one("#status-bar", StatusBar)
        if kw:
            sb.show_sources(kw, papers, matches)
        else:
            sb.hide()

        # Mostrar respuesta
        self._append_assistant(content)

        # Guardar respuesta en DB
        self.db.guardar_mensaje_chat(
            self.sesion_id, "assistant", content,
            keywords_detectadas=kw,
            papers_encontrados=papers,
            matches_relevantes=matches,
        )

    def _on_error(self, msg: str) -> None:
        sb = self.query_one("#status-bar", StatusBar)
        sb.hide()
        resp_area = self.query_one("#response-area", ResponseArea)
        resp_area.mount(Static(
            f"[red]Error: {msg}[/red]",
            classes="error-block",
        ))
        resp_area.scroll_end(animate=False)

    # ─── Append helpers ─────────────────────────────────────────────────────

    def _append_user(self, text: str) -> None:
        resp_area = self.query_one("#response-area", ResponseArea)
        resp_area.mount(Static(
            f"[bold bright_white]> {text}[/bold bright_white]",
            classes="user-msg",
        ))

    def _append_assistant(self, md: str) -> None:
        resp_area = self.query_one("#response-area", ResponseArea)
        resp_area.mount(Markdown(md, classes="assistant-msg"))
        resp_area.scroll_end(animate=False)

    # ─── Actions ────────────────────────────────────────────────────────────

    def action_clear(self) -> None:
        self.historial.clear()
        self.query_one("#status-bar", StatusBar).hide()
        resp_area = self.query_one("#response-area", ResponseArea)
        resp_area.remove_children()
        resp_area.mount(Markdown("*Historial limpiado. Presiona Enter para empezar.*"))
