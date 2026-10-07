"""Bengaluru Public Works Explorer — web app factory.

    create_app("public")  read-only public site over the sanitised database (data/public/koramangala_public.db):
                          public routes only, hardening on (app/security.py), debug forced off
    create_app("dev")     the same pages over the full working database, plus dev-only routes (app/devtools.py)
"""
import logging
import os
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
DEV_DB = ROOT / "data" / "koramangala.db"
PUBLIC_DB = ROOT / "data" / "public" / "koramangala_public.db"


def create_app(mode="dev", **overrides):
    from app import core, site

    if mode not in ("dev", "public"):
        raise ValueError(f"unknown mode {mode!r}")
    public = mode == "public"
    app = Flask(__name__)
    app.config.update(
        MODE=mode, PUBLIC=public,
        DB_PATH=os.environ.get("PUBLIC_DB" if public else "KORA_DB", str(PUBLIC_DB if public else DEV_DB)),
        ENABLE_IMAGERY=not public,                 # Esri-derived images: redistribution terms unclear (LICENSE_NOTES.md)
        PUBLIC_REPO_URL=os.environ.get("PUBLIC_REPO_URL", ""),
        PUBLIC_CONTACT=os.environ.get("PUBLIC_CONTACT", ""),
        RATE_LIMIT=int(os.environ.get("RATE_LIMIT", 120)), API_RATE_LIMIT=int(os.environ.get("API_RATE_LIMIT", 30)),
        TRUST_PROXY=os.environ.get("TRUST_PROXY", "1") == "1",
        DEBUG=False,
    )
    app.config.update(overrides)
    core.install(app)
    with app.app_context():
        site.register(app)
    if public:
        from app import security
        security.harden(app)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    else:
        from app import devtools
        devtools.register(app)
    return app
