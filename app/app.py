"""Development entry point: the full working database plus dev-only routes, bound to localhost.

    python -m app.app            # http://127.0.0.1:5000  (PORT=… to change)

Never expose this to the internet — use app.public (see DEPLOYMENT.md)."""
import os
from pathlib import Path

from app import create_app

app = create_app("dev")
DB_PATH = Path(app.config["DB_PATH"])

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
