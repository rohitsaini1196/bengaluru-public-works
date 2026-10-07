"""Public entry point (read-only, hardened) over data/public/koramangala_public.db.

    gunicorn -c deploy/gunicorn.conf.py app.public:app      # production (behind Caddy; see DEPLOYMENT.md)
    python -m app.public                                    # local preview only, http://127.0.0.1:8000
"""
import os

from app import create_app

app = create_app("public")

if __name__ == "__main__":
    # Flask's built-in server, bound to localhost, for previewing the public build. Not for the internet.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), debug=False)
