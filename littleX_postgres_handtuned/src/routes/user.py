"""User auth routes — register / login.

Direct psycopg, no ORM. Both endpoints are single-statement: register is
one INSERT (with the unique constraint on username doing the duplicate
check for free), login is one SELECT against the same unique index.
"""

from datetime import datetime
from flask import Blueprint

from src import build_error, get_validated_body
from src import db


bp = Blueprint("user", __name__)


def _resp(payload, status_code=200):
    from flask import jsonify
    response = jsonify({"data": payload})
    response.status_code = status_code
    return response


@bp.route("/register", methods=["POST"])
def register():
    data = get_validated_body(["username", "password"])
    try:
        with db.conn() as c, c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (username, handle, password, created_at)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (data["username"], data["username"], data["password"], datetime.utcnow()),
            )
            row = cur.fetchone()
            uid = row["id"]
    except Exception as exc:
        # Most likely cause: unique constraint violation on username.
        # Anything else: bubble up so we see it during smoke testing.
        if "duplicate key" in str(exc).lower():
            return build_error("User with username already exists", 400)
        return build_error(f"register failed: {exc}", 500)

    return _resp({
        "username": data["username"],
        "token": data["username"],
        "root_id": str(uid),
    })


@bp.route("/login", methods=["POST"])
def login():
    data = get_validated_body(["username", "password"])
    with db.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE username = %s AND password = %s",
            (data["username"], data["password"]),
        )
        row = cur.fetchone()
    if not row:
        return build_error("User with provided username/password not found", 400)
    return _resp({
        "username": data["username"],
        "token": data["username"],
        "root_id": str(row["id"]),
    })
