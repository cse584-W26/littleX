"""User auth endpoints.

Register / login are thin no-ops: we use bearer-username auth (same as PG
hand-tuned). The bench driver sends Authorization: Bearer <username> and
the walker routes resolve it to a Profile by jac_id.

Register creates an empty placeholder Profile so setup_profile can UPDATE
rather than CREATE on first call — keeps write paths idempotent.
"""

from fastapi import APIRouter
from pydantic import BaseModel

import src


router = APIRouter()


class Credentials(BaseModel):
    username: str
    password: str = ""


@router.post("/register")
def register(creds: Credentials):
    # The username is treated as the Profile jac_id directly. No password.
    with src.get_driver().session() as s:
        s.run(
            """
            MERGE (p:Profile {jac_id: $jac_id})
              ON CREATE SET p.username = $jac_id, p.bio = '', p.created_at = $now
            """,
            jac_id=creds.username,
            now=src.now_iso(),
        )
    return src.build_response({"success": True})


@router.post("/login")
def login(creds: Credentials):
    # No-op — bearer scheme is Authorization: Bearer <username>.
    return {"token": creds.username}
