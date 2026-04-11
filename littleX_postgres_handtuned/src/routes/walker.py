"""Hand-tuned LittleX route handlers (no ORM, no Flask-SQLAlchemy).

Every endpoint here is hand-written SQL designed to be as fast as a
competent Postgres developer can make it on the LittleX workload:

* Read paths (load_feed, get_profile, get_all_profiles) build the entire
  response shape inside Postgres via `json_build_object` / `json_agg`,
  so the Flask layer just relays the JSON bytes. This eliminates the
  per-tweet Python object instantiation that crushes ORM-based
  implementations on load_feed.

* Write paths (create_tweet, follow_user, like_tweet, delete_tweet) use
  parameterized prepared statements (psycopg's `prepare_threshold=0`
  setting in db.py makes every parameterized query server-side
  prepared after first use). All single-round-trip where possible.

* like_tweet uses INSERT ... ON CONFLICT DO NOTHING for the toggle path
  rather than the ORM "load relationship → modify → commit" dance,
  cutting it from 3 round-trips to 2.

* All FK lookups are indexed. The (author_id, created_at DESC) covering
  index on tweets is what makes load_feed fast.
"""

from datetime import datetime

from flask import Blueprint, request, g, abort, jsonify
import psycopg

from src import build_error, get_validated_body
from src import db


bp = Blueprint("walker", __name__)


# ---------------------------------------------------------------------------
# Response shape — matches both the Jac walker convention (data.reports[0])
# and the JacSQL convention (data.result). The bench client reads from
# data.reports for collection ops and data.reports[0] for singletons.
# ---------------------------------------------------------------------------

def build_response(reports, result=None, status_code=200):
    if result is None:
        result = reports[0] if len(reports) == 1 else reports
    response = jsonify({"data": {"result": result, "reports": reports}})
    response.status_code = status_code
    return response


def singleton_response(payload, status_code=200):
    return build_response([payload], result=payload, status_code=status_code)


def list_response(items, status_code=200):
    return build_response(items, result=items, status_code=status_code)


# ---------------------------------------------------------------------------
# Auth — every authenticated request looks up the user by username via
# the unique index on `users.username`. O(log N) per request.
# ---------------------------------------------------------------------------

PUBLIC_ENDPOINTS = {"get_all_profiles", "import_data"}


@bp.before_request
def check_login():
    endpoint = (request.endpoint or "").rsplit(".", 1)[-1]
    if endpoint in PUBLIC_ENDPOINTS:
        return

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        abort(build_error("Not Logged In", 401))

    # Naive bearer convention shared with the SQLAlchemy variant.
    username = auth_header.replace("Bearer ", "")
    with db.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, username, handle, bio FROM users WHERE username = %s",
            (username,),
        )
        row = cur.fetchone()
    if not row:
        abort(build_error(
            f"User with username {username} not found. Did the database get cleared?",
            400,
        ))
    g.user = row


# ---------------------------------------------------------------------------
# setup_profile — single UPDATE
# ---------------------------------------------------------------------------

@bp.route("/setup_profile", methods=["POST"])
def setup_profile():
    data = request.get_json(silent=True) or {}
    new_handle = data.get("username")
    new_bio = data.get("bio")

    sets = []
    params = []
    if new_handle:
        sets.append("handle = %s")
        params.append(new_handle)
    if new_bio is not None:
        sets.append("bio = %s")
        params.append(new_bio)

    if sets:
        params.append(g.user["id"])
        with db.conn() as c, c.cursor() as cur:
            cur.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = %s "
                f"RETURNING id, handle, bio, created_at",
                params,
            )
            row = cur.fetchone()
    else:
        row = g.user

    return singleton_response({
        "id": row["id"],
        "username": row.get("handle", ""),
        "bio": row.get("bio", ""),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else "",
    })


# ---------------------------------------------------------------------------
# load_feed — THE hot path. Single SQL statement that:
#   1. UNIONs my own tweets and tweets from people I follow
#   2. Optionally filters by substring (uses pg_trgm GIN index)
#   3. Joins author username, like usernames, and comments inline
#   4. Returns the entire response as a JSON array built in Postgres
#
# The Flask layer never instantiates a Tweet/User/Like Python object.
# It receives a single JSON blob and relays it to the client.
# ---------------------------------------------------------------------------

