from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import async_session, init_db
from app.llm import configure_from_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_from_settings(settings)
    await init_db()

    # Single-user local tool: if a model config row exists in the DB it
    # overrides the environment-variable defaults (hot-reloadable via the
    # settings API afterwards).
    from app.database import async_session as _app_async_session
    from app.settings.service import load_and_apply
    try:
        await load_and_apply(_app_async_session())
    except Exception:
        import logging
        logging.getLogger(__name__).exception("failed to load model config from DB")
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
    from app.api.routes_settings import router as settings_router
    app.include_router(settings_router)

    from app.mcp.server import mcp
    app.mount("/mcp", mcp.sse_app())


register_routers()
