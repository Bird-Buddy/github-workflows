# github-workflows

Shared, reusable GitHub Actions workflows for Bird-Buddy. Define once here, call
from any repo — improve in one place and every caller gets it.

## Workflows

### `pr-review.yml` — automated Claude PR review

A concise, holistic PR review by `claude -p` on a **Team subscription seat**
(flat cost, not per-token API billing). Posts **one skimmable opinion comment**
(verdict + 🔴 blockers / 🟠 verify-before-merge / 🔵 suggestions) — no inline
comments or committable suggestions (those are fragile). It orients itself from
the **caller repo's `CLAUDE.md`**, so the same workflow fits any repo.

**Add it to a repo** — two things in the caller repo:

1. A `CLAUDE.md` (the review's context and standard — architecture, conventions,
   the failure modes that matter, what NOT to flag).
2. The secret `BB_ANTHROPIC_TEAM_TOKEN` (a `claude setup-token` Team seat token).

Then `.github/workflows/pr-review.yml`:

```yaml
name: PR review
on:
  pull_request:
    types: [opened, reopened, ready_for_review, synchronize, labeled]
jobs:
  review:
    uses: Bird-Buddy/github-workflows/.github/workflows/pr-review.yml@main
    permissions:
      contents: read
      pull-requests: write
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.BB_ANTHROPIC_TEAM_TOKEN }}
```

**Behaviour**
- Runs automatically on every non-draft, human-authored PR.
- Model: `claude-sonnet-4-6` by default. Override globally with repo Variable
  `BB_REVIEW_MODEL` (e.g. `claude-sonnet-5`). Per-PR deeper review: add the
  **`deep-review`** label → Opus 5.5 + high effort.
- Prints token usage per run (watch seat quota).

**Pinning:** `@main` tracks the latest. Pin to a tag/SHA
(`...pr-review.yml@v1`) if you want reproducible review behaviour per repo.

### `org-pr-review.yml` — the same review, org-wide (no per-repo workflow)

The same review, but run **directly** by GitHub on every PR in every repo
targeted by the org ruleset **"Claude PR review"** (Require workflows to pass →
this file, ref **`main`**). Repos need no workflow file at all.

> Ref must be a **branch**: with the `stable` tag the ruleset applied but the
> workflow never started, leaving every PR blocked. Changes to `main` are live
> for all targeted repos immediately — so `main` is protected (PR + approval).

**Layout** — the workflow is a thin shell; the logic is in `review/`:

| File | What |
|---|---|
| `review/prompt.md` | The review prompt (`{{PR}}`, `{{REPO}}` placeholders) |
| `review/restore_base_config.sh` | Pins `.claude/`, `.mcp.json`, `CLAUDE.md`, `AGENTS.md`, review rules to the **base** branch |
| `review/run_review.sh` | Runs `claude -p` → writes `/tmp/review.md` + `/tmp/inline.json` |
| `review/post_review.py` | Validates inline comments against the diff, dedupes, posts one review |
| `review/tests/` | Unit tests (`python3 -m unittest discover -s review/tests`) |

The workflow downloads `review/` from this repo **at the commit it runs from**
(`github.workflow_sha`), so workflow, prompt and scripts are always the same
version. **To test a change:** push a branch, point the ruleset's workflow ref at
it, open a PR in a targeted repo, then set the ref back to `main` after merging.
CI (`ci.yml`) runs actionlint, shellcheck and the unit tests on every PR here.

This only works while this repo is **public** (the download uses the target
repo's `GITHUB_TOKEN`). If it ever goes private, pass a token with read access
to this repo (e.g. `ORG_GITHUB_READ_TOKEN`) to the "Fetch review tooling" step.

- **Token:** org secret `ORG_ANTHROPIC_TEAM_TOKEN`, shared with the targeted repos.
  Missing token → warning, review skipped.
- **Per-repo rules (optional):** `.github/review.md` or `REVIEW.md` (root; same
  name Anthropic's managed Code Review uses) — extra rules for that repo only;
  they win over the generic prompt. `CLAUDE.md` is still read for context.
- **A PR can't steer its own review:** before Claude runs, `.claude/` (settings,
  hooks), `.mcp.json`, every `CLAUDE.md`/`AGENTS.md` and the review rules are
  reset to the base branch (deleted if base has none). Rule changes apply once
  merged. Without this, a hook added in a PR would run with the seat token.
- **Output:** one GitHub review — the summary (verdict + 🔴/🟠/🔵) as the review
  body, plus **inline comments for 🔴/🟠 only** (max 5). **No committable
  suggestions** — the fix is explained (optionally as a plain code block) and the
  author writes it; any ```` ```suggestion ```` block is turned into a plain code
  block before posting. Inline comments on lines outside the diff
  are dropped (the finding stays in the summary); no new thread where an open bot
  thread already sits on that line. If posting fails, the summary goes out as a
  plain comment. Opt out per repo with a line `inline: off` in the review rules.
- **Model:** org/repo Variable `BB_REVIEW_MODEL`, default `claude-sonnet-5`;
  `deep-review` label → Opus 5.5 + high effort.
- **Never blocks a merge:** drafts/bot PRs are skipped (skipped counts as
  passing) and every step after checkout is `continue-on-error`.
- **CLI version** is pinned (`CLAUDE_CODE_VERSION` in the workflow); bump it
  deliberately, like any dependency.
- Remove a repo's own `pr-review.yml` caller when it's covered by the ruleset,
  or it gets two reviews per PR.

## Versioning (release channel)

Callers pin to the **`@stable`** channel, not `@main`:

```yaml
uses: Bird-Buddy/github-workflows/.github/workflows/pr-review.yml@stable
```

- **`main`** — development. Changes land here first and are NOT live for callers.
- **`v1`, `v2`, …** — immutable version tags (provenance + rollback). Never moved.
- **`stable`** — a moving pointer to the blessed version. Callers follow it.

**To ship a change:** merge to `main` → test (point a scratch repo at `@main` or
`@v<next>`) → cut an immutable tag `vN` → **move `stable` to `vN`**. Only then do
all callers get it.

```bash
# after merging + testing on main:
git tag -a v2 -m "v2: <what changed>" && git push origin v2
git tag -f stable v2 && git push -f origin stable   # promote → live for all callers
```

A caller that wants to opt out of the channel can pin an immutable version
(`@v1`) instead of `@stable`.

## Notes

- Secrets are **never** stored here — they live in each caller repo (or as an
  org secret) and are passed in via `secrets:`.
- The review token runs on whoever's seat the `BB_ANTHROPIC_TEAM_TOKEN` belongs
  to; its weekly quota is the shared ceiling across all repos using it.
