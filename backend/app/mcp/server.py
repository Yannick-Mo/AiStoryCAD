from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP


@asynccontextmanager
async def mcp_lifespan(server: FastMCP):
    yield {}


mcp = FastMCP(
    "AiStoryCAD MCP",
    instructions="AiStoryCAD project management MCP server — read/write projects, acts, chapters, characters, relations and setups; read progress.",
    lifespan=mcp_lifespan,
)


# **必须在文件末尾**：这几个模块会 ``from app.mcp.server import mcp`` 来注册
# 自己的工具，放在顶部会循环导入。它也不“未使用”——导入本身就把
# ``@mcp.tool()`` 注册上了。静态检查会把它报成 unused（F401）和位置不对（E402），
# 两条都是它该带的豁免，别删。
from app.mcp.tools import project, story, character  # noqa: F401, E402
