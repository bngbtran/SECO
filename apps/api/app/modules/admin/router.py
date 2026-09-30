"""Web admin API under /api/v1/admin. Every route except login requires an ADMIN token."""

from fastapi import APIRouter

from app.modules.admin import auth, monitoring, stations, users

router = APIRouter(prefix="/api/v1/admin")
router.include_router(auth.router)
router.include_router(monitoring.router)
router.include_router(users.router)
router.include_router(stations.router)
