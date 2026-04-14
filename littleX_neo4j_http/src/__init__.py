"""LittleX Neo4j-over-HTTP baseline.

Thin FastAPI wrapper around the Neo4j Bolt driver so the benchmark driver
talks to Neo4j through the same HTTP + JSON stack as the PG hand-tuned
and SQLAlchemy baselines. This eliminates the protocol confound when
comparing engines — every baseline now looks like:

    bench host -> kubectl port-forward -> ClusterIP svc -> python web pod
                                                                  |
                                                                  v
                                                             DB pod (Bolt / psycopg)

Auth scheme: Authorization: Bearer <username> (same as PG hand-tuned).
The username is the Profile's jac_id in Neo4j, so a simple bearer string
is enough — no JWT, no password, matching the other Flask baselines.

Schema matches littleX-benchmarks/Neo4j/neo4j_littlex_schema.py:
    (:Profile {jac_id, username, bio, created_at})
    (:Tweet   {jac_id, content, author_username, created_at, likes, comments})
    (:Channel {jac_id, name, description, creator_username, created_at})
    (:Profile)-[:POST]->(:Tweet), (:Profile)-[:MEMBER]->(:Channel),
    (:Profile)-[:FOLLOW]->(:Profile)
"""

import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from neo4j import GraphDatabase

from src.routes import bp as router


NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_AUTH = None if not NEO4J_PASSWORD else (NEO4J_USER, NEO4J_PASSWORD)


app = FastAPI(title="LittleX Neo4j-HTTP baseline")


_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        with _driver.session() as s:
            for stmt in (
                "CREATE CONSTRAINT profile_jac_id IF NOT EXISTS FOR (p:Profile) REQUIRE p.jac_id IS UNIQUE",
                "CREATE CONSTRAINT tweet_jac_id   IF NOT EXISTS FOR (t:Tweet)   REQUIRE t.jac_id IS UNIQUE",
                "CREATE CONSTRAINT channel_jac_id IF NOT EXISTS FOR (c:Channel) REQUIRE c.jac_id IS UNIQUE",
            ):
                s.run(stmt)
    return _driver


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return str(uuid.uuid4())


def build_response(result, reports=None):
    return {"data": {"result": result, "reports": reports if reports is not None else [result]}}


app.include_router(router)
