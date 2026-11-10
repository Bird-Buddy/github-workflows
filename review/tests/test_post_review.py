import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from post_review import build_comments, commentable_lines, inline_disabled, without_suggestions  # noqa: E402

DIFF = """\
diff --git a/app/server.py b/app/server.py
index 1111111..2222222 100644
--- a/app/server.py
+++ b/app/server.py
@@ -10,4 +10,5 @@ def handler():
     a = 1
-    b = 2
+    b = 3
+    c = 4
     return a
@@ -40,2 +41,2 @@ def other():
-    x = 0
+    x = 1
     y = 2
diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-gone
-too
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+hello
+world
\\ No newline at end of file
"""


class CommentableLines(unittest.TestCase):
    def setUp(self):
        self.valid = commentable_lines(DIFF)

    def test_added_and_context_lines_of_new_file(self):
        self.assertEqual(self.valid["app/server.py"], {10, 11, 12, 13, 41, 42})

    def test_deleted_file_has_no_lines(self):
        self.assertNotIn("old.txt", self.valid)

    def test_new_file(self):
        self.assertEqual(self.valid["new.txt"], {1, 2})

    def test_removed_lines_do_not_count(self):
        self.assertNotIn(14, self.valid["app/server.py"])

    def test_quoted_path(self):
        diff = '+++ "b/dir/caf\\303\\251.txt"\n@@ -1 +1 @@\n+x\n'
        self.assertIn("dir/caf\\303\\251.txt", commentable_lines(diff))

    def test_empty_diff(self):
        self.assertEqual(commentable_lines(""), {})


class BuildComments(unittest.TestCase):
    valid = {"a.py": {1, 2, 3, 4, 10}}

    def build(self, inline, threads=frozenset(), limit=5):
        return build_comments(inline, self.valid, set(threads), limit)

    def test_single_line(self):
        comments, skipped = self.build([{"path": "a.py", "line": 2, "body": "🔴 x"}])
        self.assertEqual(comments, [{"path": "a.py", "line": 2, "side": "RIGHT", "body": "🔴 x"}])
        self.assertEqual(skipped, [])

    def test_multi_line_range(self):
        comments, _ = self.build([{"path": "a.py", "start_line": 1, "line": 3, "body": "🟠"}])
        self.assertEqual(comments[0]["start_line"], 1)
        self.assertEqual(comments[0]["start_side"], "RIGHT")

    def test_range_with_gap_outside_diff_is_dropped(self):
        comments, skipped = self.build([{"path": "a.py", "start_line": 4, "line": 10, "body": "🟠"}])
        self.assertEqual(comments, [])
        self.assertIn("not in diff", skipped[0])

    def test_start_equal_to_line_is_single_line(self):
        comments, _ = self.build([{"path": "a.py", "start_line": 2, "line": 2, "body": "🔴"}])
        self.assertNotIn("start_line", comments[0])

    def test_reversed_range_is_dropped(self):
        comments, skipped = self.build([{"path": "a.py", "start_line": 3, "line": 1, "body": "🔴"}])
        self.assertEqual(comments, [])
        self.assertIn("bad range", skipped[0])

    def test_line_outside_diff_is_dropped(self):
        comments, skipped = self.build([{"path": "a.py", "line": 99, "body": "🔴"}])
        self.assertEqual(comments, [])
        self.assertIn("not in diff", skipped[0])

    def test_unknown_file_is_dropped(self):
        comments, _ = self.build([{"path": "b.py", "line": 1, "body": "🔴"}])
        self.assertEqual(comments, [])

    def test_open_bot_thread_is_not_duplicated(self):
        comments, skipped = self.build([{"path": "a.py", "line": 2, "body": "🔴"}], threads={("a.py", 2)})
        self.assertEqual(comments, [])
        self.assertIn("open thread", skipped[0])

    def test_malformed_items_are_skipped(self):
        comments, skipped = self.build([{"line": 2}, {"path": "a.py", "line": "x", "body": ""}, "nope"])
        self.assertEqual(comments, [])
        self.assertEqual(len(skipped), 3)

    def test_limit(self):
        inline = [{"path": "a.py", "line": n, "body": "🟠"} for n in (1, 2, 3, 4, 10)]
        comments, _ = self.build(inline, limit=2)
        self.assertEqual([c["line"] for c in comments], [1, 2])

    def test_not_a_list(self):
        self.assertEqual(self.build({"path": "a.py"}), ([], []))


class WithoutSuggestions(unittest.TestCase):
    def test_suggestion_becomes_plain_code_block(self):
        body = "🔴 fix\n```suggestion\n    x = 0\n```"
        self.assertEqual(without_suggestions(body), "🔴 fix\n```\n    x = 0\n```")

    def test_long_fence_and_indent(self):
        self.assertEqual(without_suggestions("  ````suggestion\nx\n  ````"), "  ````\nx\n  ````")

    def test_other_code_blocks_untouched(self):
        body = "```python\nx = 0\n```\nsee suggestion below"
        self.assertEqual(without_suggestions(body), body)

    def test_applied_to_inline_comments(self):
        comments, _ = build_comments([{"path": "a.py", "line": 1, "body": "```suggestion\nx\n```"}], {"a.py": {1}}, set())
        self.assertNotIn("suggestion", comments[0]["body"])


class InlineDisabled(unittest.TestCase):
    def test_off(self):
        self.assertTrue(inline_disabled("# rules\ninline: off\n"))
        self.assertTrue(inline_disabled("  Inline:   OFF"))

    def test_not_off(self):
        self.assertFalse(inline_disabled("inline: on"))
        self.assertFalse(inline_disabled("we keep inline: offline docs"))
        self.assertFalse(inline_disabled(""))


if __name__ == "__main__":
    unittest.main()
