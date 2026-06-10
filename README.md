# LCDA Searcher

**Mapeo de investigadores con grafos de conocimiento e inteligencia artificial.**

Prueba de concepto (PoC) que extrae publicaciones desde Google Scholar, las persiste en SQLite, estandariza palabras clave con un LLM, detecta tendencias internas vs mundiales (OpenAlex) y genera un grafo interactivo, tabla de sinergias y reporte automático.

---

## Plan del piloto

### Objetivo

No es solo un buscador: es un **grafo de redes semánticas** que visualiza cómo se conectan investigadores, papers y temas, y detecta **sinergias de colaboración** y **tendencias de investigación**.

### Investigadores del piloto

| Scholar ID | Nombre | Afiliación |
|------------|--------|------------|
| `Wk2naEgAAAAJ` | José R. Espinoza | Universidad de Concepción |
| `6O2aO7IAAAAJ` | Miguel Torres | Universidad de los Andes |

### Pipeline (7 pasos)

```
Scholar IDs → extract.py (scraping + caché JSON) → SQLite
                ↓
         abstracts.py (OpenAlex API: abstract, DOI, autores, URLs)
                ↓
         citations.py (top-5 papers, ~50 citantes c/u)
                ↓
         keywords.py (LLM + json_schema, 15 keywords/paper)
                ↓
         trends.py (serie interna + OpenAlex global + brecha)
                ↓
         graph.py (NetworkX + Pyvis) → grafo.html
         report.py → sinergias.csv, reporte.md, tendencias.html/csv
```

**Documentación detallada:** carpeta [`docs/`](docs/README.md) (fuentes de datos, LLM, IEEE PDF, esquema BD).

### Entregables

| Archivo | Descripción |
|---------|-------------|
| `output/grafo.html` | Grafo interactivo (investigadores, papers, keywords) |
| `output/sinergias.csv` | Keywords compartidas entre investigadores |
| `output/tendencias.html` | Gráficos de tendencias + cuadrante de oportunidades |
| `output/tendencias.csv` | Datos tabulares de tendencias |
| `output/reporte.md` | Resumen IA + métrica de tiempo + sinergias |
| `presentacion_piloto.html` | Presentación para el profesor (modo claro) |

---

## Condiciones y decisiones técnicas

Estas condiciones fueron acordadas en el diseño del piloto y **deben respetarse** al extender el sistema.

### Fuentes de datos (3 capas)

| Capa | Tecnología | Rol |
|------|------------|-----|
| 1 | **Google Scholar** (`scholarly`) | Scraping — lista papers, citas, coautores perfil |
| 2 | **OpenAlex** (API) | Abstract, DOI, autores/afiliaciones, URLs |
| 3 | **IEEE Xplore** (browser + suscripción) | PDF para RAG — ver `docs/IEEE_PDF.md` |

- Scholar: **no hay API oficial**; caché JSON en `data/raw/<scholar_id>.json`.
- OpenAlex: API gratuita; `openalex_mailto` en `config.yaml`.
- IEEE: **no pegar credenciales**; login institucional en navegador.

### Persistencia

- **SQLite** (`data/lcda.db`) como única fuente de verdad.
- El grafo **no se almacena**: se reconstruye al vuelo con NetworkX.
- Migración a Neo4j queda como opción de fase 2.

### Alcance de citas

- Se guardan **conteos de citas por paper** (vienen gratis con cada publicación).
- Se guarda la **lista de coautores del perfil** (con afiliación y `scholar_id` cuando existe).
- **Extracción acotada de citantes**: solo los **top 5 papers** más citados por investigador, máximo **~50 citantes por paper**, con pausas anti-bloqueo.
- Extracción masiva de citantes queda para **fase 2** (>1.300 requests solo para Espinoza → bloqueo seguro).

### Inteligencia artificial

- Cliente **protocolo OpenAI-compatible** (`openai` con `base_url` configurable).
- Compatible con: OpenAI, Gemini (endpoint compatible), OpenRouter, Ollama local, etc.
- Variables en `.env`: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.
- Recomendado: `mimo-v2.5-pro` + `LLM_JSON_MODE=json_schema` (ver `docs/LLM_Y_KEYWORDS.md`).
- **15 keywords** por paper con salida JSON estructurada obligatoria.
- Sin API key: fallback local desde título/abstract.
- AI SDK de Vercel **descartado** en piloto; reservado para web fase 2.

### Tendencias

- **Internas**: frecuencia de keywords por año desde los papers del grupo.
- **Globales**: conteos mundiales vía **OpenAlex** (API abierta, gratis, sin API key).
- **Brecha**: cuadrante cobertura del grupo vs crecimiento global → categorías: Oportunidad, Fortaleza al alza, Madura, Nicho.
- Configurar `openalex_mailto` en `config.yaml` (requerido por OpenAlex polite pool).

### Visualización

- Grafo: **NetworkX + Pyvis** → HTML standalone.
- Tendencias: **Plotly** → HTML standalone.

---

## Instalación y ejecución

