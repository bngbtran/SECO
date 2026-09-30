from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import CurrentUser
from app.core.security import create_access_token, verify_password
from app.db.mongodb import get_collection
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    UserMe,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
users = get_collection("users")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    email = _normalize_email(request.email)
    user = await users.find_one({"email": email})

    if not user or not user.get("is_active", False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(request.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    public_user = UserMe(
        user_id=user["user_id"],
        name=user["name"],
        email=user["email"],
        role=user["role"],
        balance_vnd=user["balance_vnd"],
        is_active=user["is_active"],
    )

    return LoginResponse(
        access_token=create_access_token(
            user["user_id"],
            user["role"],
            int(user.get("token_version", 0)),
        ),
        user=public_user,
    )


@router.get("/me", response_model=UserMe)
async def me(user: CurrentUser):
    return UserMe(**user)

@router.post("/logout", response_model=LogoutResponse)
async def logout(user: CurrentUser):
    # Stateless JWTs cannot be deleted, so revoke them by bumping the version.
    # This signs the user out on every device.
    await users.update_one(
        {"user_id": user["user_id"]},
        {
            "$inc": {"token_version": 1},
            "$set": {"updated_at": datetime.now(UTC)},
        },
    )
    return LogoutResponse(message="Logged out successfully")
