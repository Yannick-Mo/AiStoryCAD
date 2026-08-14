from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings, validate_jwt_secret
from app.database import init_db
from app.llm import configure_from_settings

def _validate_config():
    validate_jwt_secret()
_validate_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_from_settings(settings)
    await init_db()

    # Consistency v3 write path: ORM events → inbox → background worker
    # (+ periodic hash audit as the runtime fallback net, §5.1 兜底 A).
    worker = None
    audit_task = None
    worker_task = None
    try:
        from app.agent.consistency.worker import FactWorker, Inbox, register_worker
        from app.database import async_session
        from app.events.consistency_events import register_scene_content_events

        inbox = Inbox()
        worker = FactWorker(inbox, async_session)
        register_scene_content_events(inbox)
        register_worker(worker)
        worker_task = asyncio.create_task(worker.run_forever())
        audit_task = asyncio.create_task(_audit_loop(worker))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("consistency worker startup failed")

    # Cross-check tool registry vs filter sets at startup
    import logging
    logger = logging.getLogger(__name__)
    from app.agent.tools import get_tool_registry
    from app.agent.tool_filter import verify_tool_registry
    registry = get_tool_registry()
    issues = verify_tool_registry(registry)
    for issue in issues:
        logger.warning("Tool registry drift: %s", issue)

    yield
    if worker is not None:
        await worker.stop()
    if audit_task is not None:
        audit_task.cancel()
    try:
        if worker_task is not None:
            worker_task.cancel()
    except Exception:
        pass
    from app.llm.client import close_shared_client
    await close_shared_client()


async def _audit_loop(worker):
    """Periodic hash audit (兜底 A): default every 60s, see config."""
    import asyncio
    from app.config import settings

    interval = settings.consistency_audit_interval_s
    while True:
        await asyncio.sleep(interval)
        try:
            await worker.audit_now()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("consistency audit failed")


app = FastAPI(title="StoryCAD", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'"
    return response


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.2.0"}


def register_routers():
    from app.api.routes_auth import router as auth_router
    app.include_router(auth_router)
    from app.api.routes_project import router as project_router
    app.include_router(project_router)
    from app.api.routes_storycad import router as storycad_router
    app.include_router(storycad_router)
    from app.api.routes_ai import router as ai_router
    app.include_router(ai_router)
    from app.api.routes_ai import material_router
    app.include_router(material_router)
    from app.api.routes_ai_v2 import router as ai_v2_router
    app.include_router(ai_v2_router)
    from app.api.routes_inspiration import router as inspiration_router
    app.include_router(inspiration_router)
    from app.api.routes_rhythm import router as rhythm_router
    app.include_router(rhythm_router)
    from app.api.routes_consistency import router as consistency_router
    app.include_router(consistency_router)

    from app.mcp.server import mcp
    _mount_secured_mcp(mcp)


_MCP_MAX_CONNECTIONS_PER_USER = 5


async def _extract_mcp_token(request: Request, method: str, path: str) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:]
    # query token 仅允许用于 SSE 建连端点(GET /mcp/sse);
    # POST /mcp/messages 等其它请求不允许携带 query token。
    if method == "GET" and path.rstrip("/") == "/mcp/sse":
        return request.query_params.get("token")
    return None


async def _mcp_transport_auth(scope, receive, send, mcp_app):
    """Transport-layer auth for the MCP SSE mount: valid JWT (Bearer header
    or ?token= query for the SSE endpoint) is required, plus a soft per-user
    connection cap. Per-tool token checks in app.mcp.auth remain the second
    layer."""
    if scope["type"] != "http":
        await mcp_app(scope, receive, send)
        return
    from starlette.requests import Request as StarletteRequest

    request = StarletteRequest(scope, receive)
    method = scope.get("method", "")
    path = scope.get("path", "")
    token = await _extract_mcp_token(request, method, path)
    if not token:
        response = JSONResponse({"detail": "Missing authentication token"}, status_code=401)
        await response(scope, receive, send)
        return

    from app.api.deps import decode_token, get_redis

    payload = await decode_token(token)
    if payload is None:
        response = JSONResponse({"detail": "Invalid or revoked token"}, status_code=401)
        await response(scope, receive, send)
        return

    redis = await get_redis()
    conn_key = f"mcp:conn:{payload['sub']}"
    if redis is not None:
        try:
            count = await redis.incr(conn_key)
            await redis.expire(conn_key, 300)
            if int(count) > _MCP_MAX_CONNECTIONS_PER_USER:
                await redis.decr(conn_key)
                response = JSONResponse({"detail": "Too many MCP connections"}, status_code=429)
                await response(scope, receive, send)
                return
        except Exception:
            redis = None
    try:
        await mcp_app(scope, receive, send)
    finally:
        if redis is not None:
            try:
                await redis.decr(conn_key)
            except Exception:
                pass


def _mount_secured_mcp(mcp):
    mcp_app = mcp.sse_app()

    async def _secured_mcp(scope, receive, send):
        await _mcp_transport_auth(scope, receive, send, mcp_app)

    app.mount("/mcp", _secured_mcp)


register_routers()
