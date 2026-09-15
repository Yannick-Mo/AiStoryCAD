import uuid
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.project.repository import ProjectRepository
from app.project.models import Project
from app.storycad.models import Chapter
from app.utils import row_to_dict


# count_words（app/agent/utils.py）的中文区间。Python 字符串里的 \u 转义会
# 变成真实字符，所以传给 Postgres 的就是 [一-鿿] / [^一-鿿]。
_CJK_CLASS = "[\u4e00-\u9fff]"
_NOT_CJK_CLASS = "[^\u4e00-\u9fff]"


def word_count_expr(column):
    """与 ``app.agent.utils.count_words`` 口径一致的 SQL 字数表达式。

    字数不落库（派生值不存），但项目列表要显示进度，所以必须有一段 SQL
    复刻同一套规则：CJK 逐字计数 + 非 CJK 按空白分词。两处口径不一致的话，
    同一个项目会出现两个"字数"。
    """
    cjk = func.length(func.regexp_replace(func.coalesce(column, ""), _NOT_CJK_CLASS, "", "g"))
    remainder = func.btrim(func.regexp_replace(func.coalesce(column, ""), _CJK_CLASS, "", "g"))
    non_cjk = case(
        (remainder == "", 0),
        else_=func.array_length(func.regexp_split_to_array(remainder, r"\s+"), 1),
    )
    return cjk + non_cjk


class ProjectService:
    def __init__(self, db: AsyncSession):
        self.repo = ProjectRepository(db)

    async def create_project(self, title: str, description: str, owner_id: uuid.UUID) -> dict:
        project = await self.repo.create(title, description, owner_id)
        return row_to_dict(project)

    async def get_project(self, project_id: uuid.UUID, owner_id: uuid.UUID) -> dict | None:
        project = await self.repo.get(project_id)
        if not project or project.owner_id != owner_id:
            return None
        return row_to_dict(project)

    async def list_projects(self, owner_id: uuid.UUID, page: int = 1, size: int = 20, search: str = "", status: str = "") -> dict:
        projects = await self.repo.list_projects(owner_id, page, size, search, status)

        if not projects:
            return {"items": [], "total": 0, "page": page, "size": size}

        project_ids = [p.id for p in projects]

        chapter_stmt = select(
            Chapter.project_id,
            func.count().label("chapter_count"),
            func.coalesce(func.sum(word_count_expr(Chapter.content)), 0).label("written_words"),
        ).where(
            Chapter.project_id.in_(project_ids)
        ).group_by(Chapter.project_id)
        chapter_rows = (await self.repo.db.execute(chapter_stmt)).all()
        chapter_map = {
            row.project_id: {"chapters": int(row.chapter_count), "words": int(row.written_words)}
            for row in chapter_rows
        }

        items = []
        for project in projects:
            stats = chapter_map.get(project.id, {"chapters": 0, "words": 0})
            item = row_to_dict(project)
            item["total_chapters"] = stats["chapters"]
            # 这两个键曾经都叫 total_words（一个是目标、一个是已写），
            # 现在分开：target_words 是设定，written_words 是实时算出来的。
            item["target_words"] = int(project.total_words or 0)
            item["written_words"] = stats["words"]
            items.append(item)

        total = (await self.repo.db.execute(
            select(func.count()).select_from(Project).where(Project.owner_id == owner_id)
        )).scalar() or 0

        return {"items": items, "total": total, "page": page, "size": size}

    async def update_project(self, project_id: uuid.UUID, owner_id: uuid.UUID, **kwargs) -> bool:
        project = await self.repo.get(project_id)
        if not project or project.owner_id != owner_id:
            return False
        return await self.repo.update(project_id, **kwargs)

    async def delete_project(self, project_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
        project = await self.repo.get(project_id)
        if not project or project.owner_id != owner_id:
            return False
        return await self.repo.delete(project_id)
