from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_access_token
from app.db.mongodb import get_collection

bearer = HTTPBearer(auto_error=True)
users = get_collection("users")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def user_from_token(token: str) -> dict:
    """Resolve a JWT to its user, rejecting expired, revoked or orphaned tokens."""
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid or expired token") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise _unauthorized("Invalid or expired token")

    user = await users.find_one(
        {"user_id": user_id},
        {"_id": 0, "password_hash": 0},
    )
    if not user:
        raise _unauthorized("User not found")

    if int(payload.get("ver", 0)) != int(user.get("token_version", 0)):
        raise _unauthorized("Token has been revoked")

    return user


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
):
    user = await user_from_token(credentials.credentials)
    if not user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )
    return user


CurrentUser = Annotated[dict, Depends(get_current_user)]


async def require_role(required_role: str, user):
    if user.get("role") != required_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{required_role} only",
        )
    return user


async def require_admin(user: CurrentUser):
    return await require_role("ADMIN", user)


async def require_technician(user: CurrentUser):
    return await require_role("TECHNICIAN", user)


async def require_member(user: CurrentUser):
    return await require_role("USER", user)


CurrentAdmin = Annotated[dict, Depends(require_admin)]
CurrentTechnician = Annotated[dict, Depends(require_technician)]
CurrentMember = Annotated[dict, Depends(require_member)]
