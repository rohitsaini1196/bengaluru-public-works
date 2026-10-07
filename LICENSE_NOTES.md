# Licensing and data release

This is not legal advice. Where rights are unclear, nothing is licensed, and the conservative choice (link, don't redistribute) is the default.

## Recommendation

| Material | Recommendation | Why |
|---|---|---|
| **Source code** (`app/`, `ingest/`, `experiments/`, `tests/`, `deploy/`, config) | **MIT** (in `LICENSE`) | Conventional and permissive, and compatible with every dependency: Flask, Werkzeug and Jinja2 (BSD-3), Gunicorn (MIT), openpyxl and certifi (MIT / MPL-2.0 data file), pypdf (BSD-3), Pillow (MIT-CMU style). Apache-2.0 is an equally good choice if an explicit patent grant matters to contributors. |
| **Generated metadata** (`data/public/dataset/`: project summaries, facts, signals, work items, cases, discovery table; and the public DB) | Release under **CC BY 4.0**, once the owner confirms | This is a new compilation: structure, links, confidence labels and analysis. The underlying facts are government records. Attribution keeps the provenance chain intact. |
| **Reports and case packs** (`*REPORT.md`, `cases/`, `CASE_INDEX.md`, `RTI_QUESTIONS.md`, `docs/`) | **CC BY 4.0**, same as the dataset | Original writing |
| **Cached government documents** (scanned work orders, bill forms, MBs, photos, KPPP files; `data/raw/`) | **Do not redistribute; link to the source URL** | Indian government works are protected by copyright (Copyright Act 1957, s.17(dd), s.52(1)(q)). Republication of some public documents is permitted, but no open licence covers these portals' attachments. The public site already links every document at its original URL. |
| **Site photos** attached in IFMS | **Do not redistribute** | Same as above; some photos show people |
| **Satellite before/after images** (`data/reality/`, Esri World Imagery via Wayback) | **Do not publish** | Esri's terms restrict redistribution. These images are shown only in the local dev app. |
| **OpenStreetMap-derived geocodes** (in facts) | ODbL attribution required | "© OpenStreetMap contributors" is kept in the evidence labels |

The copyright holder line in `LICENSE` reads "Bengaluru Public Works Explorer contributors". Replace it with a person or organisation name before publishing if you prefer.

## Public data release split

### In the repository (open source)
- Code, schemas (`ingest/schema.sql`), tests, deployment files, area configs.
- Methodology and reports, case packs (`cases/`), `CASE_INDEX.md`, `RTI_QUESTIONS.md`.
- The scrubbed public dataset in `data/public/dataset/` (≈6 MB of CSV/JSON, with `MANIFEST.json` checksums). Alternatively, attach it to a GitHub release instead of committing it.
- Small test fixtures inline in the tests. The tests that need the full database skip when it is absent.

### Public generated dataset (release asset / website)
- `data/public/koramangala_public.db` (≈8 MB, scrubbed), committed to the repository because GitHub Pages builds the static site from it.
- The same content as CSV/JSON in `data/public/dataset/`.

### Excluded
| Path | Size | Reason |
|---|---|---|
| `data/raw/` (IFMS caches, attachments, KPPP documents, Wayback copies) | ≈3.6 GB | Unclear redistribution rights; duplicated PDFs and images; may contain contact details in scanned letterheads |
| `data/koramangala.db`, `data/*.json` (quantities, case evidence, EoT, investigation cases) | ≈10 MB | Unscrubbed working copies: their OCR rows can include letterhead phone numbers and e-mails. The public DB and dataset are the scrubbed equivalents. |
| `data/reality/` | small | Esri imagery terms |

`.gitignore` and `.dockerignore` enforce this split.
