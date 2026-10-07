# Deployment

## Option A (free, recommended): GitHub Pages

The public site is read-only, so it is exported as static HTML (`python -m app.freeze`) and served by GitHub Pages:
- no server, no cost, HTTPS by GitHub;
- search and filters run in the browser (`app/static/explorer.js`; a test checks they match the server's filters exactly).

One-time setup:
1. Create a GitHub repository and push this code, **including** `data/public/koramangala_public.db` (the scrubbed public DB, ~8 MB; `.gitignore` allows it).
2. Repository **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. Push to `main`. `.github/workflows/pages.yml`:
   - runs the public-surface and static-export tests;
   - freezes the site with the repository's base path;
   - deploys it to `https://<user>.github.io/<repo>/`.

Update the data: run `python -m ingest.publish` locally, commit the new `data/public/koramangala_public.db`, and push.

Preview the export locally:

```bash
.venv/bin/python -m app.freeze --base /bengaluru-public-works/ --out site     # 789 pages, ~14 MB, link-checked
mkdir -p /tmp/pages && rm -rf /tmp/pages/bengaluru-public-works && cp -R site /tmp/pages/bengaluru-public-works
python3 -m http.server 8090 --directory /tmp/pages                # open http://127.0.0.1:8090/bengaluru-public-works/
```

Custom domain (optional, free with Pages):
1. Add it under Settings → Pages.
2. Point a CNAME record at `<user>.github.io`.

The workflow picks up the new base path (`/`) automatically.

What Pages can't do, compared with option B:
- **No custom HTTP headers.** The CSP is set by a `<meta>` tag instead; frame and HSTS headers are GitHub's defaults.
- **No live JSON API.** `api/projects.json` and `api/cases.json` are static files instead.

Neither affects a read-only static site, which has no server-side attack surface.

## Option B: your own server (Docker + Caddy + Gunicorn)

```
Internet ──► Caddy (HTTPS, HTTP→HTTPS, 16 KB body limit, GET/HEAD only, header hygiene)
               │
               ▼
           Gunicorn (3 workers × 2 threads, 30 s timeout)  — container "web", read-only filesystem, non-root, no capabilities
               │
               ▼
           Flask public app  (app.public:app — read-only routes, security headers, per-client rate limit, generic errors)
               │
               ▼
           data/public/koramangala_public.db  (sanitised, chmod 444, opened mode=ro&immutable=1, baked into the image)
```

There are no secrets: the app holds no credentials and writes nothing. Optional settings are environment variables:

| Variable | Purpose | Default |
|---|---|---|
| `DOMAIN` | public hostname for Caddy / Let's Encrypt (**required**) | — |
| `ACME_EMAIL` | contact for Let's Encrypt expiry notices | empty |
| `PUBLIC_REPO_URL` | GitHub link shown in the header | hidden |
| `PUBLIC_CONTACT` | contact text shown on the About page | hidden |
| `WEB_CONCURRENCY` | Gunicorn workers | 3 |
| `RATE_LIMIT` / `API_RATE_LIMIT` | requests per minute per client (pages / `/api/`) | 120 / 30 |

Keep them in a `.env` next to `docker-compose.yml`. `.env` is git-ignored.

## 1. Environment setup (build machine)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # pipeline + tests
brew install tesseract                        # or: apt install tesseract-ocr   (only to re-run OCR stages)
```

## 2. Data preparation

The public site serves only the sanitised database. To produce it from the working build:

```bash
.venv/bin/python -m ingest.build              # if data/koramangala.db needs rebuilding (uses cached raw data)
.venv/bin/python -m ingest.publish            # -> data/public/koramangala_public.db (+ data/public/dataset/)
.venv/bin/python -m pytest -q                 # must pass, including tests/test_public.py
```

`ingest.publish` aborts if any phone or e-mail pattern survives the scrub.

## 3. Run locally in public mode

```bash
.venv/bin/python -m app.public                # http://127.0.0.1:8000 — Flask preview server, localhost only
```

The same as production, but with Gunicorn (requires `pip install -r requirements-web.txt`):

```bash
BIND=127.0.0.1:8000 .venv/bin/gunicorn -c deploy/gunicorn.conf.py app.public:app
```

Never expose `python -m app.app` (dev mode) or Flask's built-in server to the internet.

## 4. Production start (any Linux VM with Docker)

```bash
# on the server, in a checkout that contains data/public/koramangala_public.db (built in step 2, or copied in)
cat > .env <<'EOF'
DOMAIN=example.org
ACME_EMAIL=you@example.org
PUBLIC_REPO_URL=https://github.com/<owner>/<repo>
EOF
docker compose up -d --build
docker compose ps                              # web should be "healthy"
curl -sI https://example.org/ | head           # 200, HSTS + CSP headers
```

DNS: point an `A`/`AAAA` record for `DOMAIN` at the server before starting. Caddy needs ports 80 and 443 reachable to obtain the certificate.

## 5. Reverse proxy and HTTPS

`deploy/Caddyfile` does the following:
- obtains and renews Let's Encrypt certificates automatically and redirects HTTP to HTTPS;
- compresses responses;
- caps request bodies at 16 KB and sets timeouts;
- refuses any method other than GET/HEAD with 405;
- removes the `Server` header and adds HSTS;
- logs requests to stdout without request headers and with client IPs masked to /24 (IPv4) or /48 (IPv6).

The app trusts exactly one proxy hop (`X-Forwarded-For`) for the rate limiter. The `web` container port is not published on the host.

## 6. Updates

Data or code change:

```bash
.venv/bin/python -m ingest.publish && .venv/bin/python -m pytest -q
git pull            # code (if any)
docker compose build web && docker compose up -d web       # Caddy keeps running; ~seconds of restart
```

The database is baked into the image, so every data update is a new image. Tag images to keep the previous one:

```bash
docker tag bengaluru-public-works:latest bengaluru-public-works:$(date +%Y%m%d)
```

## 7. Rollback

```bash
docker image ls bengaluru-public-works                                # find the previous tag
docker tag bengaluru-public-works:20261007 bengaluru-public-works:latest
docker compose up -d --no-build web
```

## 8. Backups

There is nothing to back up on the server: it holds no user data, and the app writes nothing.

Back up the **build machine**:
- `data/koramangala.db` and `data/public/`, which can be regenerated from the raw caches;
- `data/raw/` (3.6 GB of cached source responses and attachments). This is expensive to re-collect politely, so keep a cold copy.

Caddy's certificate store lives in the `caddy_data` volume; losing it only means re-issuing certificates.

## Alternative without Docker (systemd)

```ini
# /etc/systemd/system/bengaluru-public-works.service
[Service]
User=kora
WorkingDirectory=/srv/kora
Environment=PUBLIC_DB=/srv/kora/data/public/koramangala_public.db BIND=127.0.0.1:8000
ExecStart=/srv/kora/.venv/bin/gunicorn -c deploy/gunicorn.conf.py app.public:app
ProtectSystem=strict
ReadOnlyPaths=/srv/kora
NoNewPrivileges=true
Restart=on-failure
```

Put Caddy (the same Caddyfile, with `reverse_proxy 127.0.0.1:8000`) in front of it.
