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

### Pipeline (6 pasos)

```
Scholar IDs → extract.py (caché JSON) → SQLite
                ↓
         citations.py (top-5 papers, ~50 citantes c/u)
                ↓
         keywords.py (LLM OpenAI-compatible)
                ↓
         trends.py (serie interna + OpenAlex global + brecha)
                ↓
         graph.py (NetworkX + Pyvis) → grafo.html
         report.py → sinergias.csv, reporte.md, tendencias.html/csv
```

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

### Fuente de datos

- **Google Scholar** vía `scholarly` para perfiles y publicaciones.
- **Riesgo**: bloqueos por rate-limiting de Google.
- **Mitigación**: caché JSON en `data/raw/<scholar_id>.json` — si el archivo existe, no se re-scrapea.
- Opción `scholarly.use_proxies: true` en `config.yaml` para `FreeProxies`.

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
- Sin API key configurada: fallback local de keywords (tokens frecuentes del título/abstract).
- AI SDK de Vercel **descartado** para el piloto (TypeScript); reservado para interfaz web de fase 2.

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
# Editar .env con tu LLM_API_KEY (opcional pero recomendado)
python main.py
```

### Opciones CLI

```bash
python main.py --skip-extract      # Usar solo datos ya en caché/BD
python main.py --skip-citations    # Omitir scraping de citantes
python main.py --skip-trends       # Omitir consultas OpenAlex
python main.py --skip-keywords     # Omitir extracción de keywords
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
├── config.yaml               # IDs Scholar, parámetros del pipeline
├── .env.example              # Plantilla LLM
├── main.py                   # Orquestador CLI
├── presentacion_piloto.html  # Presentación para el profesor
├── src/
│   ├── db.py                 # Esquema SQLite + helpers
│   ├── extract.py            # Google Scholar → caché → SQLite
│   ├── citations.py          # Citantes acotados
│   ├── keywords.py           # LLM keywords + normalización
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
| `papers` | Publicaciones (título, abstract, año, citado_por) |
| `autorias` | Relación investigador ↔ paper |
| `keywords` | Términos + término canónico (normalizado por LLM) |
| `paper_keywords` | Relación paper ↔ keyword |
| `coautores` | Red cercana del perfil Scholar |
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
