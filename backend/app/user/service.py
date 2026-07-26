import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
import bcrypt
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.user.repository import UserRepository
from app.config import settings


class UserService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)

    @staticmethod
    async def _hash_password(password: str) -> str:
        return (await asyncio.to_thread(bcrypt.hashpw, password.encode(), bcrypt.gensalt())).decode()

    @staticmethod
    async def _verify_password(plain: str, hashed: str) -> bool:
        return await asyncio.to_thread(bcrypt.checkpw, plain.encode(), hashed.encode())

    @staticmethod
    def _create_token(user_id: uuid.UUID) -> str:
        expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
        payload = {"sub": str(user_id), "exp": expire}
        return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")

    @staticmethod
    def _decode_token(token: str) -> uuid.UUID:
        try:
            payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"], options={"verify_exp": True})
            return uuid.UUID(payload["sub"])
        except (jwt.PyJWTError, KeyError, ValueError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    async def register(self, username: str, email: str, password: str) -> dict:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        if await self.repo.get_by_email(email):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
        if await self.repo.get_by_username(username):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
        user = await self.repo.create(username, email, await self._hash_password(password))
        token = self._create_token(user.id)
        return {"token": token, "user": {"id": str(user.id), "username": user.username, "email": user.email, "display_name": user.display_name}}

    async def login(self, email: str, password: str) -> dict:
        user = await self.repo.get_by_email(email)
        if not user or not await self._verify_password(password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
        token = self._create_token(user.id)
        return {"token": token, "user": {"id": str(user.id), "username": user.username, "email": user.email, "display_name": user.display_name}}

    async def get_current_user(self, token: str) -> dict:
        user_id = self._decode_token(token)
        user = await self.repo.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return {"id": str(user.id), "username": user.username, "email": user.email, "display_name": user.display_name}

    async def delete_account(self, user_id: uuid.UUID) -> bool:
        return await self.repo.delete(user_id)

    async def update_profile(self, user_id: uuid.UUID, display_name: Optional[str] = None, password: Optional[str] = None) -> dict:
        updates = {}
        if display_name is not None:
            updates["display_name"] = display_name
        if password is not None:
            updates["password_hash"] = await self._hash_password(password)
        ok = await self.repo.update(user_id, **updates)
        if not ok:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        user = await self.repo.get_by_id(user_id)
        return {"id": str(user.id), "username": user.username, "email": user.email, "display_name": user.display_name}
