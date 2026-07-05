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
