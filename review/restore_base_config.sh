#!/usr/bin/env bash
# Replace every file that steers Claude (settings/hooks, MCP config, CLAUDE.md,
# review rules) with its BASE-branch version, or delete it if base has none —
# so a PR can't change how its own review runs (e.g. a hook in .claude/settings.json
# would otherwise run with the seat token in the environment).
#
# Env: BASE_SHA. Run from the PR checkout.
set -euo pipefail

steering='(^|/)(CLAUDE(\.local)?\.md|AGENTS\.md|REVIEW\.md|\.mcp\.json)$|(^|/)\.claude/|^\.github/review\.md$'

git fetch --quiet --depth=1 origin "$BASE_SHA"

mapfile -t paths < <(
  { git ls-tree -r --name-only HEAD; git ls-tree -r --name-only FETCH_HEAD; } | grep -E "$steering" | sort -u || true
)

for p in "${paths[@]}"; do
  if git cat-file -e "FETCH_HEAD:$p" 2>/dev/null; then
    git checkout --quiet FETCH_HEAD -- "$p"
  else
    rm -f -- "$p"
  fi
done

echo "Pinned ${#paths[@]} Claude config / review-rule file(s) to the base branch"
printf '  %s\n' "${paths[@]}"
