import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from github_review import (  # noqa: E402
    build_comments,
    commentable_lines,
    diff_fingerprint,
    failure_note,
    inline_disabled,
    parse_summary,
    rebase_note,
    render_summary,
    resolvable,
    should_skip,
    strip_heading,
    without_suggestions,
)

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

# The same PR after a rebase: main grew above the change (line numbers shift)
# and a neighbouring context line changed, but the PR's own +/- lines are identical.
REBASED = """\
diff --git a/app/server.py b/app/server.py
index 3333333..4444444 100644
--- a/app/server.py
+++ b/app/server.py
@@ -30,4 +30,5 @@ def handler():
     a = 10
-    b = 2
+    b = 3
+    c = 4
     return a
@@ -60,2 +61,2 @@ def other():
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

    def test_added_line_that_looks_like_a_header(self):
        diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n a\n+++ not a header\n"
        self.assertEqual(commentable_lines(diff), {"x": {1, 2}})

    def test_quoted_path(self):
        diff = 'diff --git a/q b/q\n--- "a/dir/caf\\303\\251.txt"\n+++ "b/dir/caf\\303\\251.txt"\n@@ -1 +1 @@\n+x\n'
        self.assertIn("dir/caf\\303\\251.txt", commentable_lines(diff))

    def test_empty_diff(self):
        self.assertEqual(commentable_lines(""), {})


class DiffFingerprint(unittest.TestCase):
    def test_rebase_keeps_fingerprint(self):
        self.assertEqual(diff_fingerprint(DIFF), diff_fingerprint(REBASED))

    def test_real_change_changes_fingerprint(self):
        self.assertNotEqual(diff_fingerprint(DIFF), diff_fingerprint(DIFF.replace("+    c = 4", "+    c = 5")))

    def test_extra_file_changes_fingerprint(self):
        extra = DIFF + "diff --git a/z b/z\n--- a/z\n+++ b/z\n@@ -1 +1 @@\n-1\n+2\n"
        self.assertNotEqual(diff_fingerprint(DIFF), diff_fingerprint(extra))

    def test_same_line_in_another_file_differs(self):
        a = "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n+x\n"
        self.assertNotEqual(diff_fingerprint(a), diff_fingerprint(a.replace("a/a", "a/b").replace("b/a", "b/b")))

    def test_trailing_whitespace_ignored(self):
        self.assertEqual(diff_fingerprint(DIFF), diff_fingerprint(DIFF.replace("+    c = 4", "+    c = 4   ")))

    def test_empty_diff_has_none(self):
        self.assertIsNone(diff_fingerprint(""))


class BuildComments(unittest.TestCase):
    valid = {"a.py": {1, 2, 3, 4, 10}}

    def build(self, inline, open_lines=frozenset(), limit=5):
        return build_comments(inline, self.valid, set(open_lines), limit)

    def test_single_line(self):
        comments, skipped = self.build([{"path": "a.py", "line": 2, "body": "🔴 x"}])
        self.assertEqual(comments, [{"path": "a.py", "line": 2, "side": "RIGHT", "body": "🔴 x"}])
        self.assertEqual(skipped, [])

    def test_multi_line_range(self):
        comments, _ = self.build([{"path": "a.py", "start_line": 1, "line": 3, "body": "🟠"}])
        self.assertEqual((comments[0]["start_line"], comments[0]["start_side"]), (1, "RIGHT"))

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
        self.assertEqual(self.build([{"path": "b.py", "line": 1, "body": "🔴"}])[0], [])

    def test_open_thread_is_not_duplicated(self):
        comments, skipped = self.build([{"path": "a.py", "line": 2, "body": "🔴"}], open_lines={("a.py", 2)})
        self.assertEqual(comments, [])
        self.assertIn("open thread", skipped[0])

    def test_malformed_items_are_skipped(self):
        comments, skipped = self.build([{"line": 2}, {"path": "a.py", "line": "x", "body": ""}, "nope"])
        self.assertEqual(comments, [])
        self.assertEqual(len(skipped), 3)

    def test_limit(self):
        inline = [{"path": "a.py", "line": n, "body": "🟠"} for n in (1, 2, 3, 4, 10)]
        self.assertEqual([c["line"] for c in self.build(inline, limit=2)[0]], [1, 2])

    def test_not_a_list(self):
        self.assertEqual(self.build({"path": "a.py"}), ([], []))

    def test_suggestions_stripped(self):
        comments, _ = self.build([{"path": "a.py", "line": 1, "body": "```suggestion\nx\n```"}])
        self.assertNotIn("suggestion", comments[0]["body"])


class WithoutSuggestions(unittest.TestCase):
    def test_suggestion_becomes_plain_code_block(self):
        self.assertEqual(without_suggestions("🔴 fix\n```suggestion\n    x = 0\n```"), "🔴 fix\n```\n    x = 0\n```")

    def test_long_fence_and_indent(self):
        self.assertEqual(without_suggestions("  ````suggestion\nx\n  ````"), "  ````\nx\n  ````")

    def test_other_code_blocks_untouched(self):
        body = "```python\nx = 0\n```\nsee suggestion below"
        self.assertEqual(without_suggestions(body), body)


class Summary(unittest.TestCase):
    state = {"fingerprint": "f00", "sha": "abcdef1234567890", "pass": 2}

    def test_round_trip(self):
        body = "Looks good.\n\nNo blocking issues."
        rendered = render_summary(self.state, body)
        self.assertEqual(parse_summary(rendered), (self.state, body))
        self.assertIn("## Claude review · `abcdef1` · pass 2", rendered)

    def test_note_is_not_part_of_body(self):
        rendered = render_summary(self.state, "Body.", note=rebase_note(self.state, "9999999aaaa"))
        self.assertIn("_Rebased: no changes", rendered)
        self.assertEqual(parse_summary(rendered)[1], "Body.")

    def test_foreign_comment_is_not_ours(self):
        self.assertEqual(parse_summary("## Review\nsomething"), ({}, ""))
        self.assertEqual(parse_summary("<!-- claude-pr-review:state {broken -->\n<!-- claude-pr-review:body -->\nx"), ({}, ""))

    def test_not_reviewed_yet(self):
        state = {"sha": "abcdef1234567890", "pass": 0}
        rendered = render_summary(state, "", note="_⚠️ failed_")
        self.assertIn("## Claude review · `abcdef1` · not reviewed yet", rendered)
        self.assertEqual(parse_summary(rendered), (state, ""))

    def test_strip_heading(self):
        self.assertEqual(strip_heading("## Review\n\nVerdict."), "Verdict.")
        self.assertEqual(strip_heading("Verdict.\n## Later heading"), "Verdict.\n## Later heading")


class FailureNote(unittest.TestCase):
    def test_usage_limit(self):
        note = failure_note("Claude AI usage limit reached|1762891200", "9999999aaaa", {})
        self.assertIn("Not reviewed at `9999999`: the review seat hit its usage limit.", note)
        self.assertNotIn("review below", note)

    def test_other_error_first_line_only(self):
        note = failure_note("API Error: 500 overloaded\nstack…", "9999999aaaa", {})
        self.assertIn(": API Error: 500 overloaded.", note)
        self.assertNotIn("stack", note)

    def test_points_at_earlier_review(self):
        note = failure_note("boom", "9999999aaaa", {"sha": "abcdef1234", "pass": 2})
        self.assertIn("The review below is from `abcdef1`.", note)


class ShouldSkip(unittest.TestCase):
    state = {"fingerprint": "f00", "sha": "abc", "pass": 1}

    def test_push_with_identical_changes_is_skipped(self):
        self.assertTrue(should_skip("synchronize", self.state, "f00"))

    def test_push_with_new_changes_runs(self):
        self.assertFalse(should_skip("synchronize", self.state, "bar"))

    def test_other_events_always_run(self):
        for action in ("opened", "reopened"):
            self.assertFalse(should_skip(action, self.state, "f00"))

    def test_no_previous_review_or_no_diff_runs(self):
        self.assertFalse(should_skip("synchronize", {}, "f00"))
        self.assertFalse(should_skip("synchronize", self.state, None))


class Resolvable(unittest.TestCase):
    threads = [{"id": "T1"}, {"id": "T2"}]

    def test_only_known_open_threads(self):
        self.assertEqual(resolvable(["T2", "T9", 5, None], self.threads), ["T2"])

    def test_not_a_list(self):
        self.assertEqual(resolvable({"T1": True}, self.threads), [])


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
