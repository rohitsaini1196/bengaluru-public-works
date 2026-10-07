"""Dev-only routes. Registered only by create_app("dev") — never in the public app.

These serve local files and are useful while working on the pipeline, but must not face the internet."""
from pathlib import Path

from flask import abort, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
REALITY_DIR = ROOT / "data" / "reality"


def register(app):
    @app.route("/reality/<slug>/<path:fname>")
    def reality_file(slug, fname):
        """Satellite before/after images from experiments/reality_check.py (Esri imagery; see LICENSE_NOTES.md)."""
        if not (REALITY_DIR / slug).is_dir():
            abort(404)
        return send_from_directory(REALITY_DIR / slug, fname)       # safe_join: no traversal outside the folder
