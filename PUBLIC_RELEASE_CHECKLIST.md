# Public release checklist

Status as of 7 Oct 2026. `[x]` = done and verified locally; `[ ]` = needs a decision or a step that can only be done at release.

## Application
- [x] All tests pass: 179 (`.venv/bin/python -m pytest -q`)
- [x] Debug disabled: `create_app("public")` forces `DEBUG=False` and `PROPAGATE_EXCEPTIONS=False` (tested)
- [x] No write, admin, ingestion or crawl routes in the public app. Every route is GET/HEAD; POST/PUT/PATCH/DELETE return 405 (tested, and again at Caddy)
- [x] Dev-only routes (`/reality/...` file server) registered only in dev mode (tested)
- [x] Error pages do not leak internals: 404, 405, 414, 429, 500, 503 are generic; DB path and exceptions go only to the server log (tested)
- [x] Security headers: CSP (no scripts), nosniff, frame DENY, Referrer-Policy, Permissions-Policy, COOP; HSTS over HTTPS (tested)
- [x] Request limits: 16 KB body, 2 KB URL, per-client rate limit (120/min pages, 30/min API) (tested)
- [x] Public DB is read-only: chmod 444, opened `mode=ro&immutable=1`, writes fail (tested)
- [ ] Mobile layout acceptable. The CSS is responsive and the nav wraps under 640 px; **check on a real phone before announcing**

## Data and privacy
- [x] Secret scan of every file that `.gitignore` lets through (102 files): no API keys, tokens, cookies or passwords. The only certificate (`ingest/certs/`) is a public CA intermediate. No personal paths, no personal names.
- [x] Test fixtures: a real contractor mobile number (Phase 1 entity-resolution test) and a real BBMP office e-mail/landline (scrubber test) were replaced with synthetic values
- [x] Contact data removed: contractor mobile and e-mail stripped at collection; OCR'd letterhead e-mails and phones (6 found) scrubbed at publish; publish aborts on any survivor; every public page scanned in tests
- [x] Raw caches excluded (`.gitignore`, `.dockerignore`); the image contains only `app/`, Gunicorn config and the public DB
- [x] Public DB / read model prepared (`python -m ingest.publish`) with a CSV/JSON dataset and checksums
- [ ] **Decide the dataset licence (CC BY 4.0 recommended) and the copyright holder name in `LICENSE`**
- [x] Unrelated material (`fitment_study/`, `screenshots/`, `hinglish_search_benchmark.xlsx`) moved out of the repository folder to `../kora_bots_unrelated/`

## Content
- [x] Methodology page complete (sources, discovery, entity resolution, truth model, quantities, signals, case review, "signals can disappear")
- [x] Coverage and limitations page complete (data-driven numbers plus every known limitation)
- [x] Investigation case pages complete: 5 retained cases, plus the set-aside list with reasons
- [x] Project pages: promised / paid / documented / quantities / timeline / signals / evidence
- [ ] **Source links work.** Spot-checked during the research phases; **re-check a sample of IFMS and KPPP links just before release** (government portals move: the old IFMS host already died once)
- [x] README ready; DEPLOYMENT.md, docs/PRIVACY.md, LICENSE, LICENSE_NOTES.md, BLOG_NOTES.md, OUTREACH_NOTES.md written

## Infrastructure: GitHub Pages (free, recommended)
- [x] Static export builds (789 pages, ~14 MB) with 0 broken internal links; browser filters verified equal to server filters (tests/test_static.py)
- [x] Workflow `.github/workflows/pages.yml` runs the public tests, freezes with the repository's base path and deploys
- [ ] **Create the GitHub repository, push (including `data/public/koramangala_public.db`), and set Settings → Pages → Source: GitHub Actions**
- [ ] Check the first Actions run and open `https://<user>.github.io/<repo>/`

## Infrastructure: self-hosted alternative
- [x] Deployment documented (Docker Compose: Caddy → Gunicorn → Flask; systemd alternative)
- [x] HTTPS ready (Caddy automatic certificates; HSTS)
- [ ] **Docker image built and smoke-tested.** Not possible on the preparation machine (Docker daemon not running; package installs blocked by its sandbox). **First step on the deployment host: `docker compose up -d --build`, then the smoke test below**
- [ ] **Domain and server chosen; DNS pointed** (deliberately not done: no hosting purchased)
- [ ] Public repository created and `PUBLIC_REPO_URL` set. No git history exists yet, so there is nothing to clean from history. Run `git init` after moving unrelated files out.
- [ ] Public site smoke-tested at the real domain

## Smoke test (run against the deployed URL)
```bash
U=https://example.org
for p in / /projects /cases /methodology /coverage /about /sources /healthz; do curl -s -o /dev/null -w "%{http_code} $p\n" $U$p; done   # all 200
curl -s -o /dev/null -w "%{http_code}\n" -X POST $U/            # 405
curl -s -o /dev/null -w "%{http_code}\n" $U/reality/x/y.jpg     # 404
curl -sI $U/ | grep -iE "strict-transport|content-security|x-frame"
```
