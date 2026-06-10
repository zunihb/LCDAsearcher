# Fuentes de datos — Scholar, OpenAlex e IEEE

Este documento explica **qué obtiene cada fuente**, cómo se accede (API vs scraping) y cómo encajan en el pipeline LCDA Searcher.

## Resumen en una tabla

| Fuente | Tipo de acceso | Qué aporta al piloto | Qué NO aporta |
|--------|----------------|----------------------|---------------|
| **Google Scholar** | Scraping (`scholarly`) | Lista de papers, citas, coautores del perfil, h-index | DOI fiable, abstract (sin fill), PDF IEEE |
| **OpenAlex** | API REST gratuita | Abstract, DOI, autores/afiliaciones, URL DOI, metadata IEEE | PDF con suscripción institucional |
| **IEEE Xplore** | Web + suscripción UdeC | PDF completo, abstract oficial | API pública gratuita en bulk |

---

## 1. Google Scholar (`scholarly`)

### ¿Es API o scraping?

**No existe API oficial de Google Scholar.** El proyecto usa la librería Python [`scholarly`](https://github.com/scholarly-python-stack/scholarly), que:

1. Hace peticiones HTTP a `scholar.google.com`
2. Parsea el HTML de las páginas de perfil y publicaciones
3. Requiere **pausas** entre requests (`pause_min_sec` / `pause_max_sec` en `config.yaml`)

```
LCDAsearcher → scholarly → HTTP + parseo HTML → caché JSON → SQLite
```

### Qué guardamos de Scholar

| Dato | Tabla/campo | Notas |
|------|-------------|-------|
| Perfil investigador | `investigadores` | nombre, afiliación, h-index, citas |
| Lista de papers | `papers` | título, año, `citado_por` |
| Coautores frecuentes del perfil | `coautores` | nombre, afiliación, `coautor_scholar_id` |
| Caché anti-bloqueo | `data/raw/<scholar_id>.json` | No re-scrapear si existe |

### Modo `fill_each_paper`

Si `scholarly.fill_each_paper: true`, Scholar hace **una request extra por paper** (~353 papers ≈ 20–40 min) y puede traer abstract y autores. **Riesgo alto de bloqueo.**

**Recomendación del piloto:** mantener `fill_each_paper: false` y usar **OpenAlex** para abstracts (paso 2 del pipeline).

### Citantes (`citations.py`)

Scraping adicional de papers que citan los top-5 más citados (acotado). Scholar suele bloquear `citedby` → muchas corridas devuelven 0 citantes.

---

## 2. OpenAlex (`abstracts.py`)

### API oficial y gratuita

- Base: `https://api.openalex.org`
- Requiere `mailto` en queries (polite pool) → `openalex_mailto` en `config.yaml`
- Sin API key

### Qué guardamos de OpenAlex

| Dato | Tabla/campo |
|------|-------------|
| Abstract completo | `papers.abstract` |
| DOI | `papers.doi` |
| URL DOI | `papers.url_doi` → `https://doi.org/...` |
| URL IEEE (si aplica) | `papers.url_ieee` |
| PDF open access (si existe) | `papers.url_pdf` |
| ID OpenAlex | `papers.openalex_id` |
| Revista / venue | `papers.venue` |
| Autores por paper | `paper_autores` (nombre, afiliación, orden) |

### Cobertura IEEE

OpenAlex indexa publicaciones IEEE vía Crossref/DOI. Papers con DOI `10.1109/...` suelen tener:

- Abstract reconstruido desde `abstract_inverted_index`
- Revista (ej. IEEE Transactions on Industrial Electronics)
- `primary_location.landing_page_url` → `https://doi.org/10.1109/...` (redirige a IEEE Xplore)

**No incluye** el PDF de IEEE Xplore cuando el artículo es de pago y solo accesible con suscripción institucional.

### Comando solo abstracts

```bash
python main.py --only-abstracts
```

---

## 3. IEEE Xplore (fase PDF / RAG)

### Sin API pública gratuita para bulk

IEEE ofrece APIs de metadata enterprise de pago. Para el piloto académico con suscripción UdeC, el flujo correcto es:

1. Resolver DOI → página del documento en Xplore
2. **Iniciar sesión en el navegador** con cuenta institucional (Shibboleth / UdeC)
3. Descargar PDF desde la interfaz web
4. Guardar en `data/pdfs/` y enlazar en tabla `documentos` (fase RAG)

### ⚠️ Seguridad: no compartir credenciales

**No se deben pegar usuario/contraseña ni API keys de IEEE en `.env` ni en el repositorio.**

El acceso institucional funciona mejor si **tú inicias sesión en Chrome** y el agente usa esa sesión autenticada (ver `docs/IEEE_PDF.md`).

### URLs útiles

| Tipo | Formato |
|------|---------|
| DOI | `https://doi.org/10.1109/...` |
| Documento Xplore | `https://ieeexplore.ieee.org/document/<arnumber>/` |
| PDF (con sesión) | `https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=<id>` |

---

## 4. Arquitectura de capas (visión RAG)

```
CAPA 1 — Scholar (scraping)
  Descubrimiento: ¿qué publica cada investigador? + métricas de citación

CAPA 2 — OpenAlex (API)
  Enriquecimiento: abstract, DOI, autores, URLs, metadata IEEE

CAPA 3 — Keywords LLM (json_schema)
  15 términos técnicos por paper (español), usando título + abstract

CAPA 4 — IEEE Xplore (browser + suscripción)  [próximo]
  PDF → data/pdfs/ → chunks → embeddings → RAG
```

El **paper es la entidad central** en SQLite. Todo orbita alrededor: investigador, autores, keywords, links, y en el futuro PDF + vectores.

---

## 5. Embeddings y RAG (fase futura)

### ¿Hay que usar Gemini para embeddings?

**No.** Embeddings y LLM de chat son servicios **independientes**:

| Componente | Opciones recomendadas |
|------------|----------------------|
| Chat / keywords | OpenCode Go: `mimo-v2.5-pro` + `json_schema` |
| Embeddings | Local: `multilingual-e5-base` o `bge-m3` (gratis) |
| Vector store | `sqlite-vec` en la misma BD |

Gemini `text-embedding-004` es excelente pero **opcional**; no obliga a usar Gemini para el resto del pipeline.

### Flujo RAG planificado

1. PDF en `data/pdfs/{paper_id}.pdf`
2. Extracción texto (Docling / Marker)
3. Chunks ~500 tokens
4. Embedding por chunk
5. Consulta → similitud vectorial → contexto al LLM

Tablas futuras: `documentos`, `doc_chunks`, `chunk_embeddings`.
