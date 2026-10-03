from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import CurrentUser
from app.core.exceptions import BusinessRuleError, ResourceNotFoundError
from app.core.security import create_access_token, verify_password
from app.db.mongodb import get_collection
from app.modules.auth import account
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    ProfileUpdateRequest,
    ResetPasswordRequest,
    UserMe,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
users = get_collection("users")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def authenticate(email: str, password: str) -> dict:
    """Return the active user with these credentials, or raise 401."""
    user = await users.find_one({"email": _normalize_email(email)})

    if not user or not user.get("is_active", False):
        raise _invalid_credentials()

    if not verify_password(password, user["password_hash"]):
        raise _invalid_credentials()

    return user


def login_response(user: dict) -> LoginResponse:
    return LoginResponse(
        access_token=create_access_token(
            user["user_id"],
            user["role"],
            int(user.get("token_version", 0)),
        ),
        user=UserMe(
            user_id=user["user_id"],
            name=user["name"],
            email=user["email"],
            role=user["role"],
            balance_vnd=user["balance_vnd"],
            is_active=user["is_active"],
            phone=user.get("phone"),
        ),
    )


async def revoke_tokens(user_id: str) -> None:
    # Stateless JWTs cannot be deleted, so revoke them by bumping the version.
    # This signs the user out on every device.
    await users.update_one(
        {"user_id": user_id},
        {
            "$inc": {"token_version": 1},
            "$set": {"updated_at": datetime.now(UTC)},
        },
    )


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    return login_response(await authenticate(request.email, request.password))


@router.get("/me", response_model=UserMe)
async def me(user: CurrentUser):
    return UserMe(**user)


@router.patch("/me", response_model=UserMe)
async def update_me(request: ProfileUpdateRequest, user: CurrentUser):
    """Edit your own name / phone (every role)."""
    try:
        return UserMe(**await account.update_profile(user, request.model_dump(exclude_unset=True)))
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/logout", response_model=LogoutResponse)
async def logout(user: CurrentUser):
    await revoke_tokens(user["user_id"])
    return LogoutResponse(message="Logged out successfully")


@router.post("/change-password", response_model=LoginResponse)
async def change_password(request: ChangePasswordRequest, user: CurrentUser):
    """Every other device is signed out; this one gets a fresh token in the response."""
    try:
        updated = await account.change_password(user, request.current_password, request.new_password)
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return login_response(updated)


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(request: ForgotPasswordRequest):
    """Members and technicians: get a temporary password to use in reset-password.

    The current password keeps working until the reset is completed.
    """
    try:
        return await account.request_password_reset(request.email)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


@router.post("/reset-password", response_model=LoginResponse)
async def reset_password(request: ResetPasswordRequest):
    """Change password with the temporary password as the current one; signs you in."""
    try:
        updated = await account.reset_password(
            request.email, request.temporary_password, request.new_password
        )
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return login_response(updated)
