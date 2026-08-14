import uuid
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.knowledge.models import KnowledgeChunk


class VectorStore:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def search_fts(
        self,
        query: str,
        genre: str | None = None,
        project_id: uuid.UUID | None = None,
        limit: int = 5,
    ) -> list[dict]:
        if not genre:
            genre = None
        if not project_id:
            project_id = None
        query = query[:500]
        limit = min(limit, 100)

        # Build params dynamically: when genre/project_id is None, omit the
        # WHERE clause entirely so asyncpg never sees a NULL-bound parameter
        # in an equality comparison — that causes AmbiguousParameterError
        # because PostgreSQL can't infer the column type.
        params: dict = {"query": query, "limit": limit}
        where_clauses = [
            "to_tsvector('simple', content) @@ plainto_tsquery('simple', :query)",
        ]
        if genre is not None:
            where_clauses.append("genre = :genre")
            params["genre"] = genre
        if project_id is not None:
            where_clauses.append(
                "(project_id = :project_id OR project_id IS NULL)"
            )
            if isinstance(project_id, uuid.UUID):
                params["project_id"] = project_id
            else:
                params["project_id"] = project_id
        else:
            # project_id 缺失时明确限定为全局知识 chunk(project_id IS NULL),
            # 防止跨租户检索到其它用户私有知识,避免 RAG 数据泄露。
            where_clauses.append("project_id IS NULL")
        where_sql = " AND ".join(where_clauses)

        sql = text(f"""
            SELECT id, content, source_type, genre, tags,
                   ts_rank(to_tsvector('simple', content), plainto_tsquery('simple', :query)) as rank
            FROM knowledge_chunks
            WHERE {where_sql}
            ORDER BY rank DESC
            LIMIT :limit
        """)
        result = await self.db.execute(sql, params)
        rows = result.fetchall()
        return [
            {
                "id": str(r[0]),
                "content": r[1],
                "source_type": r[2],
                "genre": r[3],
                "tags": r[4],
                "similarity": float(r[5]) if r[5] is not None else 0.0,
            }
            for r in rows
        ]

    async def search(
        self,
        query_embedding: list[float],
        genre: str | None = None,
        project_id: uuid.UUID | None = None,
        limit: int = 5,
    ) -> list[dict]:
        if not genre:
            genre = None
        if not project_id:
            project_id = None
        limit = min(limit, 100)

        params: dict = {
            "query_emb": query_embedding,
            "query_emb2": query_embedding,
            "limit": limit,
        }
        where_clauses = ["embedding IS NOT NULL"]
        if genre is not None:
            where_clauses.append("genre = :genre")
            params["genre"] = genre
        if project_id is not None:
            where_clauses.append(
                "(project_id = :project_id OR project_id IS NULL)"
            )
            if isinstance(project_id, uuid.UUID):
                params["project_id"] = project_id
            else:
                params["project_id"] = project_id
        else:
            # project_id 缺失时明确限定为全局知识 chunk(project_id IS NULL),
            # 防止跨租户检索到其它用户私有知识,避免 RAG 数据泄露。
            where_clauses.append("project_id IS NULL")
        where_sql = " AND ".join(where_clauses)

        sql = text(f"""
            SELECT id, content, source_type, genre, tags,
                    1 - (embedding <=> :query_emb) as similarity
            FROM knowledge_chunks
            WHERE {where_sql}
            ORDER BY embedding <=> :query_emb2
            LIMIT :limit
        """)
        result = await self.db.execute(sql, params)
        rows = result.fetchall()
        return [
            {
                "id": str(r[0]),
                "content": r[1],
                "source_type": r[2],
                "genre": r[3],
                "tags": r[4],
                "similarity": float(r[5]) if r[5] is not None else 0.0,
            }
            for r in rows
        ]

    async def add_chunk(self, chunk_data: dict, user_id: str | None = None) -> KnowledgeChunk:
        project_id = chunk_data.get("project_id")
        if project_id and user_id:
            from app.agent.tools.base import verify_project_owner
            await verify_project_owner(self.db, project_id, user_id)
        chunk = KnowledgeChunk(
            content=chunk_data["content"],
            embedding=chunk_data.get("embedding"),
            source_type=chunk_data["source_type"],
            genre=chunk_data.get("genre"),
            tags=chunk_data.get("tags", []),
            project_id=project_id,
            user_id=chunk_data.get("user_id"),
        )
        self.db.add(chunk)
        await self.db.commit()
        await self.db.refresh(chunk)
        return chunk

    async def delete_chunk(self, chunk_id: uuid.UUID, user_id: str | None = None) -> bool:
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.id == chunk_id)
        result = await self.db.execute(stmt)
        chunk = result.scalar_one_or_none()
        if not chunk:
            return False
        if chunk.project_id and user_id:
            from app.agent.tools.base import verify_project_owner
            await verify_project_owner(self.db, chunk.project_id, user_id)
        await self.db.delete(chunk)
        await self.db.commit()
        return True

    async def get_chunks_by_project(self, project_id: uuid.UUID | None = None, user_id: str | None = None) -> list[KnowledgeChunk]:
        if project_id and user_id:
            from app.agent.tools.base import verify_project_owner
            await verify_project_owner(self.db, project_id, user_id)
        if project_id is None:
            return []
        stmt = select(KnowledgeChunk).where(
            (KnowledgeChunk.project_id == project_id) | (KnowledgeChunk.project_id.is_(None))
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()
