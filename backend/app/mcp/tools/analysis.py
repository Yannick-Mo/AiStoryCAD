from app.mcp.server import mcp
from app.database import async_session
from app.mcp.auth import get_current_user_mcp, verify_project_ownership


@mcp.tool()
async def check_consistency(token: str, project_id: str) -> dict:
    """检查故事一致性（角色、情节、设定），返回一致性报告"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        try:
            from app.agent.consistency.checker import ConsistencyChecker
            checker = ConsistencyChecker(db)
            report = await checker.check_all(project_id, requested_by=user["id"])
            return report.model_dump(mode="json")
        except Exception as e:
            # Surface the real reason (balance / auth / LLM failure) instead
            # of a canned "检查失败" that hides the root cause.
            return {"error": f"一致性检查失败：{e}", "issues": [], "summary": ""}


@mcp.tool()
async def analyze_rhythm(token: str, project_id: str) -> dict:
    """分析故事节奏（动作、悬疑、情感、幽默、强度），返回分析报告"""
    async with async_session() as db:
        user = await get_current_user_mcp(token, db)
        await verify_project_ownership(project_id, user["id"], db)
        try:
            from app.agent.rhythm.analyzer import RhythmAnalyzer
            analyzer = RhythmAnalyzer(db)
            return await analyzer.analyze(project_id)
        except Exception:
            return {"error": "节奏分析失败"}
