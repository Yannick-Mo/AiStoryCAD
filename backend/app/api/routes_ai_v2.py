from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, field_validator
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context_compressor import async_compress_context, estimate_tokens
from app.agent.memory.conversation import ConversationMemory
from app.agent.memory.models import Conversation
from app.agent.privacy import sanitise_event
from app.agent.super_agent import SuperAgent
from app.agent.tools.base import verify_project_owner as _verify_tool_owner
from app.api.deps import get_db, get_current_user, get_redis
from app.api.rate_limiter import rate_limiter
from app.llm.client import get_shared_client, get_tracker

router = APIRouter(prefix="/api/v2", tags=["AI v2"])

SSE_PING_INTERVAL = 15


async def _verify_project_owner(db: AsyncSession, project_id: uuid.UUID, user: dict) -> None:
    try:
        await _verify_tool_owner(db, project_id, user["id"])
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not authorized to access this project")


async def _verify_conversation_owner(db: AsyncSession, conversation_id: str, user: dict) -> None:
    """A conversation must belong to the current user, otherwise 404 (IDOR guard)."""
    owner = await ConversationMemory(db).get_conversation_owner_id(conversation_id)
    if owner is None or owner != str(user["id"]):
        raise HTTPException(status_code=404, detail="Conversation not found")


def _format_sse(event: str, data: str) -> str:
    """Format an SSE event, properly encoding newlines in data.

    SSE requires multi-line data to be split across multiple 'data:' lines.
    Single-line encoding breaks when data contains \\n characters.
    """
    lines = data.split('\n')
    out = f"event: {event}\n"
    for line in lines:
        out += f"data: {line}\n"
    out += "\n"
    return out


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    mode: str = "chat"
    context_view: str | None = None
    context_id: str | None = None

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be empty")
        return v


class NewChatRequest(BaseModel):
    title: str = ""


async def _stream_chat(
    project_id: str,
    user_id: str,
    message: str,
    conv_id: str | None,
    mode: str = "chat",
    context_view: str | None = None,
    context_id: str | None = None,
    agent: SuperAgent | None = None,
    request: Request | None = None,
) -> AsyncGenerator[str, None]:
    yield "retry: 3000\n\n"

    from app.database import async_session

    async with async_session() as session:
        agent.db = session

        queue: asyncio.Queue = asyncio.Queue(maxsize=128)

        async def _safe_put(item: tuple) -> None:
            """客户端断开后队列可能被填满导致 put 永久阻塞,加超时兜底。"""
            try:
                await asyncio.wait_for(queue.put(item), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("SSE queue full; dropping event")
            except asyncio.CancelledError:
                raise

        async def _run_chat():
            try:
                async for event in agent.chat_stream(project_id, user_id, message, conv_id, mode=mode, context_view=context_view, context_id=context_id):
                    await _safe_put(("event", event))
            except BaseException as exc:
                await _safe_put(("error", exc))
            finally:
                await _safe_put(("done", None))

        chat_task = asyncio.create_task(_run_chat())

        try:
            while True:
                if request is not None and await request.is_disconnected():
                    break
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=SSE_PING_INTERVAL)
                except asyncio.TimeoutError:
                    if request is not None and await request.is_disconnected():
                        break
                    yield "event: ping\ndata: {}\n\n"
                    continue

                if kind == "done":
                    break
                if kind == "error":
                    raise payload
                if kind == "event":
                    if request is not None and await request.is_disconnected():
                        break
                    safe_data = sanitise_event(payload['type'], payload['data'])
                    yield _format_sse(payload['type'], safe_data)
        except BaseException as exc:
            logger.error("AI chat error: {}", exc, exc_info=True)
            yield f"event: error\ndata: {json.dumps({'message': 'Internal error', 'detail': 'An unexpected error occurred'})}\n\n"
        finally:
            chat_task.cancel()


