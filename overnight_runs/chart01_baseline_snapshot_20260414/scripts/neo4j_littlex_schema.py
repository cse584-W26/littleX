"""LittleX schema and walker-equivalent operations for Neo4j.

This module mirrors the Jac LittleX schema (server.jac) one-to-one in Neo4j so
that benchmarks here measure the SAME workloads run by bench_filter_pushdown.py
and bench_evaluation.py against the Jac and SQLAlchemy backends.

Schema (matches server.jac):
    Nodes:
        (:Profile {jac_id, username, bio, created_at})
        (:Tweet   {jac_id, content, author_username, created_at, likes, comments})
        (:Channel {jac_id, name, description, creator_username, created_at})
    Relationships:
        (:Profile)-[:FOLLOW]->(:Profile)        # Follow edge
        (:Profile)-[:POST]->(:Tweet)            # Post edge
        (:Profile)-[:MEMBER]->(:Channel)        # Member edge
        (:Channel)-[:CHANNEL_POST]->(:Tweet)    # ChannelPost edge

Constraints (uniqueness implies an index — this is the natural pushdown path):
    Profile.jac_id, Tweet.jac_id, Channel.jac_id are all UNIQUE.

Operations exposed mirror the Jac walkers used by the benchmark suite:
    create_user, create_tweet, follow_user, like_tweet, delete_tweet,
    load_feed, create_channel, join_channel.

These are written in idiomatic Cypher — no artificial "OFF" mode. Neo4j is
treated as a database baseline: it runs in its natural, indexed configuration.
"""

import uuid
from datetime import datetime, timezone
from neo4j import GraphDatabase


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "password"


def connect(uri=DEFAULT_URI, user=DEFAULT_USER, password=DEFAULT_PASSWORD):
    """Open a Neo4j driver. Caller owns the lifecycle (driver.close())."""
    return GraphDatabase.driver(uri, auth=(user, password))


# ---------------------------------------------------------------------------
# Schema setup
# ---------------------------------------------------------------------------

CONSTRAINTS = [
    "CREATE CONSTRAINT profile_jac_id IF NOT EXISTS FOR (p:Profile) REQUIRE p.jac_id IS UNIQUE",
    "CREATE CONSTRAINT tweet_jac_id   IF NOT EXISTS FOR (t:Tweet)   REQUIRE t.jac_id IS UNIQUE",
    "CREATE CONSTRAINT channel_jac_id IF NOT EXISTS FOR (c:Channel) REQUIRE c.jac_id IS UNIQUE",
]


def reset_database(session):
    """Wipe all nodes/relationships and reinstall constraints. Sterile env."""
    session.run("MATCH (n) DETACH DELETE n")
    for stmt in CONSTRAINTS:
        session.run(stmt)


def ensure_constraints(session):
    """Idempotent constraint setup (does not delete data)."""
    for stmt in CONSTRAINTS:
        session.run(stmt)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _new_id():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Walker-equivalent operations
# Each operation maps 1:1 to a Jac walker in server.jac and is the workload
# the benchmark times. They use parameterized Cypher with PK lookups so the
# planner naturally pushes filters into the indexed scan — exactly what a
# Neo4j developer would write by hand.
# ---------------------------------------------------------------------------

def create_user(session, username, bio="Example Bio"):
    """Equivalent of setup_profile walker. Returns the new profile's jac_id."""
    jac_id = _new_id()
    session.run(
        """
        CREATE (p:Profile {
            jac_id: $jac_id,
            username: $username,
            bio: $bio,
            created_at: $created_at
        })
        """,
        jac_id=jac_id, username=username, bio=bio, created_at=_now(),
    )
    return jac_id


def create_tweet(session, author_jac_id, content):
    """Equivalent of create_tweet walker. Returns the new tweet's jac_id.

    Single Cypher statement: indexed lookup of the author Profile by jac_id,
    then CREATE the Tweet plus the [:POST] edge in one round-trip.
    """
    jac_id = _new_id()
    session.run(
        """
        MATCH (p:Profile {jac_id: $author_jac_id})
        CREATE (p)-[:POST]->(t:Tweet {
            jac_id: $jac_id,
            content: $content,
            author_username: p.username,
            created_at: $created_at,
            likes: [],
            comments: []
        })
        """,
        author_jac_id=author_jac_id, jac_id=jac_id,
        content=content, created_at=_now(),
    )
    return jac_id


def follow_user(session, my_jac_id, target_jac_id):
    """Equivalent of follow_user walker.

    The Jac version is `[allroots()-->(?:Profile, jac_id == target_id)]` —
    a global needle-in-haystack lookup. In Neo4j, the unique constraint on
    Profile.jac_id makes this an O(1) indexed lookup. MERGE makes the edge
    creation idempotent (matches Jac semantics — re-following is a no-op).
    """
    session.run(
        """
        MATCH (me:Profile     {jac_id: $my_jac_id})
        MATCH (target:Profile {jac_id: $target_jac_id})
        MERGE (me)-[:FOLLOW]->(target)
        """,
        my_jac_id=my_jac_id, target_jac_id=target_jac_id,
    )


def like_tweet(session, my_jac_id, tweet_jac_id):
    """Equivalent of like_tweet walker.

    The Jac version calls find_tweet (a 2-hop lookup
    `[allroots()-->(?:Profile)-->(?:Tweet, jac_id == X)]`) then toggles the
    user's name in the tweet's likes list. In Neo4j the indexed Tweet.jac_id
    lookup is O(1) directly — no traversal needed.
    """
    result = session.run(
        """
        MATCH (me:Profile {jac_id: $my_jac_id})
        MATCH (t:Tweet    {jac_id: $tweet_jac_id})
        SET t.likes = CASE
            WHEN me.username IN t.likes
                THEN [u IN t.likes WHERE u <> me.username]
            ELSE t.likes + me.username
        END
        RETURN t.likes AS likes
        """,
        my_jac_id=my_jac_id, tweet_jac_id=tweet_jac_id,
    )
    record = result.single()
    return record["likes"] if record else None


