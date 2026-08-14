import re
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db, get_current_user, blacklist_token
from app.api.rate_limiter import rate_limiter
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


@router.post("/register")
async def register(request: Request, payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not await rate_limiter.check(f"register:{client_ip}"):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    service = UserService(db)
    try:
        return await service.register(payload.username, payload.email, payload.password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/login")
async def login(request: Request, payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not await rate_limiter.check(f"login:{client_ip}"):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    if not payload.email or not payload.password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email and password are required")
    service = UserService(db)
    return await service.login(payload.email, payload.password)


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
    authorization: str | None = Header(None),
    current_user: dict = Depends(get_current_user),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")
    token = authorization[7:]
    await blacklist_token(token)
    return {"ok": True}


@router.delete("/me")
async def delete_me(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user_id = uuid.UUID(current_user["id"])
    service = UserService(db)
    ok = await service.delete_account(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}
