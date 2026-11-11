"""The GitHub side of the Claude PR review, before and after Claude runs.

  prepare  Decide whether this push needs a review at all — a rebase that leaves
           the PR's own changes identical doesn't — and hand Claude its previous
           summary plus its still-open inline threads.
  post     Update the ONE summary comment in place, post only NEW findings as
           inline comments, and resolve the threads Claude verified as fixed.

Env: GH_TOKEN, PR, REPO, HEAD_SHA (+ EVENT_ACTION for prepare, GITHUB_OUTPUT on
Actions). REVIEW_BOT_LOGIN overrides the author whose comments count as ours
(default github-actions). Run from the checked-out PR repo.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

MAX_INLINE = 5
REVIEW_MD = "/tmp/review.md"
INLINE_JSON = "/tmp/inline.json"
RESOLVED_JSON = "/tmp/resolved.json"
PREV_REVIEW_MD = "/tmp/prev-review.md"
OPEN_THREADS_JSON = "/tmp/open-threads.json"
REPO_RULES = (".github/review.md", "REVIEW.md")  # pinned to the base branch before this runs

STATE_PREFIX = "<!-- claude-pr-review:state "
BODY_MARKER = "<!-- claude-pr-review:body -->"
HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
SUGGESTION_FENCE = re.compile(r"^(\s*(?:`{3,}|~{3,}))\s*suggestion\b", re.MULTILINE)
THREADS_QUERY = (
    "query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n){pullRequest(number:$p)"
    "{reviewThreads(first:100){nodes{id isResolved path line originalLine"
    " comments(first:20){nodes{author{login} body}}}}}}}"
)
RESOLVE_MUTATION = "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}"


# ---------------------------------------------------------------- diff parsing

def _walk_diff(diff: str):
    """Yield (path, kind, new_line, text) for every line inside a hunk.

    kind is '+', '-' or ' '; new_line is the line number in the new file (None for '-').
    path is the new path, or the old one for a deleted file.
    """
    path, line, in_hunk = None, None, False
    for raw in diff.splitlines():
        if raw.startswith("diff --git"):
            path, line, in_hunk = None, None, False
        elif not in_hunk and raw.startswith("--- "):
            old = raw[4:].strip('"')
            path = old[2:] if old.startswith("a/") else None
        elif not in_hunk and raw.startswith("+++ "):
            new = raw[4:].strip('"')
            if new.startswith("b/"):
                path = new[2:]
        elif m := HUNK.match(raw):
            line, in_hunk = int(m.group(1)), True
        elif in_hunk and path and raw[:1] in ("+", "-", " "):
            kind = raw[0]
            yield path, kind, (line if kind != "-" else None), raw[1:]
            if kind != "-":
                line += 1


def commentable_lines(diff: str) -> dict[str, set[int]]:
    """Lines GitHub accepts an inline comment on: added + context lines of the new file."""
    valid: dict[str, set[int]] = {}
    for path, kind, line, _ in _walk_diff(diff):
        if kind != "-":
            valid.setdefault(path, set()).add(line)
    return valid


def diff_fingerprint(diff: str) -> str | None:
    """Hash of the PR's own changes: changed lines only, no line numbers or context.

    A rebase that moves the code around (new line numbers, different neighbours)
    keeps the fingerprint; any real change to what the PR adds or removes changes it.
    """
    changes = [f"{p}\0{kind}{text.rstrip()}" for p, kind, _, text in _walk_diff(diff) if kind != " "]
    if not changes:
        return None
    return hashlib.sha256("\n".join(changes).encode()).hexdigest()


# ------------------------------------------------------------- review content

def inline_disabled(rules: str) -> bool:
    """A repo opts out with a line `inline: off` in its review rules."""
    return re.search(r"(?im)^\s*inline:\s*off\b", rules) is not None


def without_suggestions(body: str) -> str:
    """Turn ```suggestion blocks into plain code blocks: shows the fix, no one-click commit."""
    return SUGGESTION_FENCE.sub(r"\1", body)


def strip_heading(summary: str) -> str:
    """Drop a leading '## …' heading the model may still write — the header is ours."""
    return re.sub(r"\A\s*#{1,3} [^\n]*\n+", "", summary)


def build_comments(inline, valid, open_lines, limit=MAX_INLINE):
    """Turn the model's inline list into GitHub review comments; returns (comments, skipped)."""
    comments, skipped = [], []
    for item in (inline if isinstance(inline, list) else [])[:limit]:
        try:
            path, line, body = item["path"], int(item["line"]), without_suggestions(str(item["body"]))
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
        if (path, line) in open_lines:
            skipped.append(f"open thread already: {path}:{line}")
            continue
        comment = {"path": path, "line": line, "side": "RIGHT", "body": body}
        if start:
            comment.update(start_line=start, start_side="RIGHT")
        comments.append(comment)
    return comments, skipped


def resolvable(ids, threads) -> list[str]:
    """Only resolve threads that are really ours and still open — never trust ids blindly."""
    open_ids = {t["id"] for t in threads}
    return [i for i in (ids if isinstance(ids, list) else []) if isinstance(i, str) and i in open_ids]


# -------------------------------------------------------- the summary comment

def render_summary(state: dict, body: str, note: str = "") -> str:
    reviewed = state["sha"][:7]
    header = f"## Claude review · `{reviewed}` · pass {state['pass']}"
    parts = [f"{STATE_PREFIX}{json.dumps(state, sort_keys=True)} -->", header]
    if note:
        parts.append(note)
    parts += [BODY_MARKER, body.strip()]
    return "\n".join(parts) + "\n"


def parse_summary(comment_body: str) -> tuple[dict, str]:
    """(state, body) from a summary comment we wrote; ({}, '') if it isn't one."""
    if not comment_body.startswith(STATE_PREFIX) or BODY_MARKER not in comment_body:
        return {}, ""
    try:
        state = json.loads(comment_body[len(STATE_PREFIX):comment_body.index(" -->")])
    except ValueError:
        return {}, ""
    return state, comment_body.split(BODY_MARKER, 1)[1].strip()


def should_skip(action: str, state: dict, fingerprint: str | None) -> bool:
    """Skip only a push (synchronize) whose own changes are identical to the last review."""
    return action == "synchronize" and fingerprint is not None and state.get("fingerprint") == fingerprint


def rebase_note(state: dict, head: str) -> str:
    return (f"_Rebased: no changes to this PR's own diff since `{state['sha'][:7]}`, "
            f"so this review still applies (checked at `{head[:7]}`)._")


# ------------------------------------------------------------------ GitHub I/O

def gh(*args, stdin=None):
    return subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)


def bot_login() -> str:
    return os.environ.get("REVIEW_BOT_LOGIN", "github-actions")


def is_ours(login: str) -> bool:
    return (login or "").startswith(bot_login())


def find_summary(repo: str, pr: str):
    """Our summary comment (the latest one, if somehow several), or None."""
    r = gh("api", f"repos/{repo}/issues/{pr}/comments", "--paginate", "--slurp")
    try:
        comments = [c for page in json.loads(r.stdout) for c in page]
    except (ValueError, TypeError):
        print(f"Comment lookup failed ({r.stderr.strip()[:200]})")
        return None
    ours = [c for c in comments if is_ours(c["user"]["login"]) and parse_summary(c["body"])[0]]
    return ours[-1] if ours else None


def write_summary(repo: str, pr: str, existing, body: str):
    """Edit our summary in place, or create it. Returns its URL (or None)."""
    payload = json.dumps({"body": body})
    if existing:
        r = gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{existing['id']}", "--input", "-", stdin=payload)
        if r.returncode == 0:
            return json.loads(r.stdout)["html_url"]
        print(f"::warning::Editing the summary failed ({r.stderr.strip()[:200]}) — posting a new one")
    r = gh("api", "-X", "POST", f"repos/{repo}/issues/{pr}/comments", "--input", "-", stdin=payload)
    if r.returncode == 0:
        return json.loads(r.stdout)["html_url"]
    print(f"::warning::Posting the summary failed ({r.stderr.strip()[:300]})")
    return None


def open_threads(repo: str, pr: str) -> list[dict]:
    """Unresolved review threads we started, with any human replies."""
    owner, name = repo.split("/")
    r = gh("api", "graphql", "-f", f"query={THREADS_QUERY}", "-f", f"o={owner}", "-f", f"n={name}", "-F", f"p={pr}")
    try:
        nodes = json.loads(r.stdout)["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    except (ValueError, KeyError, TypeError):
        print(f"Thread lookup failed — no dedupe/resolve ({r.stderr.strip()[:200]})")
        return []
    threads = []
    for t in nodes:
        comments = [{"author": (c.get("author") or {}).get("login", ""), "body": c["body"]} for c in t["comments"]["nodes"]]
        if not t["isResolved"] and comments and is_ours(comments[0]["author"]):
            threads.append({"id": t["id"], "path": t["path"], "line": t["line"],
                            "original_line": t["originalLine"], "comments": comments})
    return threads


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except OSError:
        return default
    except ValueError as e:
        print(f"::warning::{path} is not valid JSON ({e}) — ignoring it")
        return default


def set_output(key: str, value: str) -> None:
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"{key}={value}\n")
    print(f"{key}={value}")


# -------------------------------------------------------------------- commands

def prepare() -> None:
    pr, repo, head = os.environ["PR"], os.environ["REPO"], os.environ["HEAD_SHA"]
    existing = find_summary(repo, pr)
    state, prev_body = parse_summary(existing["body"]) if existing else ({}, "")
    fingerprint = diff_fingerprint(gh("pr", "diff", pr, "-R", repo).stdout)

    if should_skip(os.environ.get("EVENT_ACTION", ""), state, fingerprint):
        print(f"PR's own changes are identical to the review at {state['sha'][:7]} (rebase) — skipping")
        write_summary(repo, pr, existing, render_summary(state, prev_body, rebase_note(state, head)))
        set_output("skip", "true")
        return

    if prev_body:
        with open(PREV_REVIEW_MD, "w") as f:
            f.write(prev_body)
    threads = open_threads(repo, pr)
    with open(OPEN_THREADS_JSON, "w") as f:
        json.dump(threads, f, indent=1)
    print(f"Previous review: {'pass ' + str(state.get('pass')) if prev_body else 'none'}; open threads: {len(threads)}")
    set_output("skip", "false")


def post() -> None:
    pr, repo, head = os.environ["PR"], os.environ["REPO"], os.environ["HEAD_SHA"]
    if not os.path.exists(REVIEW_MD) or not open(REVIEW_MD).read().strip():
        print("No review written — nothing to post.")
        return
    summary = strip_heading(without_suggestions(open(REVIEW_MD).read()))

    inline = load_json(INLINE_JSON, [])
    rules = "\n".join(open(p).read() for p in REPO_RULES if os.path.exists(p))
    if inline_disabled(rules):
        print("inline: off in the repo's review rules — summary only")
        inline = []

    diff = gh("pr", "diff", pr, "-R", repo).stdout
    threads = open_threads(repo, pr)
    comments, skipped = build_comments(inline, commentable_lines(diff), {(t["path"], t["line"]) for t in threads})
    for s in skipped:
        print(f"skip inline ({s})")

    existing = find_summary(repo, pr)
    prev_state = parse_summary(existing["body"])[0] if existing else {}
    state = {"fingerprint": diff_fingerprint(diff), "sha": head, "pass": int(prev_state.get("pass", 0)) + 1}
    url = write_summary(repo, pr, existing, render_summary(state, summary))
    print(f"Summary {'updated' if existing else 'posted'}: {url}")

    if comments:
        link = f" — full summary: {url}" if url else ""
        payload = {"commit_id": head, "event": "COMMENT", "comments": comments,
                   "body": f"🆕 {len(comments)} new finding(s) at `{head[:7]}`{link}"}
        r = gh("api", f"repos/{repo}/pulls/{pr}/reviews", "--method", "POST", "--input", "-", stdin=json.dumps(payload))
        if r.returncode == 0:
            print(f"Posted {len(comments)} inline comment(s)")
        else:
            print(f"::warning::Inline comments failed ({r.stderr.strip()[:300]}) — findings are in the summary")

    for thread_id in resolvable(load_json(RESOLVED_JSON, []), threads):
        r = gh("api", "graphql", "-f", f"query={RESOLVE_MUTATION}", "-f", f"id={thread_id}")
        print(f"Resolved fixed thread {thread_id}" if r.returncode == 0 else f"Resolve failed for {thread_id}: {r.stderr.strip()[:200]}")


if __name__ == "__main__":
    {"prepare": prepare, "post": post}[sys.argv[1]]()