def delete_tweet(session, my_jac_id, tweet_jac_id):
    """Equivalent of delete_tweet walker.

    The Jac version is `[profile-->(?:Tweet, jac_id == X)]` — a one-hop
    filtered lookup that requires the tweet to be owned by the caller. We
    encode that as a MATCH with both endpoints constrained, ensuring users
    can't delete other people's tweets (same semantics as the walker).
    """
    result = session.run(
        """
        MATCH (me:Profile {jac_id: $my_jac_id})-[:POST]->(t:Tweet {jac_id: $tweet_jac_id})
        DETACH DELETE t
        RETURN count(t) AS deleted
        """,
        my_jac_id=my_jac_id, tweet_jac_id=tweet_jac_id,
    )
    record = result.single()
    return (record["deleted"] if record else 0) > 0


def load_feed(session, my_jac_id):
    """Equivalent of load_feed walker.

    Returns my own tweets plus tweets from profiles I follow. Cypher 5 is
    strict about mixing non-aggregating expressions with aggregations in the
    same WITH, so we collect followed-author tweets in their own WITH clause
    and only union with my_tweets afterward.
    """
    result = session.run(
        """
        MATCH (me:Profile {jac_id: $my_jac_id})
        OPTIONAL MATCH (me)-[:POST]->(myt:Tweet)
        WITH me, collect(DISTINCT myt) AS my_tweets
        OPTIONAL MATCH (me)-[:FOLLOW]->(:Profile)-[:POST]->(ft:Tweet)
        WITH my_tweets, collect(DISTINCT ft) AS followed_tweets
        WITH my_tweets + followed_tweets AS feed
        UNWIND feed AS t
        WITH t WHERE t IS NOT NULL
        RETURN t.jac_id AS id, t.content AS content,
               t.author_username AS author_username,
               t.created_at AS created_at,
               t.likes AS likes, t.comments AS comments
        ORDER BY t.created_at DESC
        """,
        my_jac_id=my_jac_id,
    )
    return [dict(r) for r in result]


def get_profile(session, jac_id):
    """Equivalent of get_profile walker.

    Returns the Profile plus aggregate counts (followers, following, tweets).
    Single Cypher round-trip — uses indexed Profile lookup then OPTIONAL MATCH
    fan-outs that the planner unifies into one query plan.
    """
    result = session.run(
        """
        MATCH (p:Profile {jac_id: $jac_id})
        OPTIONAL MATCH (p)<-[:FOLLOW]-(follower:Profile)
        WITH p, count(DISTINCT follower) AS followers
        OPTIONAL MATCH (p)-[:FOLLOW]->(following:Profile)
        WITH p, followers, count(DISTINCT following) AS following
        OPTIONAL MATCH (p)-[:POST]->(t:Tweet)
        RETURN p.jac_id AS id,
               p.username AS username,
               p.bio AS bio,
               p.created_at AS created_at,
               followers,
               following,
               count(DISTINCT t) AS tweet_count
        """,
        jac_id=jac_id,
    )
    record = result.single()
    return dict(record) if record else None


def load_feed_with_search(session, my_jac_id, search_term=""):
    """Equivalent of load_feed walker with the optional search_query filter.

    The Jac walker concatenates my own tweets + tweets from followed Profiles
    and then post-filters in Python. Here we push the substring filter into
    Cypher (toLower CONTAINS toLower) so it executes inside Neo4j and only
    matching nodes leave the engine. Empty search_term returns the full feed.

    Note: parameters are passed as a positional dict (not kwargs) so the
    Cypher parameter name $search can sit alongside session.run's own
    `query` argument without colliding.
    """
    cypher = """
        MATCH (me:Profile {jac_id: $my_jac_id})
        OPTIONAL MATCH (me)-[:POST]->(myt:Tweet)
        WITH me, collect(DISTINCT myt) AS my_tweets
        OPTIONAL MATCH (me)-[:FOLLOW]->(:Profile)-[:POST]->(ft:Tweet)
        WITH my_tweets, collect(DISTINCT ft) AS followed_tweets
        WITH my_tweets + followed_tweets AS feed
        UNWIND feed AS t
        WITH t
        WHERE t IS NOT NULL
          AND ($search = '' OR toLower(t.content) CONTAINS toLower($search))
        RETURN t.jac_id AS id,
               t.content AS content,
               t.author_username AS author_username,
               t.created_at AS created_at,
               t.likes AS likes,
               t.comments AS comments
        ORDER BY t.created_at DESC
    """
    result = session.run(cypher, {"my_jac_id": my_jac_id, "search": search_term})
    return [dict(r) for r in result]


def create_channel(session, creator_jac_id, name, description=""):
    """Equivalent of create_channel walker."""
    jac_id = _new_id()
    session.run(
        """
        MATCH (p:Profile {jac_id: $creator_jac_id})
        CREATE (p)-[:MEMBER]->(c:Channel {
            jac_id: $jac_id,
            name: $name,
            description: $description,
            creator_username: p.username,
            created_at: $created_at
        })
        """,
        creator_jac_id=creator_jac_id, jac_id=jac_id,
        name=name, description=description, created_at=_now(),
    )
    return jac_id
