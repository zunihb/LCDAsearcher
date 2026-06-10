# Esquema de base de datos

Archivo: `data/lcda.db` (SQLite + WAL)

## Diagrama conceptual

```
investigadores ──< autorias >── papers ──< paper_keywords >── keywords
       │                            │
       │                            ├──< paper_autores
       └──< coautores               │
                                    ├── doi, url_doi, url_ieee, url_pdf
                                    └── abstract (texto completo)
```

## Tablas

### `investigadores`

| Campo | Descripción |
|-------|-------------|
| `scholar_id` | PK, ID Google Scholar |
| `nombre`, `afiliacion` | Perfil |
| `citas_total`, `indice_h`, `indice_i10` | Métricas Scholar |

### `papers`

| Campo | Fuente típica |
|-------|---------------|
| `titulo`, `anio`, `citado_por` | Scholar |
| `abstract` | OpenAlex (o Scholar fill) |
| `doi`, `url_doi` | OpenAlex |
| `url_ieee` | OpenAlex (revistas IEEE) |
| `url_pdf` | OpenAlex OA o IEEE/repositorio |
| `url_scholar` | Scholar |
| `openalex_id` | OpenAlex |
| `venue` | OpenAlex / Scholar |
| `autores_texto` | Lista separada por comas |

### `paper_autores`

Autores **por paper** con afiliación (OpenAlex).

| Campo | Descripción |
|-------|-------------|
| `paper_id`, `nombre`, `afiliacion` | |
| `orden` | Posición en la lista de autores |
| `openalex_author_id` | ID OpenAlex |

### `coautores`

Red de coautores frecuentes del **perfil Scholar** (no por paper).

### `keywords` / `paper_keywords`

- **15 keywords** por paper (configurable)
- `termino` + `termino_canonico` (normalización LLM opcional)

### `citas`

Citantes acotados (top-5 papers, max 50 c/u).

### `tendencias_globales`

Cache OpenAlex por keyword y año.

### `pipeline_metricas`

Tiempos por paso: `extract`, `abstracts`, `keywords`, `trends`, etc.

## Migraciones

`db.init_schema()` aplica `ALTER TABLE` para columnas nuevas en BDs existentes (`url_doi`, `url_ieee`, …).

## Consultas útiles

```sql
-- Papers con abstract
SELECT COUNT(*) FROM papers WHERE abstract IS NOT NULL AND trim(abstract) != '';

-- Papers IEEE
SELECT id, titulo, doi, url_ieee FROM papers WHERE doi LIKE '10.1109%';

-- Autores de un paper
SELECT nombre, afiliacion FROM paper_autores WHERE paper_id = 1 ORDER BY orden;
```
