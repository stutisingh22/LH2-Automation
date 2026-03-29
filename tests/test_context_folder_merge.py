"""Unit tests for bounded Drive context-folder merging (no API calls)."""

from __future__ import annotations

import unittest

from services.drive_context_merge import merge_context_folder_documents


class TestMergeContextFolderDocuments(unittest.TestCase):
    def test_sorting_left_to_caller(self):
        """Caller sorts by name; merge preserves order of input iterable."""
        docs = [
            ("z-last.txt", "y" * 200),
            ("a-first.txt", "x" * 200),
        ]
        out, n = merge_context_folder_documents(
            docs,
            max_total_chars=50_000,
            max_per_file_chars=10_000,
        )
        self.assertEqual(n, 2)
        self.assertIn("z-last", out.split("\n", 1)[0])

    def test_total_cap_stops_early(self):
        docs = [
            ("a", "A" * 500),
            ("b", "B" * 500),
            ("c", "C" * 500),
        ]
        out, n = merge_context_folder_documents(
            docs,
            max_total_chars=400,
            max_per_file_chars=1000,
        )
        self.assertLessEqual(len(out), 400)
        self.assertGreaterEqual(n, 1)
        self.assertIn("...(truncated)", out)

    def test_per_file_cap(self):
        docs = [("only", "Z" * 5000)]
        out, n = merge_context_folder_documents(
            docs,
            max_total_chars=50_000,
            max_per_file_chars=120,
        )
        self.assertEqual(n, 1)
        self.assertIn("...(truncated)", out)
        self.assertLess(len(out), 500)

    def test_skips_short_text(self):
        docs = [("tiny", "hi")]
        out, n = merge_context_folder_documents(
            docs,
            max_total_chars=1000,
            max_per_file_chars=500,
        )
        self.assertEqual(out, "")
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
