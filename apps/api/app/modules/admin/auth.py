"""UC-A01: admin sign-in for the web admin."""

from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import CurrentAdmin
from app.modules.admin.schemas import AdminLoginResponse
from app.modules.auth.router import authenticate, login_response, revoke_tokens
from app.modules.auth.schemas import LoginRequest, LogoutResponse, UserMe
from app.modules.logs.service import record_audit

router = APIRouter(prefix="/auth", tags=["Admin · Auth"])


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(request: LoginRequest):
    user = await authenticate(request.email, request.password)

    if user["role"] != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin account required",
        )

    await record_audit(actor=user, action="ADMIN_LOGIN", target_type="user", target_id=user["user_id"])
    return login_response(user)


@router.get("/me", response_model=UserMe)
async def admin_me(admin: CurrentAdmin):
    return UserMe(**admin)


@router.post("/logout", response_model=LogoutResponse)
async def admin_logout(admin: CurrentAdmin):
    await revoke_tokens(admin["user_id"])
    await record_audit(
        actor=admin,
        action="ADMIN_LOGOUT",
        target_type="user",
        target_id=admin["user_id"],
    )
    return LogoutResponse(message="Logged out successfully")
