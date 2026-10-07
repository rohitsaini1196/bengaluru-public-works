"""Hardening for the public deployment: read-only HTTP surface, security headers, a small per-client rate
limit, request-size limits and error pages that never show internals.

The rate limiter is in-process (per Gunicorn worker); it is a backstop, not the only control — the reverse
proxy (Caddy) also limits request bodies and timeouts. See DEPLOYMENT.md."""
import time
from collections import defaultdict, deque

from flask import render_template, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.core import DatabaseMissing

SAFE_METHODS = {"GET", "HEAD"}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'none'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class RateLimiter:
    """Sliding-window limit per client address: `limit` requests per `window` seconds (API paths: `api_limit`)."""

    def __init__(self, limit=120, api_limit=30, window=60, max_clients=10000):
        self.limit, self.api_limit, self.window, self.max_clients = limit, api_limit, window, max_clients
        self.hits = defaultdict(deque)

    def allow(self, client, path, now=None):
        now = time.monotonic() if now is None else now
        key = (client, path.startswith("/api/"))
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        cap = self.api_limit if key[1] else self.limit
        if len(q) >= cap:
            return False
        q.append(now)
        if len(self.hits) > self.max_clients:      # bound memory: drop idle clients
            for k in [k for k, v in self.hits.items() if not v][: len(self.hits) // 2]:
                self.hits.pop(k, None)
        return True


def harden(app):
    cfg = app.config
    cfg.update(DEBUG=False, TESTING=cfg.get("TESTING", False), PROPAGATE_EXCEPTIONS=False,
               MAX_CONTENT_LENGTH=16 * 1024, JSON_SORT_KEYS=False)
    if cfg.get("TRUST_PROXY", True):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)   # exactly one proxy (Caddy) in front
    limiter = RateLimiter(cfg.get("RATE_LIMIT", 120), cfg.get("API_RATE_LIMIT", 30))
    app.extensions["rate_limiter"] = limiter

    @app.before_request
    def _guard():
        if request.method not in SAFE_METHODS:
            return _error(405, "This site is read-only.")
        if len(request.full_path) > 2048:
            return _error(414, "Request too long.")
        if cfg.get("RATE_LIMIT", 120) and not limiter.allow(request.remote_addr or "?", request.path):
            return _error(429, "Too many requests — please slow down.")
        return None

    @app.after_request
    def _headers(resp):
        h = resp.headers
        h["Content-Security-Policy"] = CSP
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "strict-origin-when-cross-origin"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.is_secure:
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if resp.status_code == 200 and "Cache-Control" not in h:
            h["Cache-Control"] = "public, max-age=300"
        h.pop("Server", None)
        return resp

    @app.errorhandler(HTTPException)
    def _http_error(e):
        return _error(e.code, {404: "Page not found.", 405: "This site is read-only.", 429: "Too many requests."}.get(e.code, e.name))

    @app.errorhandler(DatabaseMissing)
    def _no_db(_e):
        return _error(503, "The site is temporarily unavailable.")

    @app.errorhandler(Exception)
    def _unhandled(e):
        app.logger.exception("unhandled error on %s", request.path)      # details go to the server log only
        return _error(500, "Something went wrong. The error has been logged.")


def _error(code, message):
    try:
        body = render_template("error.html", code=code, message=message)
    except Exception:                                                       # never let the error page itself leak
        body = f"<!doctype html><title>{code}</title><p>{message}</p>"
    return body, code, {"Cache-Control": "no-store"}
