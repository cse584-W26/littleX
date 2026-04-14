"""LittleX walker-equivalent endpoints backed by Cypher.

Mirrors the endpoint surface of littleX_postgres_handtuned/src/routes/walker.py
so the same bench driver hits either service by swapping --url. All queries
are parameterized Cypher using the unique Profile.jac_id index — the
planner pushes filters into the indexed scan.
"""

import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import src


router = APIRouter()


def _auth(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not Logged In")
    return authorization[len("Bearer "):]


class SetupProfileBody(BaseModel):
    username: str = ""
    bio: str = ""


@router.post("/setup_profile")
def setup_profile(body: SetupProfileBody, authorization: Optional[str] = Header(None)):
    jac_id = _auth(authorization)
    with src.get_driver().session() as s:
        row = s.run(
            """
            MERGE (p:Profile {jac_id: $jac_id})
              ON CREATE SET p.created_at = $now
            SET p.username = CASE WHEN $username <> '' THEN $username ELSE coalesce(p.username, $jac_id) END,
                p.bio      = CASE WHEN $bio      <> '' THEN $bio      ELSE coalesce(p.bio, '') END
            RETURN p.jac_id AS id, p.username AS username, p.bio AS bio, p.created_at AS created_at
            """,
            jac_id=jac_id, username=body.username, bio=body.bio, now=src.now_iso(),
        ).single()
    return src.build_response(dict(row))


class CreateTweetBody(BaseModel):
    content: str


@router.post("/create_tweet")
def create_tweet(body: CreateTweetBody, authorization: Optional[str] = Header(None)):
    jac_id = _auth(authorization)
    tweet_id = src.new_id()
    with src.get_driver().session() as s:
        row = s.run(
            """
            MATCH (p:Profile {jac_id: $author_id})
            CREATE (t:Tweet {
                jac_id: $tweet_id,
                content: $content,
                author_username: p.username,
                created_at: $now,
                likes: [],
                comments: []
            })
            CREATE (p)-[:POST]->(t)
            RETURN t.jac_id AS id, t.content AS content,
                   t.author_username AS author_username, t.created_at AS created_at
            """,
            author_id=jac_id, tweet_id=tweet_id, content=body.content, now=src.now_iso(),
        ).single()
    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    d = dict(row)
    d["likes"] = []
    d["comments"] = []
    return src.build_response(d)


class CreateChannelBody(BaseModel):
    name: str
    description: str = ""


@router.post("/create_channel")
def create_channel(body: CreateChannelBody, authorization: Optional[str] = Header(None)):
    jac_id = _auth(authorization)
    channel_id = src.new_id()
    with src.get_driver().session() as s:
        row = s.run(
            """
            MATCH (p:Profile {jac_id: $creator_id})
            CREATE (c:Channel {
                jac_id: $channel_id,
                name: $name,
                description: $description,
                creator_username: p.username,
                created_at: $now
            })
            CREATE (p)-[:MEMBER]->(c)
            RETURN c.jac_id AS id, c.name AS name, c.description AS description,
                   c.creator_username AS creator_username, c.created_at AS created_at
            """,
            creator_id=jac_id, channel_id=channel_id,
            name=body.name, description=body.description, now=src.now_iso(),
        ).single()
    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    d = dict(row)
    d["is_member"] = True
    return src.build_response(d)


@router.api_route("/load_own_tweets", methods=["GET", "POST"])
def load_own_tweets(authorization: Optional[str] = Header(None)):
    """Return the authenticated user's tweets.

    Aggregates the tweet rows inside the Cypher engine via `collect(t{...})`
    so the driver decodes ONE list-of-maps value rather than N rows × 6
    fields. This is Cypher's closest analogue to Postgres' json_agg and
    represents the most honest Neo4j floor — we shouldn't penalize the
    engine for a wrapper choice that pays per-row Bolt deserialization.
    """
    jac_id = _auth(authorization)
    with src.get_driver().session() as s:
        t0 = time.perf_counter()
        rec = s.run(
            """
            MATCH (p:Profile {jac_id: $jac_id})-[:POST]->(t:Tweet)
            WITH t ORDER BY t.created_at DESC
            RETURN collect(t {
                id: t.jac_id,
                content: t.content,
                author_username: t.author_username,
                created_at: t.created_at,
                likes: coalesce(t.likes, []),
                comments: coalesce(t.comments, [])
            }) AS tweets
            """,
            jac_id=jac_id,
        ).single()
        ms_traversal = (time.perf_counter() - t0) * 1000
    tweets = rec["tweets"] if rec and "tweets" in rec else []
    payload = {
        "tweets": tweets,
        "ms_traversal": round(ms_traversal, 4),
        "ms_build_payload": 0.0,
    }
    return src.build_response(payload, reports=[payload])


@router.post("/clear_data")
def clear_data():
    with src.get_driver().session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    return src.build_response({"success": True, "message": "Database reset"})
