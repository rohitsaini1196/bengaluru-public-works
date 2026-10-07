# Public, read-only image: the Flask app + the sanitised public database. No pipeline code, no raw data.
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt && useradd --system --uid 10001 --no-create-home web
COPY app/ app/
COPY deploy/gunicorn.conf.py deploy/gunicorn.conf.py
# the database is baked in read-only (rebuild the image to update data); see DEPLOYMENT.md
COPY --chmod=0444 data/public/koramangala_public.db data/public/koramangala_public.db
ENV PUBLIC_DB=/srv/data/public/koramangala_public.db TRUST_PROXY=1
USER web
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
CMD ["gunicorn", "-c", "deploy/gunicorn.conf.py", "app.public:app"]
