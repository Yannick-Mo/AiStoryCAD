import re
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_current_user, blacklist_token
from app.api.rate_limiter import rate_limiter
from app.config import settings
from app.user.service import UserService


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        v = v.strip()
        if len(v) < 3 or len(v) > 20:
            raise ValueError("Username must be between 3 and 20 characters")
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError("Username must contain only letters, numbers, and underscores")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("Password must not exceed 128 characters")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v):
        return v.strip().lower()


class UpdateProfileRequest(BaseModel):
    display_name: str | None = None
    password: str | None = None
    old_password: str | None = None

    @field_validator("password")
    @classmethod
    def validate_new_password(cls, v):
        if v is None:
            return v
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("Password must not exceed 128 characters")
        return v


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_auth_cookie(response: Response, token: str) -> None:
    """httpOnly JWT cookie(前端为 http://localhost:5173,无需 Secure)。"""
    max_age = int(settings.jwt_expire_hours or 24) * 3600
    response.set_cookie(
        key="storycad_token",
        value=token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
    )


@router.post("/register")
async def register(request: Request, payload: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not await rate_limiter.check(f"register:{payload.email}:{client_ip}"):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    service = UserService(db)
    try:
        result = await service.register(payload.username, payload.email, payload.password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    _set_auth_cookie(response, result["token"])
    return result


@router.post("/login")
async def login(request: Request, payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not await rate_limiter.check(f"login:{payload.email}:{client_ip}"):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    # 账号维度锁定:15 分钟内尝试次数达 10 次即临时锁定该账号(成功后重置)
    if not await rate_limiter.check(f"login_lock:{payload.email}", max_attempts=10, window=900):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many failed login attempts. Please try again later.")
    if not payload.email or not payload.password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email and password are required")
    service = UserService(db)
    result = await service.login(payload.email, payload.password)
    await rate_limiter.reset(f"login_lock:{payload.email}")
    _set_auth_cookie(response, result["token"])
    return result


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return current_user


@router.patch("/me")
async def update_me(payload: UpdateProfileRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user_id = current_user["id"]
    service = UserService(db)
    if payload.password is not None:
        if not payload.old_password:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Old password is required")
        return await service.update_password(user_id, payload.old_password, payload.password)
    return await service.update_profile(user_id, display_name=payload.display_name)


@router.post("/logout")
async def logout(
    request: Request,
    authorization: str | None = Header(None),
    current_user: dict = Depends(get_current_user),
    response: Response = None,
):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token:
        token = request.cookies.get("storycad_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")
    await blacklist_token(token)
    response.delete_cookie("storycad_token", path="/")
    return {"ok": True}


@router.delete("/me")
async def delete_me(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user_id = uuid.UUID(current_user["id"])
    service = UserService(db)
    ok = await service.delete_account(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}
