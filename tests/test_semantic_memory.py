"""Tests for core/semantic_memory.py — embedding store and query."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import numpy as np


class TestEmbedText(unittest.TestCase):
    """Test _embed_text with mocked model."""

    def test_embed_text_returns_bytes_on_success(self):
        from core.semantic_memory import _embed_text

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1, 0.2, 0.3], dtype=np.float32)

        with patch("core.semantic_memory._get_model", return_value=mock_model):
            result = _embed_text("test text")
            self.assertIsInstance(result, bytes)
            # 3 floats * 4 bytes = 12 bytes
            self.assertEqual(len(result), 12)

    def test_embed_text_returns_none_when_model_unavailable(self):
        from core.semantic_memory import _embed_text

        with patch("core.semantic_memory._get_model", return_value=None):
            result = _embed_text("test text")
            self.assertIsNone(result)

    def test_embed_text_returns_none_on_exception(self):
        from core.semantic_memory import _embed_text

        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("encode failed")

        with patch("core.semantic_memory._get_model", return_value=mock_model):
            result = _embed_text("test text")
            self.assertIsNone(result)


class TestEmbedAndStoreNew(unittest.TestCase):
    """Test embed_and_store_new with mocked dependencies."""

    def test_returns_zero_when_model_unavailable(self):
        from core.semantic_memory import embed_and_store_new

        mock_store = MagicMock()
        with patch("core.semantic_memory._get_model", return_value=None):
            result = embed_and_store_new(mock_store)
            self.assertEqual(result, 0)

    def test_returns_zero_when_no_unembedded_entries(self):
        from core.semantic_memory import embed_and_store_new

        mock_model = MagicMock()
        mock_store = MagicMock()
        mock_store.get_knowledge_without_embeddings.return_value = []

        with patch("core.semantic_memory._get_model", return_value=mock_model):
            result = embed_and_store_new(mock_store)
            self.assertEqual(result, 0)

    def test_embeds_and_stores_entries(self):
        from core.semantic_memory import embed_and_store_new

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array(
            [[0.1, 0.2], [0.3, 0.4]], dtype=np.float32
        )

        mock_store = MagicMock()
        mock_store.get_knowledge_without_embeddings.return_value = [
            {"id": 1, "summary": "topic1", "detail": "detail1"},
            {"id": 2, "summary": "topic2", "detail": ""},
        ]

        with patch("core.semantic_memory._get_model", return_value=mock_model):
            result = embed_and_store_new(mock_store)
            self.assertEqual(result, 2)
            self.assertEqual(mock_store.set_knowledge_embedding.call_count, 2)

    def test_handles_encode_exception(self):
        from core.semantic_memory import embed_and_store_new

        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("batch failed")

        mock_store = MagicMock()
        mock_store.get_knowledge_without_embeddings.return_value = [
            {"id": 1, "summary": "topic1", "detail": ""},
        ]

        with patch("core.semantic_memory._get_model", return_value=mock_model):
            result = embed_and_store_new(mock_store)
            self.assertEqual(result, 0)


class TestQueryAndFormat(unittest.TestCase):
    """Test query_and_format with mocked dependencies."""

    def test_returns_none_when_store_is_none(self):
        from core.semantic_memory import query_and_format

        result = query_and_format(None, "query")
        self.assertIsNone(result)

    def test_returns_none_when_embedding_fails(self):
        from core.semantic_memory import query_and_format

        mock_store = MagicMock()
        with patch("core.semantic_memory._embed_text", return_value=None):
            result = query_and_format(mock_store, "query")
            self.assertIsNone(result)

    def test_returns_none_when_no_results(self):
        from core.semantic_memory import query_and_format

        mock_store = MagicMock()
        mock_store.query_semantic_knowledge.return_value = []

        with patch("core.semantic_memory._embed_text", return_value=b"\x00" * 8):
            result = query_and_format(mock_store, "query")
            self.assertIsNone(result)

    def test_formats_relevant_results(self):
        from core.semantic_memory import query_and_format

        mock_store = MagicMock()
        mock_store.query_semantic_knowledge.return_value = [
            {"summary": "learned A", "detail": "detail A", "category": "bug", "similarity": 0.85},
            {"summary": "learned B", "detail": "detail B", "category": "pattern", "similarity": 0.72},
        ]

        with patch("core.semantic_memory._embed_text", return_value=b"\x00" * 8):
            result = query_and_format(mock_store, "query")
            self.assertIsNotNone(result)
            self.assertIn("SEMANTIC MEMORY", result)
            self.assertIn("[bug]", result)
            self.assertIn("[pattern]", result)
            self.assertIn("learned A", result)

    def test_filters_below_min_similarity(self):
        from core.semantic_memory import query_and_format

        mock_store = MagicMock()
        mock_store.query_semantic_knowledge.return_value = [
            {"summary": "low relevance", "detail": "", "category": "general", "similarity": 0.1},
        ]

        with patch("core.semantic_memory._embed_text", return_value=b"\x00" * 8):
            result = query_and_format(mock_store, "query", min_similarity=0.3)
            self.assertIsNone(result)

    def test_handles_missing_detail(self):
        from core.semantic_memory import query_and_format

        mock_store = MagicMock()
        mock_store.query_semantic_knowledge.return_value = [
            {"summary": "topic", "category": "general", "similarity": 0.5},
        ]

        with patch("core.semantic_memory._embed_text", return_value=b"\x00" * 8):
            result = query_and_format(mock_store, "query")
            self.assertIsNotNone(result)
            self.assertIn("topic", result)


class TestGetModel(unittest.TestCase):
    """Test _get_model lazy loading."""

    def test_get_model_returns_none_on_import_error(self):
        from core.semantic_memory import _get_model
        import core.semantic_memory as sm

        # Reset cached model
        sm._model = None

        with patch("sentence_transformers.SentenceTransformer", side_effect=ImportError):
            result = _get_model()
            self.assertIsNone(result)
        sm._model = None


if __name__ == "__main__":
    unittest.main()
