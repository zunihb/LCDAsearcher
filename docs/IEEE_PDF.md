# Descarga de PDFs IEEE (suscripción institucional)

## Principio

Los PDFs de IEEE Xplore con suscripción **no** se descargan con API key en `.env`. El acceso es vía **sesión web autenticada** (cuenta UdeC / Shibboleth).

**No compartas contraseñas ni cookies en el chat ni en el repositorio.**

## Flujo recomendado (manual asistido)

### Paso 1 — Tú inicias sesión

1. Abre Chrome con tu sesión habitual
2. Ve a [IEEE Xplore](https://ieeexplore.ieee.org)
3. Inicia sesión con **Institutional Sign-In** → Universidad de Concepción (o tu método habitual)
4. Verifica que puedes abrir un paper de prueba y ver "PDF" o "Download PDF"

### Paso 2 — El agente usa tu sesión de navegador

Con la integración de browser en Cursor, el agente puede:

1. Leer `papers.doi` / `papers.url_ieee` desde SQLite
2. Abrir `https://doi.org/<doi>` o el link Xplore
3. Descargar PDF si la sesión ya está autenticada
4. Guardar en `data/pdfs/<paper_id>.pdf`

### Paso 3 — Registro en BD (fase RAG)

Tabla futura `documentos`:

| Campo | Descripción |
|-------|-------------|
| `paper_id` | FK a `papers` |
| `ruta_pdf` | `data/pdfs/42.pdf` |
| `fuente` | `ieee_xplore` |
| `estado` | `descargado` / `pendiente` / `sin_acceso` |

## Resolver DOI → documento IEEE

Muchos papers del piloto tienen DOI `10.1109/...`. OpenAlex ya guarda:

- `url_doi` → `https://doi.org/10.1109/...`
- `url_ieee` → landing page cuando la fuente es IEEE

El navegador redirige a `ieeexplore.ieee.org/document/<arnumber>/`.

## Cuando no hay acceso

Marcar en manifest:

- `no_access` — requiere compra o login
- `opened_pdf_viewer` — visible en browser, guardar manualmente
- `oa_repository` — usar `url_pdf` de OpenAlex (copia en repositorio)

## Script de prueba (`scripts/download_ieee_pdf.py`)

Usa **Chrome del sistema** con perfil persistente en `data/ieee_browser_profile/` (sesión separada del Chrome habitual).

```bash
# 0) Abrir Chrome IEEE (persiste aunque cierres Cursor)
./scripts/open_ieee_chrome.sh

# 1) Una vez: guardar login institucional UdeC (ventana Chrome ~90 s)
python scripts/download_ieee_pdf.py --login

# 2) Descargar un paper por id de la BD
python scripts/download_ieee_pdf.py 4

# PDF → data/pdfs/<paper_id>.pdf
# Manifest → data/ieee_manifest.csv
```

Requisito: `pip install playwright && playwright install chrome`

## Próximo módulo (`src/ieee.py`)

Planeado:

- Cola batch para papers con `doi LIKE '10.1109%'` sin PDF local
- Actualizar `documentos` y `papers.url_pdf`

## Manifest de ejemplo

Ver skill `ieee-xplore-downloader` para columnas: `document_id`, `doi`, `url`, `pdf_url`, `status`, `local_path`.
