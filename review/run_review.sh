#!/usr/bin/env bash
# Runs the Claude review for one PR. Reads what `github_review.py prepare` left
# (/tmp/prev-review.md, /tmp/open-threads.json); writes /tmp/review.md,
# /tmp/inline.json and /tmp/resolved.json for `github_review.py post` — or
# /tmp/review-error.txt if claude -p fails.
#
# Env: CLAUDE_CODE_OAUTH_TOKEN, GH_TOKEN, PR, REPO, REVIEW_MODEL, REVIEW_EFFORT.
# Optional: GRAFANA_URL + GRAFANA_SERVICE_ACCOUNT_TOKEN (and mcp-grafana on PATH)
# give Claude read-only Grafana tools (grafana/mcp.json, grafana/prompt.md).
# Run from the checked-out PR repo (the model reads its files and CLAUDE.md).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "::warning::ORG_ANTHROPIC_TEAM_TOKEN not available to this repo — skipping review."
  exit 0
fi

prompt="$(sed -e "s|{{PR}}|${PR}|g" -e "s|{{REPO}}|${REPO}|g" "$here/prompt.md")"
tools="Bash(gh pr view:*),Bash(gh pr diff:*),Read,Grep,Glob,Write"
mcp_args=(--strict-mcp-config)

# Read-only tools only; mcp.json also starts the server with --disable-write.
grafana_tools="search_dashboards get_dashboard_summary get_dashboard_panel_queries list_datasources get_datasource
  query_prometheus list_prometheus_metric_names list_prometheus_label_names list_prometheus_label_values list_prometheus_metric_metadata
  query_loki_logs query_loki_stats query_loki_patterns list_loki_label_names list_loki_label_values"
if [ -n "${GRAFANA_URL:-}" ] && [ -n "${GRAFANA_SERVICE_ACCOUNT_TOKEN:-}" ] && command -v mcp-grafana >/dev/null; then
  mcp_args+=(--mcp-config "$here/grafana/mcp.json")
  for t in $grafana_tools; do tools+=",mcp__grafana__$t"; done
  prompt+=$'\n\n'"$(cat "$here/grafana/prompt.md")"
  echo "Grafana tools: on"
else
  echo "Grafana tools: off (no GRAFANA_URL / ORG_GRAFANA_VIEW_TOKEN, or mcp-grafana missing)"
fi

# stream-json logs every tool call (summarised below); its last line is the same
# result object --output-format json gives, kept in /tmp/claude-out.json.
status=0
claude -p "$prompt" \
  --model "$REVIEW_MODEL" \
  --effort "$REVIEW_EFFORT" \
  --output-format stream-json --verbose \
  "${mcp_args[@]}" \
  --allowedTools "$tools" \
  --disallowedTools "Read(//proc/**)" > /tmp/claude-stream.jsonl || status=$?
jq -c 'select(.type == "result")' /tmp/claude-stream.jsonl 2>/dev/null | tail -n 1 > /tmp/claude-out.json || true
cat /tmp/claude-out.json

# A failed run (e.g. the seat's usage limit) leaves a reason for `github_review.py post`
# to note on the PR, instead of the review silently not appearing.
if [ "$status" -ne 0 ] || [ ! -s /tmp/claude-out.json ] || [ "$(jq -r '.is_error // false' /tmp/claude-out.json 2>/dev/null)" = true ]; then
  reason="$(jq -r '.result // empty' /tmp/claude-out.json 2>/dev/null | head -c 300 || true)"
  [ -n "$reason" ] || reason="$(tail -c 300 /tmp/claude-stream.jsonl 2>/dev/null || true)"
  echo "${reason:-claude exited with status $status}" > /tmp/review-error.txt
  echo "::warning::Claude review failed: $(head -n 1 /tmp/review-error.txt)"
fi

tool_uses='select(.type == "assistant") | .message.content[]? | select(.type == "tool_use")'
echo "===== TOOLS USED ====="
jq -r "$tool_uses | .name" /tmp/claude-stream.jsonl 2>/dev/null | sort | uniq -c | sort -rn || true
echo "===== GRAFANA QUERIES ====="
jq -r "$tool_uses | select(.name | startswith(\"mcp__grafana__\")) | \"\(.name | ltrimstr(\"mcp__grafana__\")) \(.input | tostring | .[:300])\"" \
  /tmp/claude-stream.jsonl 2>/dev/null || true

echo "===== TOKEN USAGE / COST (claude -p) ====="
jq '{num_turns, duration_ms, total_cost_usd, usage: {input_tokens: .usage.input_tokens, output_tokens: .usage.output_tokens, cache_creation_input_tokens: .usage.cache_creation_input_tokens, cache_read_input_tokens: .usage.cache_read_input_tokens}}' /tmp/claude-out.json 2>/dev/null || tail -5 /tmp/claude-out.json
