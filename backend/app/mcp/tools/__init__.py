# 导入即注册（各模块用 ``@mcp.tool()`` 挂到 server.mcp 上）。**不要当成未用
# 导入删掉**——删了工具就消失了。
from . import project, story, character  # noqa: F401
