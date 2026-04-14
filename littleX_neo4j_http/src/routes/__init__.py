"""Route module — FastAPI router aggregating user + walker endpoints."""
from fastapi import APIRouter
from src.routes.user import router as user_router
from src.routes.walker import router as walker_router

bp = APIRouter()
bp.include_router(user_router, prefix="/user")
# Match the Flask baselines (PG hand-tuned, SQLAlchemy pure): walker endpoints
# mounted under both /walker and /function so the bench driver hits a
# consistent path across backends.
bp.include_router(walker_router, prefix="/walker")
bp.include_router(walker_router, prefix="/function")
