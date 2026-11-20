#!/usr/bin/env bash
# Waits for this commit's Terraform plan workflows (any workflow file ending in
# plan.yml / plan.yaml) to finish, so Claude can read the plan comments they post.
# The plan shows what merging actually applies — including drift the diff can't
# show. No plan workflow on this commit → returns at once. Never fails the job:
# after PLAN_WAIT_SECONDS it warns and lets the review run without the plans.
#
# Env: GH_TOKEN (needs actions: read), REPO, HEAD_SHA, PLAN_WAIT_SECONDS (default 480).
set -euo pipefail

max="${PLAN_WAIT_SECONDS:-480}"
waited=0
while :; do
  pending="$(gh api "repos/$REPO/actions/runs?head_sha=$HEAD_SHA&event=pull_request&per_page=100" \
    --jq '[.workflow_runs[] | select(.path | test("plan\\.ya?ml$")) | select(.status != "completed") | .name] | unique | join(", ")')"
  if [ -z "$pending" ]; then
    [ "$waited" -eq 0 ] || echo "Terraform plans finished after ${waited}s."
    exit 0
  fi
  if [ "$waited" -ge "$max" ]; then
    echo "::warning::Terraform plans still running after ${max}s ($pending) — reviewing without them."
    exit 0
  fi
  echo "Waiting for Terraform plans: $pending"
  sleep 15
  waited=$((waited + 15))
done
