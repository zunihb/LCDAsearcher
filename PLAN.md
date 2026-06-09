# Plan consolidado — LCDA Searcher

Documento de referencia del plan acordado. **No modificar sin consenso del equipo.**

## 1. Contexto

Pipeline en Python para mapear sinergias entre investigadores del proyecto LCDA, comenzando con un piloto de 2 perfiles y escalable a 80–90 personas.

## 2. Decisiones técnicas (vinculantes)

| Área | Decisión | Condición |
|------|----------|-----------|
| Fuente principal | Google Scholar (`scholarly`) | Caché JSON obligatoria; no re-scrapear si existe |
| Persistencia | SQLite (`data/lcda.db`) | Un archivo; grafo reconstruido, no almacenado |
| Citas | Conteos + coautores + citantes acotados | Top 5 papers, max 50 citantes, pausas anti-bloqueo |
| IA keywords | OpenAI-compatible | Agnóstico al proveedor; fallback local sin API key |
| Tendencias global | OpenAlex | API abierta; `mailto` en config |
| Visualización | Pyvis (grafo) + Plotly (tendencias) | HTML standalone |
| AI SDK Vercel | Descartado en piloto | Reservado para web fase 2 |

## 3. Pipeline

1. **extract.py** — Perfil + publicaciones → SQLite (dedup por título/ID)
2. **citations.py** — Citantes de top-5 papers (reanudable)
3. **keywords.py** — 5 keywords/paper + normalización global (2 pasadas LLM)
4. **trends.py** — Serie interna por año + OpenAlex + cuadrante brecha
5. **graph.py** — Grafo NetworkX → Pyvis HTML
6. **report.py** — sinergias.csv, reporte.md, métrica tiempo

## 4. Análisis de tendencias

### Interno
Frecuencia de keyword canónica por año (`papers.anio` + `paper_keywords`).

### Global
OpenAlex: `works?search=<kw>&group_by=publication_year` → cache `tendencias_globales`.

### Categorías de brecha
- **Oportunidad**: global sube, grupo cubre poco
- **Fortaleza al alza**: grupo fuerte y global sube
- **Madura**: grupo fuerte, global plano/baja
- **Nicho**: ambos bajos

## 5. Fase futura: PDFs + RAG

- Motor híbrido: local (Docling/Nougat/Marker) o API (Mistral OCR/LlamaParse)
- Tablas: `documentos`, `doc_chunks`, `chunk_embeddings` (sqlite-vec)
- Módulos: `ingest.py`, `rag.py`
- Ya documentado en presentación (sección 07)

## 6. Fuera de alcance (fase 2)

- Clustering 90 investigadores
- Buscador web de expertos
- Despliegue en nube
- Extracción masiva citantes
- Neo4j

## 7. Métrica de venta

> "Hacer esto a mano tomaría ~4 h por investigador. Con el pipeline, toma minutos."

Reporte incluye comparación explícita en `reporte.md`.
