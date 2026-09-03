from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP
from app.database import async_session


@asynccontextmanager
async def mcp_lifespan(server: FastMCP):
    yield {}


mcp = FastMCP(
    "AiStoryCAD MCP",
    instructions="AiStoryCAD project management MCP server — read/write projects, chapters, scenes, characters, and run analysis.",
    lifespan=mcp_lifespan,
)


from app.mcp.tools import project, story, character, analysis
