"""Tests for VectorStore: search, add_chunk, delete_chunk."""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.knowledge.vector_store import VectorStore


class TestSearch:
    async def test_search_returns_chunks(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("c1", "Knowledge content", "writing_guide", "fantasy", ["plot"], 0.92),
            ("c2", "More content", "reference", "fantasy", ["character"], 0.85),
        ]
        db.execute = AsyncMock(return_value=mock_result)
        store = VectorStore(db)

        results = await store.search(
            query_embedding=[0.1, 0.2, 0.3],
            genre="fantasy",
            project_id=uuid.uuid4(),
            limit=5,
        )

        assert len(results) == 2
        assert results[0]["content"] == "Knowledge content"
        assert results[1]["similarity"] == 0.85
        db.execute.assert_awaited_once()

    async def test_search_empty_result(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        db.execute = AsyncMock(return_value=mock_result)
        store = VectorStore(db)

        results = await store.search(
            query_embedding=[0.1, 0.2, 0.3],
            genre=None,
            project_id=None,
            limit=5,
        )

        assert results == []

    async def test_search_with_null_similarity(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("c1", "Content", "guide", "general", [], None),
        ]
        db.execute = AsyncMock(return_value=mock_result)
        store = VectorStore(db)

        results = await store.search(
            query_embedding=[0.1, 0.2, 0.3],
            limit=5,
        )

        assert results[0]["similarity"] == 0.0

    async def test_search_without_filters(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("c1", "General", "guide", None, [], 0.5),
        ]
        db.execute = AsyncMock(return_value=mock_result)
        store = VectorStore(db)

        results = await store.search(
            query_embedding=[0.1, 0.2, 0.3],
            limit=5,
        )

        assert len(results) == 1
        db.execute.assert_awaited_once()


class TestAddChunk:
    async def test_add_chunk_success(self):
        db = AsyncMock()
        store = VectorStore(db)

        await store.add_chunk({
            "content": "Test content",
            "source_type": "guide",
            "genre": "fantasy",
            "tags": ["plot"],
        })

        db.add.assert_called_once()
        db.commit.assert_awaited_once()
        db.refresh.assert_awaited_once()

    async def test_add_chunk_with_embedding_and_ids(self):
        db = AsyncMock()
        store = VectorStore(db)

        await store.add_chunk({
            "content": "Vectorized content",
            "embedding": [0.1, 0.2, 0.3],
            "source_type": "reference",
            "genre": "scifi",
            "tags": ["worldbuilding"],
            "project_id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
        })

        db.add.assert_called_once()
        db.commit.assert_awaited_once()

    async def test_add_chunk_no_embedding(self):
        db = AsyncMock()
        store = VectorStore(db)

        await store.add_chunk({
            "content": "No embedding",
            "source_type": "note",
        })

        db.add.assert_called_once()
        db.commit.assert_awaited_once()


class TestDeleteChunk:
    async def test_delete_existing_chunk(self):
        db = AsyncMock()
        chunk = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = chunk
        db.execute = AsyncMock(return_value=mock_result)
        store = VectorStore(db)

        result = await store.delete_chunk(uuid.uuid4())

        assert result is True
        db.delete.assert_awaited_once_with(chunk)
        db.commit.assert_awaited_once()

    async def test_delete_nonexistent_chunk(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_result)
        store = VectorStore(db)

        result = await store.delete_chunk(uuid.uuid4())

        assert result is False
        db.delete.assert_not_called()
        db.commit.assert_not_called()

    async def test_delete_with_invalid_id_raises(self):
        db = AsyncMock()
        db.execute.side_effect = ValueError("invalid uuid")
        store = VectorStore(db)

        with pytest.raises(ValueError, match="invalid uuid"):
            await store.delete_chunk("not-a-uuid")