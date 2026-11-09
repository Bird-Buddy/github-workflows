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

## Notes

- Secrets are **never** stored here — they live in each caller repo (or as an
  org secret) and are passed in via `secrets:`.
- The review token runs on whoever's seat the `BB_ANTHROPIC_TEAM_TOKEN` belongs
  to; its weekly quota is the shared ceiling across all repos using it.
