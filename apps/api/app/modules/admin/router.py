"""Web admin API under /api/v1/admin. Every route except login requires an ADMIN token."""

from fastapi import APIRouter

from app.modules.admin import auth, card_registration, monitoring, stations, support, users

router = APIRouter(prefix="/api/v1/admin")
router.include_router(auth.router)
router.include_router(monitoring.router)
router.include_router(users.router)
router.include_router(stations.router)
router.include_router(card_registration.router)
router.include_router(support.router)
