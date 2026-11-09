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