```bash
cd LCDAsearcher
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Editar .env con tu API key (ver sección OpenCode Go abajo)
python scripts/test_llm.py          # 1) probar que el LLM responde
python main.py                      # 2) pipeline completo
```

### Probar con OpenCode Go

1. Copia tu API key de [opencode.ai](https://opencode.ai) en `.env`:

```env
LLM_BASE_URL=https://opencode.ai/zen/go/v1
LLM_API_KEY=sk-tu-clave
LLM_MODEL=mimo-v2.5-pro
LLM_JSON_MODE=json_schema
```

2. **Modelos recomendados** (OpenCode Go, `/v1/chat/completions`):

| Model ID | Uso |
|----------|-----|
| `mimo-v2.5-pro` | **Keywords** — mejor calidad + json_schema |
| `mimo-v2.5` | Más barato, alto volumen |
| `kimi-k2.6` | Alternativa |
| `deepseek-v4-flash` | Barato; JSON mode inestable vía proxy |

3. **No compatibles** con el cliente actual (usan API Anthropic `/v1/messages`): MiniMax M2.5/M2.7/M3, Qwen3.6/3.7 Plus/Max.

```bash
# Probar conexión
python scripts/test_llm.py

# Probar todos los modelos chat/completions
python scripts/test_llm.py --all

# Demo rápido sin Google Scholar
python scripts/seed_demo.py
python main.py --skip-extract --skip-citations --reprocess-keywords

# Pipeline real (requiere Scholar + puede tardar)
python main.py
```

### Opciones CLI

```bash
python main.py --only-abstracts    # Solo OpenAlex: abstract, DOI, autores (~2 min)
python main.py --skip-extract      # Usar caché/BD existente
python main.py --skip-abstracts    # Omitir paso OpenAlex
python main.py --skip-citations    # Omitir citantes Scholar
python main.py --skip-trends       # Omitir tendencias
python main.py --skip-keywords      # Omitir keywords
python main.py --reprocess-keywords  # Borrar y regenerar keywords (15 términos)
```

### Requisitos

- Python 3.10+
- Conexión a internet (Scholar, OpenAlex; LLM si se configura API key)

---

## Estructura del proyecto

```
LCDAsearcher/
├── README.md                 # Este archivo (plan + condiciones)
├── PLAN.md                   # Plan consolidado detallado
├── docs/                     # Documentación técnica ampliada
│   ├── FUENTES_DE_DATOS.md   # Scholar / OpenAlex / IEEE
│   ├── ESQUEMA_BD.md
│   ├── LLM_Y_KEYWORDS.md
│   ├── IEEE_PDF.md
│   └── DECISIONES_Y_TROUBLESHOOTING.md  # Historial de decisiones del piloto
├── config.yaml               # IDs Scholar, parámetros del pipeline
├── .env.example              # Plantilla LLM
├── main.py                   # Orquestador CLI
├── presentacion_piloto.html  # Presentación para el profesor
├── src/
│   ├── db.py                 # Esquema SQLite + helpers
│   ├── extract.py            # Google Scholar (scraping) → caché → SQLite
│   ├── abstracts.py          # OpenAlex: abstract, DOI, autores, URLs
│   ├── citations.py          # Citantes acotados
│   ├── keywords.py           # LLM keywords (json_schema) + normalización
│   ├── trends.py             # Tendencias internas/global + Plotly
│   ├── graph.py              # NetworkX + Pyvis
│   └── report.py             # Sinergias + reporte + métricas
├── data/
│   ├── lcda.db               # Base de datos (generada)
│   └── raw/                  # Caché JSON por investigador
└── output/                   # Entregables HTML/CSV/MD
```

---

## Esquema de base de datos

| Tabla | Propósito |
|-------|-----------|
| `investigadores` | Perfil + métricas (citas, índice h, i10) |
| `papers` | Título, **abstract**, DOI, `url_doi`, `url_ieee`, `url_pdf`, año, citas |
| `paper_autores` | Autores por paper + afiliación (OpenAlex) |
| `autorias` | Relación investigador ↔ paper |
| `keywords` | Términos + término canónico (normalizado por LLM) |
| `paper_keywords` | Relación paper ↔ keyword (15/paper) |
| `coautores` | Red del perfil Scholar (con afiliación) |
| `citas` | Papers citantes (top-5, acotado) |
| `tendencias_globales` | Cache OpenAlex por keyword/año |
| `pipeline_metricas` | Tiempos de ejecución por paso |

---

## Fase futura (no implementada)

Documentada en `PLAN.md` y en `presentacion_piloto.html`:

1. **Ingesta de PDFs + RAG**: subida de papers, extracción con IA (LaTeX, tablas), indexación vectorial con sqlite-vec.
2. **Escalado a 90 investigadores**: clustering, buscador de expertos, mapa de calor 90×90.
3. **Extracción masiva de citantes**: incremental, priorizando papers más citados.
4. **Migración a Neo4j**: si las consultas de red profunda lo justifican.
5. **Interfaz web** (Next.js + AI SDK de Vercel): subida de PDFs, chat RAG, buscador.

---

## Licencia

Proyecto académico — LCDA / Universidad de Concepción.