@bp.route("/load_feed", methods=["POST"])
def load_feed():
    data = request.get_json(silent=True) or {}
    search_query = data.get("search_query", "") or ""
    limit_clause = ""
    params = [g.user["id"], g.user["id"], search_query, search_query]
    if "limit" in data:
        limit_clause = "LIMIT %s"
        params.append(int(data["limit"]))

    sql = f"""
        WITH eligible AS (
            SELECT id FROM users WHERE id = %s
            UNION
            SELECT followee_id AS id FROM follows WHERE follower_id = %s
        ),
        feed AS (
            SELECT
                t.id,
                t.content,
                t.created_at,
                u.username AS author_username
            FROM tweets t
            JOIN eligible e ON e.id = t.author_id
            JOIN users u ON u.id = t.author_id
            WHERE %s = '' OR t.content ILIKE '%%' || %s || '%%'
            ORDER BY t.created_at DESC
            {limit_clause}
        )
        SELECT COALESCE(json_agg(
            json_build_object(
                'id', f.id,
                'content', f.content,
                'author_username', f.author_username,
                'created_at', f.created_at,
                'likes', COALESCE((
                    SELECT json_agg(u2.username)
                    FROM likes l JOIN users u2 ON u2.id = l.user_id
                    WHERE l.tweet_id = f.id
                ), '[]'::json),
                'comments', COALESCE((
                    SELECT json_agg(json_build_object(
                        'username', c.author_handle,
                        'content', c.content,
                        'created_at', c.created_at
                    ) ORDER BY c.created_at)
                    FROM comments c WHERE c.tweet_id = f.id
                ), '[]'::json)
            )
        ), '[]'::json) AS feed
        FROM feed f
    """
    with db.conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return list_response(row["feed"] or [])


# ---------------------------------------------------------------------------
# get_profile — single SQL statement that returns the entire profile
# (with following, followers, tweets) as a JSON document.
# ---------------------------------------------------------------------------

@bp.route("/get_profile", methods=["POST"])
def get_profile():
    sql = """
        SELECT json_build_object(
            'id', u.id,
            'username', u.handle,
            'bio', u.bio,
            'created_at', u.created_at,
            'following', COALESCE((
                SELECT json_agg(json_build_object('id', u2.id, 'username', u2.username))
                FROM follows f
                JOIN users u2 ON u2.id = f.followee_id
                WHERE f.follower_id = u.id
            ), '[]'::json),
            'followers', COALESCE((
                SELECT json_agg(json_build_object('id', u3.id, 'username', u3.username))
                FROM follows f
                JOIN users u3 ON u3.id = f.follower_id
                WHERE f.followee_id = u.id
            ), '[]'::json),
            'tweets', COALESCE((
                SELECT json_agg(json_build_object(
                    'id', t.id,
                    'content', t.content,
                    'author_username', u.username,
                    'created_at', t.created_at,
                    'likes', COALESCE((
                        SELECT json_agg(u4.username)
                        FROM likes l JOIN users u4 ON u4.id = l.user_id
                        WHERE l.tweet_id = t.id
                    ), '[]'::json),
                    'comments', COALESCE((
                        SELECT json_agg(json_build_object(
                            'username', c.author_handle,
                            'content', c.content,
                            'created_at', c.created_at
                        ))
                        FROM comments c WHERE c.tweet_id = t.id
                    ), '[]'::json)
                ) ORDER BY t.created_at DESC)
                FROM tweets t WHERE t.author_id = u.id
            ), '[]'::json)
        ) AS profile
        FROM users u WHERE u.id = %s
    """
    with db.conn() as c, c.cursor() as cur:
        cur.execute(sql, (g.user["id"],))
        row = cur.fetchone()
    if not row or not row["profile"]:
        return build_error("Profile not found", 404)
    return singleton_response(row["profile"])


# ---------------------------------------------------------------------------
# get_all_profiles — single SELECT, no JSON aggregation needed
# ---------------------------------------------------------------------------

