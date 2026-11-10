"""Post the Claude review as ONE GitHub review: summary body + inline comments.

Reads what run_review.sh left behind (/tmp/review.md, /tmp/inline.json), drops
inline comments GitHub would reject (lines outside the diff) or that duplicate an
open bot thread, and posts the rest. If the review post fails, the summary goes
out as a plain PR comment so the review is never lost.

Env: GH_TOKEN, PR, REPO, HEAD_SHA. Run from the checked-out PR repo.
"""

import json
import os
import re
import subprocess

MAX_INLINE = 5
REVIEW_MD = "/tmp/review.md"
INLINE_JSON = "/tmp/inline.json"
REPO_RULES = (".github/review.md", "REVIEW.md")  # pinned to the base branch before this runs
HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
THREADS_QUERY = (
    "query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n){pullRequest(number:$p)"
    "{reviewThreads(first:100){nodes{isResolved path line comments(first:1){nodes{author{login}}}}}}}}"
)


def commentable_lines(diff: str) -> dict[str, set[int]]:
    """Lines GitHub accepts an inline comment on: added + context lines of the new file."""
    valid: dict[str, set[int]] = {}
    path, line = None, None
    for raw in diff.splitlines():
        if raw.startswith("diff --git"):
            path, line = None, None
        elif raw.startswith("+++ "):
            target = raw[4:].strip('"')
            path = target[2:] if target.startswith("b/") else None  # /dev/null = deleted file
        elif raw.startswith("--- "):
            pass
        elif m := HUNK.match(raw):
            line = int(m.group(1))
        elif path and line is not None and raw[:1] in ("+", " "):
            valid.setdefault(path, set()).add(line)
            line += 1
    return valid


def inline_disabled(rules: str) -> bool:
    """A repo opts out with a line `inline: off` in its review rules."""
    return re.search(r"(?im)^\s*inline:\s*off\b", rules) is not None


def build_comments(inline, valid, open_threads, limit=MAX_INLINE):
    """Turn the model's inline list into GitHub review comments; returns (comments, skipped)."""
    comments, skipped = [], []
    for item in (inline if isinstance(inline, list) else [])[:limit]:
        try:
            path, line, body = item["path"], int(item["line"]), str(item["body"])
            start = int(item["start_line"]) if item.get("start_line") is not None else None
        except (KeyError, TypeError, ValueError):
            skipped.append(f"malformed: {item!r:.80}")
            continue
        if start == line:
            start = None
        if start is not None and start > line:
            skipped.append(f"bad range: {path}:{start}-{line}")
            continue
        lines = range(start or line, line + 1)
        if not all(n in valid.get(path, ()) for n in lines):
            skipped.append(f"not in diff: {path}:{line}")
            continue
        if (path, line) in open_threads:
            skipped.append(f"open thread already: {path}:{line}")
            continue
        comment = {"path": path, "line": line, "side": "RIGHT", "body": body}
        if start:
            comment.update(start_line=start, start_side="RIGHT")
        comments.append(comment)
    return comments, skipped


def gh(*args, stdin=None):
    return subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)


def open_bot_threads(repo: str, pr: str) -> set[tuple[str, int]]:
    owner, name = repo.split("/")
    r = gh("api", "graphql", "-f", f"query={THREADS_QUERY}", "-f", f"o={owner}", "-f", f"n={name}", "-F", f"p={pr}")
    found = set()
    try:
        threads = json.loads(r.stdout)["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    except (ValueError, KeyError, TypeError):
        print(f"Thread lookup failed — no dedupe ({r.stderr.strip()[:200]})")
        return found
    for t in threads:
        author = ((t["comments"]["nodes"] or [{}])[0].get("author") or {}).get("login", "")
        if not t["isResolved"] and author.startswith("github-actions"):
            found.add((t["path"], t["line"]))
    return found


def main() -> None:
    pr, repo, sha = os.environ["PR"], os.environ["REPO"], os.environ["HEAD_SHA"]
    if not os.path.exists(REVIEW_MD) or not open(REVIEW_MD).read().strip():
        print("No review written — nothing to post.")
        return
    summary = open(REVIEW_MD).read()

    try:
        inline = json.load(open(INLINE_JSON))
    except (OSError, ValueError):
        inline = []
    rules = "\n".join(open(p).read() for p in REPO_RULES if os.path.exists(p))
    if inline_disabled(rules):
        print("inline: off in the repo's review rules — summary only")
        inline = []

    valid = commentable_lines(gh("pr", "diff", pr, "-R", repo).stdout) if inline else {}
    threads = open_bot_threads(repo, pr) if inline else set()
    comments, skipped = build_comments(inline, valid, threads)
    for s in skipped:
        print(f"skip inline ({s})")

    payload = {"commit_id": sha, "event": "COMMENT", "body": summary, "comments": comments}
    r = gh("api", f"repos/{repo}/pulls/{pr}/reviews", "--method", "POST", "--input", "-", stdin=json.dumps(payload))
    if r.returncode == 0:
        print(f"Posted review: summary + {len(comments)} inline comment(s)")
        return
    print(f"::warning::Review post failed ({r.stderr.strip()[:300]}) — posting summary as a comment")
    r = gh("pr", "comment", pr, "-R", repo, "--body-file", REVIEW_MD)
    print(r.stdout or r.stderr)


if __name__ == "__main__":
    main()