@router.post("/projects/{project_id}/chat")
async def chat(
    project_id: uuid.UUID,
    req: ChatRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis | None = Depends(get_redis),
):
    await _verify_project_owner(db, project_id, user)
    if not await rate_limiter.check(f"ai_v2_chat:{user['id']}", max_attempts=30, window=60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    if req.conversation_id:
        await _verify_conversation_owner(db, req.conversation_id, user)
    llm_client = get_shared_client().fork()
    agent = SuperAgent(db=db, redis_client=redis_client, llm_client=llm_client)
    return StreamingResponse(
        _stream_chat(str(project_id), user["id"], req.message, req.conversation_id, req.mode, context_view=req.context_view, context_id=req.context_id, agent=agent, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class CompressRequest(BaseModel):
    conversation_id: str


@router.post("/projects/{project_id}/chat/compress")
async def compress_context(
    project_id: uuid.UUID,
    req: CompressRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis | None = Depends(get_redis),
):
    await _verify_project_owner(db, project_id, user)
    await _verify_conversation_owner(db, req.conversation_id, user)
    if not await rate_limiter.check(f"ai_v2_compress:{user['id']}", max_attempts=30, window=60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
    llm_client = get_shared_client().fork()
    agent = SuperAgent(db=db, redis_client=redis_client, llm_client=llm_client)
    history = await agent.conv_memory.get_history(req.conversation_id, user["id"])
    if not history:
        return {"compressed": False, "detail": "对话历史为空"}

    before_count = len(history)
    before_tokens = estimate_tokens(history)

    from app.agent.context_compressor import (
        async_compress_context,
        build_boundary_message,
        _LIGHTER_SUMMARY_PROMPT,
    )

    # Percentage-based head/tail: 20% head + 30% tail retained, middle 50% summarized
    total = len(history)
    head_count = max(2, int(total * 0.20))
    tail_count = max(3, int(total * 0.30))

    # Button always compresses (threshold=0.0), lighter parameters
    compressed = await async_compress_context(
        history,
        llm_client.chat,
        threshold=0.0,
        head_count=head_count,
        tail_count=tail_count,
        summary_prompt=_LIGHTER_SUMMARY_PROMPT,
    )

    after_count = len(compressed)
    after_tokens = estimate_tokens(compressed)
    saved_pct = round((1 - after_tokens / max(before_tokens, 1)) * 100)

    # Only persist if compression actually reduced anything
    if after_count >= before_count:
        return {
            "compressed": False,
            "before": {"messages": before_count, "tokens": before_tokens},
            "after": {"messages": after_count, "tokens": after_tokens},
            "saved_percent": 0,
        }

    await agent.conv_memory.replace_history(req.conversation_id, compressed, user["id"])
    boundary = build_boundary_message(before_count, after_count)
    await agent.conv_memory.save_message(req.conversation_id, boundary, user["id"])

    return {
        "compressed": True,
        "before": {"messages": before_count, "tokens": before_tokens},
        "after": {"messages": after_count, "tokens": after_tokens},
        "saved_percent": saved_pct,
    }


@router.post("/projects/{project_id}/conversations")
async def new_conversation(
    project_id: uuid.UUID,
    req: NewChatRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis | None = Depends(get_redis),
):
    await _verify_project_owner(db, project_id, user)
    llm_client = get_shared_client()
    agent = SuperAgent(db=db, redis_client=redis_client, llm_client=llm_client)
    conv = await agent.create_conversation(str(project_id), user["id"], req.title)
    return {"conversation_id": conv, "project_id": str(project_id), "title": req.title}


@router.get("/projects/{project_id}/conversations")
async def list_conversations(
    project_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis | None = Depends(get_redis),
):
    await _verify_project_owner(db, project_id, user)
    llm_client = get_shared_client()
    agent = SuperAgent(db=db, redis_client=redis_client, llm_client=llm_client)
    convs = await agent.list_conversations(str(project_id), user["id"])
    return {"conversations": convs}


@router.get("/projects/{project_id}/conversations/{conv_id}")
async def get_conversation(
    project_id: uuid.UUID,
    conv_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis | None = Depends(get_redis),
):
    await _verify_project_owner(db, project_id, user)
    llm_client = get_shared_client()
    agent = SuperAgent(db=db, redis_client=redis_client, llm_client=llm_client)
    conv = await agent.get_conversation(str(project_id), user["id"], conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


class RenameRequest(BaseModel):
    title: str


@router.patch("/projects/{project_id}/conversations/{conv_id}")
async def rename_conversation(
    project_id: uuid.UUID,
    conv_id: str,
    req: RenameRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis | None = Depends(get_redis),
):
    await _verify_project_owner(db, project_id, user)
    await _verify_conversation_owner(db, conv_id, user)
    llm_client = get_shared_client()
    agent = SuperAgent(db=db, redis_client=redis_client, llm_client=llm_client)
    await agent.conv_memory.rename_conversation(conv_id, req.title, user["id"])
    return {"ok": True}


@router.delete("/projects/{project_id}/conversations/{conv_id}")
async def delete_conversation(
    project_id: uuid.UUID,
    conv_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis | None = Depends(get_redis),
):
    await _verify_project_owner(db, project_id, user)
    llm_client = get_shared_client()
    agent = SuperAgent(db=db, redis_client=redis_client, llm_client=llm_client)
    ok = await agent.delete_conversation(str(project_id), user["id"], conv_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@router.get("/usage")
async def get_usage(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return token usage for the current user's own conversations.

    The tracker records sessions (conversation ids); we aggregate only over
    this user's conversations so no cross-user/global usage is leaked.
    """
    result = await db.execute(
        select(Conversation.id).where(Conversation.user_id == uuid.UUID(user["id"]))
    )
    conv_ids = [str(cid) for cid in result.scalars().all()]
    prompt = completion = total = 0
    cost = 0.0
    for cid in conv_ids:
        agg = get_tracker().get_session_total(cid)
        prompt += agg["prompt_tokens"]
        completion += agg["completion_tokens"]
        total += agg["total_tokens"]
        cost += agg["cost"]
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cost": round(cost, 6),
    }


@router.get("/usage/session/{session_id}")
async def get_session_usage(
    session_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get token usage for a session, but only if it belongs to the user."""
    result = await db.execute(
        select(Conversation.id).where(
            Conversation.id == session_id,
            Conversation.user_id == uuid.UUID(user["id"]),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return get_tracker().get_session_total(str(session_id))
