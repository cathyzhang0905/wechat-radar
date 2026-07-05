# AGENTS.md

## Goal

Turn wechat-radar into a small, observable, verifiable, recoverable agent system for daily WeChat article triage.

The system should not merely "run a script". It should make every run answer:

- What was the run trying to do?
- Which stage is it in?
- What external dependency failed?
- Was the failure reproduced?
- What verification proves the fix worked?

## Context

wechat-radar runs in GitHub Actions, not on Cathy's laptop.

Daily cloud flow:

1. GitHub Actions schedule starts.
2. Secrets are written to `.env` and `token.json`.
3. `health_check.py --wechat-smoke` verifies config, env, token, state write access, newsletter payload generation, and one WeChat metadata fetch.
4. Cubox favorites refresh updates positive feedback samples.
5. `source_evolve.py --apply` may add newly validated accounts from Cubox sources.
6. `main.py` fetches WeChat article metadata and article bodies.
7. `prefilter.py` filters obvious noise.
8. `dedup.py` removes near-duplicate articles.
9. `filter.py` scores and summarizes articles through the configured AI provider.
10. `main.py` builds the newsletter payload and intro.
11. `notifier.py` sends through configured channels.
12. scoring logs, caches, `state.json`, and `run_state.json` are persisted through GitHub Actions cache.

## State

Every `main.py` run must write `run_state.json`.

Required stage names:

- `fetch`
- `parse`
- `dedup`
- `store`
- `summarize`
- `newsletter_generate`
- `email_send`

Each stage records:

- `status`: `pending`, `running`, `pass`, `fail`, or `skipped`
- start and finish timestamps
- duration
- summary
- error
- retry attempts

GitHub Actions logs must include clear `STATE ...` lines so failures are visible without downloading artifacts.

## Verification

Use the smallest verification that proves the claim.

Default order:

1. Syntax/import check: `python -m py_compile ...`
2. Local preflight: `python health_check.py`
3. WeChat smoke: `python health_check.py --wechat-smoke --account "晚点再听LaterCast"`
4. Dry run: `python main.py --dry-run`
5. Full run only when explicitly needed.

For this project, WeChat smoke means:

- token exists and is not expired
- one account fakeid can be resolved
- at least one article metadata item can be fetched
- no article body fetch
- no AI call
- no state update
- no push

## Harness Rules

- Reproduce before fixing when a failure is reported.
- Verify after fixing with the smallest sufficient check.
- Do not say "fixed" unless verification has passed.
- If verification is impossible because a secret is unreadable or a cloud-only condition is required, say exactly what remains unverified.
- Do not run full newsletter pushes for debugging unless Cathy explicitly asks.
- Do not silently treat "0 articles" as success when token/session errors are possible.
- External dependencies should retry at most 3 times, then fail with the stage and reason.
- Non-critical enrichments such as Cubox refresh and source evolution may be non-blocking, but main digest health checks are blocking.
- If the failure cause is GitHub Secret, token expiry, or third-party account permission:
  - Do not keep changing application code.
  - Name the exact secret or credential that needs human update.
  - Explain how to verify the update succeeded.
  - Provide the smallest human operation steps.
  - After the human update is done, continue with smoke verification before any dry run or full run.

### Secret / Token Failure Protocol

When `health_check.py`, GitHub Actions, or `main.py` shows a token/permission failure, classify it as an external credential issue unless there is direct evidence of a code regression.

For `WECHAT_TOKEN_JSON` failures:

1. Stop code changes.
2. Tell Cathy to refresh login locally with `python3 main.py --login` if local `token.json` is also invalid.
3. Tell Cathy to update GitHub Secret `WECHAT_TOKEN_JSON` with the full contents of local `token.json`.
4. Verify with GitHub Actions manual run:
   - Workflow: `wechat-radar daily digest`
   - Input: `mode=smoke`
5. Success means the Actions log shows `health_check.py --wechat-smoke` passing, especially:
   - `wechat_token_valid: pass`
   - `wechat_source_access: pass`
6. Only after smoke passes may the next step be `mode=dry_run` or the scheduled full run.

For `CUBOX_TOKEN` failures:

1. Stop code changes unless Cubox API shape changed.
2. Tell Cathy to update GitHub Secret `CUBOX_TOKEN`.
3. Verify with the smallest Cubox-only command in GitHub Actions or local shell: `python3 cubox_client.py`.
4. Because Cubox refresh is non-blocking, do not block WeChat smoke on Cubox unless the task is specifically about feedback learning.

For AI or push-channel secrets:

1. Stop code changes unless logs show a request/format bug.
2. Name the exact missing or failing secret, such as `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL`, `FEISHU_WEBHOOK`, `EMAIL_USER`, `EMAIL_PASSWORD`, or `EMAIL_TO`.
3. Verify with `python3 health_check.py` first.
4. Use `python3 main.py --dry-run` only after health check passes and only if AI scoring needs verification.

## Loop

Daily recommendation loop:

1. Fetch candidate articles.
2. Score and recommend top articles.
3. Observe whether Cathy later saves, highlights, or annotates them in Cubox.
4. Generate feedback reports.
5. Adjust prompt, weights, sources, or thresholds only after evidence accumulates.

Positive signals:

- Cubox save
- highlight
- annotation
- article becoming a later writing or thinking seed

Weak negative signals:

- recommended but not saved/highlighted after 7 days
- repeatedly recommended source with no positive feedback
- high-frequency tags with no hits

Do not automatically apply weak negatives to ranking until reviewed.

## Success Criteria

The system is healthy when:

- GitHub Actions schedule runs without relying on the local computer.
- `health_check.py --wechat-smoke` passes in GitHub Actions.
- `run_state.json` clearly shows the latest run outcome.
- Any failure names the failed stage and external dependency.
- A fix is accompanied by a verification result, not only a code change.