@bp.route("/get_all_profiles", methods=["POST"])
def get_all_profiles():
    with db.conn() as c, c.cursor() as cur:
        cur.execute("SELECT id, handle, bio FROM users")
        rows = cur.fetchall()
    profiles = [{"id": r["id"], "username": r["handle"], "bio": r["bio"]} for r in rows]
    return list_response(profiles)


# ---------------------------------------------------------------------------
# follow_user — single INSERT with idempotent ON CONFLICT
# ---------------------------------------------------------------------------

@bp.route("/follow_user", methods=["POST"])
def follow_user():
    data = get_validated_body(["target_id"])
    try:
        target_id = int(data["target_id"])
    except (TypeError, ValueError):
        return build_error("Invalid target_id", 400)

    try:
        with db.conn() as c, c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO follows (follower_id, followee_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (g.user["id"], target_id),
            )
    except psycopg.errors.ForeignKeyViolation:
        return build_error("User not found", 400)

    return singleton_response({"success": True})


# ---------------------------------------------------------------------------
# unfollow_user
# ---------------------------------------------------------------------------

@bp.route("/unfollow_user", methods=["POST"])
def unfollow_user():
    data = get_validated_body(["target_id"])
    try:
        target_id = int(data["target_id"])
    except (TypeError, ValueError):
        return build_error("Invalid target_id", 400)

    with db.conn() as c, c.cursor() as cur:
        cur.execute(
            "DELETE FROM follows WHERE follower_id = %s AND followee_id = %s",
            (g.user["id"], target_id),
        )
        if cur.rowcount == 0:
            return build_error("User not found", 400)
    return singleton_response({"success": True})


# ---------------------------------------------------------------------------
# create_tweet — single INSERT with RETURNING
# ---------------------------------------------------------------------------

@bp.route("/create_tweet", methods=["POST"])
def create_tweet():
    data = get_validated_body(["content"])
    with db.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tweets (author_id, content)
            VALUES (%s, %s)
            RETURNING id, created_at
            """,
            (g.user["id"], data["content"]),
        )
        row = cur.fetchone()

    return singleton_response({
        "id": row["id"],
        "content": data["content"],
        "author_username": g.user.get("username", ""),
        "created_at": row["created_at"].isoformat(),
        "likes": [],
        "comments": [],
    })


# ---------------------------------------------------------------------------
# delete_tweet — single DELETE with author check
# ---------------------------------------------------------------------------

@bp.route("/delete_tweet", methods=["POST"])
def delete_tweet():
    data = get_validated_body(["tweet_id"])
    try:
        tid = int(data["tweet_id"])
    except (TypeError, ValueError):
        return build_error("Invalid tweet_id", 400)

    with db.conn() as c, c.cursor() as cur:
        cur.execute(
            "DELETE FROM tweets WHERE id = %s AND author_id = %s",
            (tid, g.user["id"]),
        )
        if cur.rowcount == 0:
            return build_error("Tweet not found", 404)
    return singleton_response({"success": True})


# ---------------------------------------------------------------------------
# like_tweet — atomic toggle. We do two round-trips:
#   (1) Try to INSERT. If a row appears (rowcount=1), the user wasn't liking.
#       If ON CONFLICT swallows it (rowcount=0), the user already liked.
#   (2) Decide to keep the insert (new like) or DELETE (toggle off).
#   (3) Read back the new likes list.
#
# Three statements, two round-trips because (1) and (2) can be combined into
# one CTE — but Postgres MVCC means the read-back has to be a separate
# statement to see the post-update state. Still better than the ORM version
# which loaded the entire likes relationship.
# ---------------------------------------------------------------------------

@bp.route("/like_tweet", methods=["POST"])
def like_tweet():
    data = get_validated_body(["tweet_id"])
    try:
        tid = int(data["tweet_id"])
    except (TypeError, ValueError):
        return build_error("Invalid tweet_id", 400)

    uid = g.user["id"]

    with db.conn() as c, c.cursor() as cur:
        # First check if the tweet exists at all (we need to distinguish
        # "tweet not found" from "user already likes" / "user doesn't like").
        cur.execute("SELECT 1 FROM tweets WHERE id = %s", (tid,))
        if cur.fetchone() is None:
            return build_error("Tweet not found", 404)

        # Try to insert. If it succeeds, the user wasn't liking. If it
        # conflicts (rowcount=0), they were liking and we should toggle off.
        cur.execute(
            "INSERT INTO likes (tweet_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (tid, uid),
        )
        liked = cur.rowcount > 0
        if not liked:
            cur.execute(
                "DELETE FROM likes WHERE tweet_id = %s AND user_id = %s",
                (tid, uid),
            )

        # Read back the new likes list.
        cur.execute(
            """
            SELECT COALESCE(json_agg(u.username), '[]'::json) AS likes
            FROM likes l JOIN users u ON u.id = l.user_id
            WHERE l.tweet_id = %s
            """,
            (tid,),
        )
        row = cur.fetchone()

    return singleton_response({"liked": liked, "likes": row["likes"] or []})


# ---------------------------------------------------------------------------
# add_comment — single INSERT
# ---------------------------------------------------------------------------

@bp.route("/add_comment", methods=["POST"])
def add_comment():
    data = get_validated_body(["tweet_id", "content"])
    try:
        tid = int(data["tweet_id"])
    except (TypeError, ValueError):
        return build_error("Invalid tweet_id", 400)

    try:
        with db.conn() as c, c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO comments (tweet_id, author_handle, content)
                VALUES (%s, %s, %s)
                RETURNING id, created_at
                """,
                (tid, g.user.get("handle", ""), data["content"]),
            )
            row = cur.fetchone()
    except psycopg.errors.ForeignKeyViolation:
        return build_error("Tweet not found", 404)

    return singleton_response({
        "success": True,
        "comment": {
            "username": g.user.get("handle", ""),
            "content": data["content"],
            "created_at": row["created_at"].isoformat(),
        },
    })


