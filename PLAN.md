# Plan consolidado — LCDA Searcher

Documento de referencia del plan acordado. Ver también [`docs/`](docs/README.md) para detalle técnico y [`docs/DECISIONES_Y_TROUBLESHOOTING.md`](docs/DECISIONES_Y_TROUBLESHOOTING.md) para el historial de problemas resueltos y benchmarks.

## 1. Contexto

Pipeline en Python para mapear sinergias entre investigadores LCDA, piloto 2 perfiles → escalable a 80–90 personas. El **paper** es la entidad central; hacia RAG con PDFs IEEE.

## 2. Decisiones técnicas (vinculantes)

| Área | Decisión | Condición |
|------|----------|-----------|
| Descubrimiento | Google Scholar (`scholarly`) | **Scraping**, no API; caché JSON obligatoria |
| Enriquecimiento | OpenAlex API | Abstracts, DOI, autores, URLs; `mailto` en config |
| PDFs IEEE | Browser + suscripción UdeC | Sin credenciales en repo; ver `docs/IEEE_PDF.md` |
| Persistencia | SQLite (`data/lcda.db`) | Grafo reconstruido, no almacenado |
| Keywords | 15/paper, `json_schema` | LLM OpenCode Go `mimo-v2.5-pro` recomendado |
| Citas | Acotado | Top 5 papers, max 50 citantes |
| Tendencias | OpenAlex | Brecha interna vs global |
| Visualización | Pyvis + Plotly | HTML standalone |

## 3. Pipeline (7 pasos)

1. **extract.py** — Scholar: perfiles, papers, coautores → SQLite + `data/raw/*.json`
2. **abstracts.py** — OpenAlex: abstract completo, DOI, `url_doi`, `url_ieee`, `paper_autores`
3. **citations.py** — Citantes top-5 (reanudable; Scholar suele bloquear)
4. **keywords.py** — 15 keywords/paper, `response_format: json_schema`, guardado 1-a-1
5. **trends.py** — Serie interna + OpenAlex global + cuadrante
6. **graph.py** — Grafo → `output/grafo.html`
7. **report.py** — `sinergias.csv`, `reporte.md`, tendencias

## 4. Fuentes: qué trae cada una

Ver [`docs/FUENTES_DE_DATOS.md`](docs/FUENTES_DE_DATOS.md).

- **Scholar**: títulos, citas, red coautores — sin abstract en modo rápido
- **OpenAlex**: abstract, DOI, autores/afiliaciones — indexa IEEE vía DOI
- **IEEE Xplore**: PDF con login institucional — fase RAG

## 5. LLM y JSON

Ver [`docs/LLM_Y_KEYWORDS.md`](docs/LLM_Y_KEYWORDS.md).

- `json_schema` strict elimina basura tipo "The user wants..."
- Abstract en BD mejora calidad de keywords

## 6. Fase RAG (próxima)

1. Cola PDF desde `papers.doi` / `url_ieee`
2. Descarga con sesión browser IEEE (usuario logueado)
3. `data/pdfs/`, tablas `documentos`, `doc_chunks`, `chunk_embeddings`
4. Embeddings locales (`multilingual-e5` o `bge-m3`) — **no requiere Gemini**

## 7. Fuera de alcance (fase 2)

- Clustering 90 investigadores
- Neo4j
- Extracción masiva citantes
- Interfaz web (Next.js)

## 8. Expansión de red de coautores (2026-06-10)

### Objetivo
Escalar de 2 investigadores piloto a 17, incorporando la red de coautores directos de José R. Espinoza para enriquecer el MVP y demostrar la escalabilidad del pipeline.

### Criterios de selección (15 nuevos investigadores)

**Top 10 por impacto citacional** (papers más citados de Espinoza):

| # | Nombre | Afiliación | Scholar ID |
|---|--------|-----------|------------|
| 1 | Jose Rodriguez | Universidad San Sebastian | KZ5TMuAAAAAJ |
| 2 | Geza Joos | McGill University | wdTi3S0AAAAJ |
| 3 | Pat Wheeler | University of Nottingham | 8z38R2MAAAAJ |
| 4 | Marian P. Kazmierkowski | Warsaw University of Technology | c1VhcpIAAAAJ |
| 5 | Haitham Abu-Rub | Hamad Bin Khalifa University | gggnQKsAAAAJ |
| 6 | Marcelo A. Perez | UTFSM | c9ql-BAAAAAJ |
| 7 | Christian A. Rojas | UTFSM | U1_r_C4AAAAJ |
| 8 | Marco Rivera | University of Nottingham UK | FOKLmaQAAAAJ |
| 9 | Javier Muñoz Vidal | Universidad de Talca | YwQuZr0AAAAJ |
| 10 | Mariusz Malinowski | Warsaw University of Technology | nv4fvLcAAAAJ |

**5 de UdeC/colaboración cercana**:

| # | Nombre | Afiliación | Scholar ID |
|---|--------|-----------|------------|
| 11 | Luis Morán | UdeC | EwIjpU4AAAAJ |
| 12 | Daniel G. Sbarbaro-Hofer | UdeC | b1UsMiEAAAAJ |
| 13 | Miguel Figueroa | UdeC | tRxVpaUAAAAJ |
| 14 | Claudio Molina Muñoz | UdeC (egresado) | _d9POm4AAAAJ |
| 15 | Jaime Addin Rohten | UBB (colaborador cercano UdeC) | aa-NCokAAAAJ |

### Impacto esperado

| Métrica | Antes | Después (estimado) |
|---------|-------|-------------------|
| Investigadores | 2 | 17 |
| Papers únicos | 353 | ~2,000-3,000 |
| Autorias | 360 | ~3,000-5,000 |
| Red de coautores | 55 | ~200+ |

### Riesgos
- Rate limiting Scholar: ~1-2h en extract.py
- Posibles bloqueos tras ~100-200 requests

## 9. Métrica de venta

> "Hacer esto a mano tomaría ~4 h por investigador. Con el pipeline, toma minutos."

Reporte incluye comparación en `output/reporte.md`.
