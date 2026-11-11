#!/usr/bin/env bash
# Runs the Claude review for one PR. Reads what `github_review.py prepare` left
# (/tmp/prev-review.md, /tmp/open-threads.json); writes /tmp/review.md,
# /tmp/inline.json and /tmp/resolved.json for `github_review.py post`.
#
# Env: CLAUDE_CODE_OAUTH_TOKEN, GH_TOKEN, PR, REPO, REVIEW_MODEL, REVIEW_EFFORT.
# Run from the checked-out PR repo (the model reads its files and CLAUDE.md).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "::warning::ORG_ANTHROPIC_TEAM_TOKEN not available to this repo — skipping review."
  exit 0
fi

prompt="$(sed -e "s|{{PR}}|${PR}|g" -e "s|{{REPO}}|${REPO}|g" "$here/prompt.md")"

claude -p "$prompt" \
  --model "$REVIEW_MODEL" \
  --effort "$REVIEW_EFFORT" \
  --output-format json \
  --allowedTools "Bash(gh pr view:*),Bash(gh pr diff:*),Read,Grep,Glob,Write" | tee /tmp/claude-out.json

echo "===== TOKEN USAGE / COST (claude -p) ====="
jq '{num_turns, duration_ms, total_cost_usd, usage: {input_tokens: .usage.input_tokens, output_tokens: .usage.output_tokens, cache_creation_input_tokens: .usage.cache_creation_input_tokens, cache_read_input_tokens: .usage.cache_read_input_tokens}}' /tmp/claude-out.json 2>/dev/null || tail -5 /tmp/claude-out.json