# ---------------------------------------------------------------------------
# import_data — bulk insert via executemany / COPY
# ---------------------------------------------------------------------------

@bp.route("/import_data", methods=["POST"])
def import_data():
    if not request.is_json:
        abort(build_error("Expected JSON body", 415))
    data = request.get_json()

    with db.conn() as c, c.cursor() as cur:
        # Look up all existing user ids and usernames in one query.
        cur.execute("SELECT id, username FROM users")
        all_users = cur.fetchall()
        all_user_ids = [u['id'] for u in all_users]
        username_to_id = {u["username"]: u["id"] for u in all_users}

        # Import tweets in bulk via executemany.
        for username, payload in data["data"].items():
            user_id = username_to_id.get(payload.get("email") or username)
            if user_id is None:
                continue
            tweets = payload.get("tweets", [])
            if tweets:
                for tweet in tweets:
                  tweet_id = cur.execute(
                    "INSERT INTO tweets (author_id, content, created_at) VALUES (%s, %s, %s) RETURNING id",
                    (user_id, tweet["content"], datetime.fromisoformat(tweet["timestamp"]))
                  ).fetchone()['id']
                  if tweet['likes'] > 0:
                    likes = [
                        (tweet_id, all_user_ids[user_idx]) for user_idx in range(0, min(tweet['likes'], len(all_user_ids)))
                    ]
                    cur.executemany(
                      "INSERT INTO likes (tweet_id, user_id) VALUES (%s, %s)",
                      likes,
                      )
            for followee_id in payload.get("following", []):
                cur.execute(
                    "INSERT INTO follows (follower_id, followee_id) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (user_id, followee_id),
                )
    
        # have the Viewer user follow everyone
        viewer = cur.execute("SELECT id from users WHERE handle = %s", ["Viewer"]).fetchone()
        if viewer:
          viewer_id = viewer['id']
          viewer_follows = [(viewer_id, id) for id in all_user_ids if id != viewer_id]
          cur.executemany(
            "INSERT INTO follows (follower_id, followee_id) "
            "VALUES (%s, %s)",
            viewer_follows
          )

    return singleton_response({"success": True})