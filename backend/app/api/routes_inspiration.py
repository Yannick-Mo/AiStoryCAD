from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from app.api.deps import get_current_user
from app.api.rate_limiter import rate_limiter
from app.agent.inspiration.generator import InspirationGenerator
from app.agent.inspiration.challenges import get_challenges, get_random_challenge

router = APIRouter(prefix="/api/inspiration", tags=["Inspiration"])


class StarterRequest(BaseModel):
    genre: str
    style: str = ""
    constraints: list[str] | None = None


class BatchRequest(BaseModel):
    genres: list[str]
    count: int = 3

    @field_validator("count")
    @classmethod
    def count_in_range(cls, v):
        if v < 1 or v > 5:
            raise ValueError("count must be between 1 and 5")
        return v

    @field_validator("genres")
    @classmethod
    def genres_limit(cls, v):
        if not v:
            raise ValueError("genres must not be empty")
        if len(v) > 5:
            raise ValueError("genres must not exceed 5 items")
        return v


@router.post("/starter")
async def story_starter(
    req: StarterRequest,
    user: dict = Depends(get_current_user),
):
    if not await rate_limiter.check(f"inspiration:{user['id']}", max_attempts=30, window=60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    try:
        gen = InspirationGenerator()
        result = await gen.generate_story_starter(req.genre, req.style, req.constraints)
        if result is None:
            raise HTTPException(status_code=500, detail="生成故事起点失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成故事起点失败: {str(e)}")


@router.post("/batch")
async def batch_starters(
    req: BatchRequest,
    user: dict = Depends(get_current_user),
):
    if not await rate_limiter.check(f"inspiration:{user['id']}", max_attempts=30, window=60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    try:
        gen = InspirationGenerator()
        return await gen.batch_generate(req.genres, req.count)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量生成失败: {str(e)}")


@router.get("/challenges")
async def list_challenges(
    difficulty: str | None = Query(None),
    genre: str | None = Query(None),
    user: dict = Depends(get_current_user),
):
    try:
        return get_challenges(difficulty, genre)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取挑战列表失败: {str(e)}")


@router.get("/challenges/random")
async def random_challenge(
    user: dict = Depends(get_current_user),
):
    try:
        return get_random_challenge()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取随机挑战失败: {str(e)}")
