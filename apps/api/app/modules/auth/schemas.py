from datetime import datetime

from pydantic import BaseModel, Field

# Self-service passwords; admins may still set shorter ones for demo accounts.
NewPassword = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class UserMe(BaseModel):
    user_id: str
    name: str
    email: str
    role: str
    balance_vnd: int
    is_active: bool
    phone: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserMe

class LogoutResponse(BaseModel):
    message: str


class ProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    # null clears the phone number.
    phone: str | None = Field(default=None, max_length=32, pattern=r"^\+?[0-9 .\-]{8,20}$")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=255)
    new_password: str = NewPassword


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class ForgotPasswordResponse(BaseModel):
    # Shown on screen; used as the "current password" in reset-password.
    temporary_password: str
    expires_at: datetime


class ResetPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    temporary_password: str = Field(min_length=1, max_length=64)
    new_password: str = NewPassword
