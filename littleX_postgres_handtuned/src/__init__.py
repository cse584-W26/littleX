"""LittleX hand-tuned Postgres backend.

Flask app + raw psycopg + hand-written SQL. NO SQLAlchemy. The schema is
designed for the LittleX workload from scratch (see schema.sql) and every
hot read path returns the entire response shape as a single JSON document
built inside Postgres via `json_build_object`/`json_agg`, so the Flask
layer just relays bytes.

This is the "expert hand-tuned Postgres baseline" — paired with
littleX_sqlalchemy/ (the fair-ORM baseline) it shows the gap between
"competent ORM developer" and "expert direct SQL".
"""

import os
from datetime import datetime

from flask import Flask, request, jsonify, abort

from src import db


app = Flask(__name__)


def build_error(message: str, status_code: int):
    response = jsonify({"error": message})
    response.status_code = status_code
    return response


def get_validated_body(keys):
    if not request.is_json:
        abort(build_error("Expected JSON body", 415))
    data = request.get_json()
    for key in keys:
        if key not in data:
            abort(build_error(f"Missing expected key {key}", 422))
    return data


# ---------------------------------------------------------------------------
# Bootstrap the schema on first request. We can't run it at import time
# because Postgres might not be ready yet (the K8s pod starts in parallel
# with the Postgres pod). Lazy bootstrap on first request handles this.
# ---------------------------------------------------------------------------

_bootstrapped = False


@app.before_request
def _ensure_schema():
    global _bootstrapped
    if not _bootstrapped:
        try:
            db.bootstrap()
            _bootstrapped = True
        except Exception as exc:
            print(f"[db] bootstrap failed: {exc}")
            # Don't mark as bootstrapped — try again on next request.


# ---------------------------------------------------------------------------
# Register blueprints. Register under both /walker/ and /function/ to mirror
# the convention used by littleX_sqlalchemy/ so the bench client works
# unchanged regardless of which prefix it expects.
# ---------------------------------------------------------------------------

from src.routes.user import bp as user_bp  # noqa: E402
from src.routes.walker import bp as walker_bp  # noqa: E402

app.register_blueprint(user_bp, url_prefix="/user")
app.register_blueprint(walker_bp, url_prefix="/walker")
app.register_blueprint(walker_bp, url_prefix="/function", name="function")


# ---------------------------------------------------------------------------
# clear_data — used by the bench scripts between runs
# ---------------------------------------------------------------------------

def _reset_db():
    db.reset()
    return jsonify({
        "data": {
            "result": {"success": True, "message": "Database reset"},
            "reports": [{"success": True, "message": "Database reset"}],
        }
    })


app.add_url_rule("/walker/clear_data", view_func=_reset_db, methods=["POST"])
app.add_url_rule("/function/clear_data", view_func=_reset_db, methods=["POST"],
                 endpoint="function_clear_data")
