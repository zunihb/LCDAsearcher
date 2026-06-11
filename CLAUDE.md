# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

LCDA Searcher is a research-mapping tool for the LCDA group (Universidad de Concepción). It scrapes Google Scholar profiles, enriches papers with abstracts/DOIs via IEEE Xplore (Playwright) or OpenAlex, extracts keywords via LLM, detects research trends, and generates an interactive knowledge graph + reports.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # required for ieee-pw abstract source
cp .env.example .env          # then edit with LLM credentials

# Verify LLM connection
python scripts/test_llm.py
python scripts/test_llm.py --all   # test all OpenCode Go compatible models

# Quick demo (no Scholar scraping needed)
python scripts/seed_demo.py
python main.py --skip-extract --skip-citations --reprocess-keywords

# Full pipeline
python main.py

# Common partial runs
python main.py --skip-extract --skip-citations --reprocess-keywords   # redo keywords only
python main.py --only-abstracts --source openalex                     # abstracts bulk (sin Playwright)
python main.py --only-abstracts --source ieee-pw                      # abstracts via IEEE (lento)
python main.py --phase-a --source openalex                            # abstracts + keywords + aliases

# Mantenimiento de keywords (tras añadir papers o cambiar reglas de normalización)
python scripts/fix_keywords.py --rebuild-norms --merge-pk

# Interactive modes
python main.py --search "consulta"    # single-shot LLM query
python main.py --chat                 # interactive chat (terminal)
python main.py --tui                  # Textual visual interface
python main.py --history              # show saved chat history
```

## Environment variables (`.env`)

| Variable | Purpose |
|---|---|
| `LLM_BASE_URL` | OpenAI-compatible endpoint (e.g. `https://opencode.ai/zen/go/v1`) |
| `LLM_API_KEY` | API key for the above |
| `LLM_MODEL` | Model ID (recommended: `mimo-v2.5-pro`) |
| `LLM_JSON_MODE` | `json_schema` \| `json_object` \| `text` |
| `LLM_BACKEND` | `openai` (default) \| `gemini` |
| `GEMINI_API_KEY` | Only when `LLM_BACKEND=gemini` |
| `GEMINI_MODEL` | Default: `gemini-3.1-flash-lite` |

## Architecture

### Pipeline (7 steps, all orchestrated by `main.py`)

```
Scholar IDs (config.yaml)
  → [1] extract.py       — scholarly scraping → cache JSON + SQLite
  → [2] abstracts*.py    — abstract/DOI/authors enrichment (configurable source)
  → [3] citations.py     — top-5 papers × 50 citing papers (Scholar, rate-limited)
  → [4] keywords.py      — LLM extracts 15 keywords/paper (json_schema)
  → [5] trends.py        — internal frequency + OpenAlex global counts → Plotly HTML
  → [6] graph.py         — NetworkX + Pyvis → output/grafo.html
  → [7] report.py        — sinergias.csv + reporte.md + tendencias.html
```

### Abstract sources (selectable via `--source` or `config.yaml abstracts.source`)

| Source key | Module | Notes |
|---|---|---|
| `ieee-pw` | `abstracts_ieee_pw.py` | **Default; Playwright headless browser** |
| `scholarly-pw` | `abstracts_pw.py` | Playwright + Scholar |
| `multisource` | `abstracts_multi.py` | Tries multiple sources |
| `ieee` | `abstracts_ieee.py` | Direct IEEE (no Playwright) |
| `openalex` | `abstracts.py` | OpenAlex REST API (free, no key) |
| `scholarly` | `abstracts.py` | Scholar fill per paper (slow) |

### LLM layer (`src/llm_backend.py`)

`LLMBackend` unifies OpenAI-compatible and Google GenAI SDKs behind a single `chat_with_tools()` interface. Tool calling is defined in `src/tools.py` (OpenAI JSON Schema format); Gemini calls convert via `_convert_tools_to_gemini()`. The agentic loop runs up to `max_rounds=5` iterations.

### Database (`src/db.py`)

SQLite at `data/lcda.db`. Schema defined as a `SCHEMA` string constant in `db.py`; migrations are applied automatically at startup via `db.init_schema()`. The graph is **not persisted** — it is always rebuilt from SQLite at runtime.

Key tables: `investigadores`, `papers`, `autorias`, `paper_autores`, `keywords`, `paper_keywords`, `coautores`, `citas`, `tendencias_globales`, `pipeline_metricas`, `chat_sesiones`, `chat_mensajes`.

### Chat tools (`src/tools.py`)

All LLM-callable tools are defined as a `TOOLS` list in OpenAI JSON Schema format. `execute_tool(db, name, args)` dispatches calls. Tools include: `list_researchers`, `get_researcher_profile`, `search_papers`, `search_keywords`, `search_topic_hybrid`, `get_topic_evidence`, `compare_researchers`, `get_papers_by_researcher_and_topic`, `get_data_quality_report`, `get_suspicious_records`.

## Key design constraints

- **No official Scholar API** — `scholarly` library scrapes; cached per researcher in `data/raw/<scholar_id>.json`. Use `--skip-extract` to avoid re-scraping.
- **Citation crawling is intentionally capped** — top-5 papers × 50 citing papers only. Full citation graph would require 1300+ requests for Espinoza alone and triggers Scholar blocks. Table `citas` is empty in the current DB.
- **IEEE PDFs require institutional login** — do not commit credentials. See `docs/IEEE_PDF.md`.
- **LLM must support OpenAI-compatible `/v1/chat/completions`** — Anthropic-native models (MiniMax M2.x, Qwen3.x Plus/Max) are not compatible with the current client.
- **Keywords pipeline**: always uses `json_schema` strict mode → `json_object` fallback → local title extraction. Model `mimo-v2.5-pro` + `json_schema` gives best results; avoid `kimi-k2.5` (reasoning tokens cause fallback).
- **Keyword dedup**: all topic queries use `keyword_norm` (normalized form), never `termino_canonico` directly. After adding new papers, re-run `scripts/fix_keywords.py --rebuild-norms --merge-pk` to consolidate variants.
- **Paper dedup**: `upsert_paper` deduplicates by DOI (priority) → `scholar_pub_id` → exact title. DOIs are normalized to lowercase.
- Set `openalex_mailto` in `config.yaml` (trends section) to a real email — required for OpenAlex polite pool priority.
- **DB state (June 2026)**: 16 investigators, ~6.500 papers, 66% abstract coverage, 0 citas.

## Output files

All outputs go to `output/`: `grafo.html` (interactive graph), `sinergias.csv`, `reporte.md`, `tendencias.html`, `tendencias.csv`.
