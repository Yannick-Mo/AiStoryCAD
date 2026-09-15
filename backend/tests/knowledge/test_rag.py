"""Tests for RAGEngine: retrieve_context and retrieve_chunks."""
import uuid
from unittest.mock import AsyncMock, patch
from app.knowledge.rag import RAGEngine


class TestRetrieveChunks:
    @patch("app.knowledge.rag.embed_text")
    async def test_retrieve_chunks_with_results(self, mock_embed_text):
        mock_embed_text.return_value = [0.1, 0.2, 0.3]
        db = AsyncMock()
        engine = RAGEngine(db)
        engine.vector_store.search_fts = AsyncMock(return_value=[
            {"id": "c1", "content": "Test knowledge", "source_type": "writing_guide",
             "genre": "fantasy", "tags": ["plot"], "similarity": 0.92},
            {"id": "c2", "content": "More knowledge", "source_type": "reference",
             "genre": "fantasy", "tags": ["character"], "similarity": 0.85},
        ])

        chunks = await engine.retrieve_chunks(
            project_id=uuid.uuid4(), genre="fantasy", query="plot twist", limit=5
        )

        assert len(chunks) == 2
        assert chunks[0]["content"] == "Test knowledge"
        assert chunks[1]["similarity"] == 0.85
        engine.vector_store.search_fts.assert_awaited_once()

    @patch("app.knowledge.rag.embed_text")
    async def test_retrieve_chunks_empty(self, mock_embed_text):
        mock_embed_text.return_value = [0.1, 0.2, 0.3]
        db = AsyncMock()
        engine = RAGEngine(db)
        engine.vector_store.search_fts = AsyncMock(return_value=[])

        chunks = await engine.retrieve_chunks(
            project_id=None, genre=None, query="unknown", limit=5
        )

        assert chunks == []

    @patch("app.knowledge.rag.embed_text")
    async def test_retrieve_chunks_without_project(self, mock_embed_text):
        mock_embed_text.return_value = [0.1, 0.2, 0.3]
        db = AsyncMock()
        engine = RAGEngine(db)
        engine.vector_store.search_fts = AsyncMock(return_value=[
            {"id": "c1", "content": "General knowledge", "source_type": "guide",
             "genre": None, "tags": [], "similarity": 0.9},
        ])

        chunks = await engine.retrieve_chunks(
            project_id=None, genre=None, query="test", limit=5
        )

        assert len(chunks) == 1
        engine.vector_store.search_fts.assert_awaited_once()
        args, kwargs = engine.vector_store.search_fts.call_args
        assert kwargs["project_id"] is None
        assert kwargs["genre"] is None

    @patch("app.knowledge.rag.embed_text")
    @patch("app.knowledge.rag.settings.embedding_base_url", "https://embeddings.example.com")
    async def test_embedding_failure_falls_back_to_fts(self, mock_embed_text):
        mock_embed_text.side_effect = RuntimeError("API down")
        db = AsyncMock()
        engine = RAGEngine(db)
        engine.vector_store.search_fts = AsyncMock(return_value=[])

        chunks = await engine.retrieve_chunks(
            project_id=None, genre=None, query="fail", limit=5
        )

        assert chunks == []
        engine.vector_store.search_fts.assert_awaited_once()


class TestRetrieveContext:
    @patch("app.knowledge.rag.embed_text")
    async def test_retrieve_context_formatted(self, mock_embed_text):
        mock_embed_text.return_value = [0.1, 0.2, 0.3]
        db = AsyncMock()
        engine = RAGEngine(db)
        engine.vector_store.search_fts = AsyncMock(return_value=[
            {"id": "c1", "content": "Try a three-act structure.",
             "source_type": "writing_guide", "genre": "fantasy",
             "tags": ["structure", "plot"], "similarity": 0.92},
        ])

        context = await engine.retrieve_context(
            project_id=uuid.uuid4(), genre="fantasy",
            query="structure", limit=3
        )

        assert "Retrieved Knowledge" in context
        assert "Writing Guide" in context
        assert "Tags:" in context
        assert "structure" in context
        assert "Try a three-act structure" in context

    @patch("app.knowledge.rag.embed_text")
    async def test_retrieve_context_empty_chunks(self, mock_embed_text):
        mock_embed_text.return_value = [0.1, 0.2, 0.3]
        db = AsyncMock()
        engine = RAGEngine(db)
        engine.vector_store.search_fts = AsyncMock(return_value=[])

        context = await engine.retrieve_context(
            project_id=uuid.uuid4(), genre="fantasy", query="nonexistent"
        )

        assert context == ""

    @patch("app.knowledge.rag.embed_text")
    async def test_retrieve_context_multiple_chunks(self, mock_embed_text):
        mock_embed_text.return_value = [0.1, 0.2, 0.3]
        db = AsyncMock()
        engine = RAGEngine(db)
        engine.vector_store.search_fts = AsyncMock(return_value=[
            {"id": "c1", "content": "First chunk", "source_type": "guide",
             "genre": "fantasy", "tags": [], "similarity": 0.9},
            {"id": "c2", "content": "Second chunk", "source_type": "reference",
             "genre": "fantasy", "tags": [], "similarity": 0.8},
        ])

        context = await engine.retrieve_context(
            project_id=None, genre="fantasy", query="test", limit=5
        )

        assert "### 1." in context
        assert "### 2." in context
        assert "First chunk" in context
        assert "Second chunk" in context