"""Gunicorn settings for the public app (app.public:app). Read by: gunicorn -c deploy/gunicorn.conf.py app.public:app"""
import os

bind = os.environ.get("BIND", "0.0.0.0:8000")
workers = int(os.environ.get("WEB_CONCURRENCY", 3))
threads = 2
worker_class = "gthread"
timeout = 30
graceful_timeout = 20
keepalive = 5
max_requests = 2000                 # recycle workers (bounds memory, incl. the in-process rate limiter)
max_requests_jitter = 200
limit_request_line = 4094
limit_request_fields = 50
limit_request_field_size = 8190
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")   # only Caddy can reach the container port
accesslog = "-"
errorlog = "-"
loglevel = "info"
# no query strings or headers beyond the request line are logged; nothing sensitive is accepted by the app anyway
access_log_format = '%(h)s "%(m)s %(U)s" %(s)s %(b)s %(L)ss'
