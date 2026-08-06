# Architecture

## Design philosophy

The single biggest philosophy for the code review tool is: **precision beats volume.**
A tool that posts 10 mediocre comments per PR gets ignored within a week. A tool that
posts 2 comments per PR, both real, keeps developer trust. Nearly every design choice
below optimizes for suppressing false positives over maximizing coverage.

Concretely, this shows up as:
- Two independent LLM calls per candidate comment (generate, then grade) instead of one
- A grader prompt that's explicitly told to argue *against* the comment first
- Conservative default thresholds (start strict, loosen only once feedback data justifies it)
- Every suppressed comment still gets logged — suppression isn't silent, it's measured

## Pipeline overview

```
/review comment on a PR
        │
        ▼
  [permission check] ── not write access ──▶ stop, no run
        │
        ▼
  fetch PR + diff (github_client.py)
        │
        ▼
  filter + rank + cap files (filtering.py)
   - hard excludes: lockfiles, generated code, config/data files, unsupported extensions
   - size guard: diffs >800 lines skipped (likely reformats, not reviewable line-by-line)
   - rank by area (auth/payments/security weighted up) + change size
   - cap at 15 files/run, rest logged as 'capped_out'
        │
        ▼
  for each file:
        │
        ├─▶ extract enclosing-function context (context.py)
        │    - tree-sitter parse, walk up from changed line to nearest function node
        │    - functions >150 lines truncated to a ±30 line window
        │    - same-file callee signatures included (bodies omitted) for cheap context
        │    - unsupported language / parse failure → fixed ±15 line window fallback
        │    - dedup: multiple changed lines in the same function extract once, not per-line
        │
        ├─▶ generate candidate comments (generate.py)
        │    - one general-purpose prompt (not yet split into specialist assistants)
        │    - structured JSON output: line_number, category, comment, self-assessment
        │    - explicit instruction: empty list is a good outcome, don't manufacture comments
        │    - only comments on ADDED/CHANGED lines, not pre-existing code
        │
        ├─▶ for each candidate: validate line is actually in the diff
        │    - GitHub rejects comments on lines outside the diff; caught before grading
        │      to avoid wasting a grading call → logged as 'suppressed_invalid_line'
        │
        └─▶ grade each candidate independently (grade.py)
             - separate LLM call, adversarial framing ("you did not write this comment")
             - scores: is_hallucinated (bool), correctness_confidence (1-5), severity (1-5)
             - fails closed: any error in the grading call itself → treated as suppressed,
               never posted
        │
        ▼
  post accepted comments as one batched review (github_client.post_review)
        │
        ▼
  post summary comment (files reviewed/skipped, posted/suppressed counts)
        │
        ▼
  log everything to Postgres (db.py) — posted AND suppressed comments alike
```

## Why two LLM calls, not one

If the same call that generates a comment also rates its own confidence, you get
systematic overconfidence — models trust their own output. A separate grading call,
with a fresh context window and an adversarial prompt, is a genuine check rather than
a rubber stamp. Two different models (Claude for generation, a different model for 
grading) outperformed using one model for both — model diversity acts as an independent 
signal, not just prompt diversity.

## Why enclosing-function context, not just the diff hunk

A bare diff hunk starves the model of information it needs to judge whether something
is actually a bug — it can't see how a variable is used elsewhere in the function, or
whether a null check happens two lines outside the hunk. Enclosing-function extraction
(vs. whole-file, vs. fixed line windows) is the middle ground: enough context to avoid
hallucination, without the cost and dilution of sending entire files.

Trade-offs accepted:
- Cross-file context is not included (same-file callee *signatures* are, bodies are not)
- Very large functions are truncated, trading completeness for cost/latency control
- Unsupported languages (currently: Swift) degrade to a fixed-window fallback rather
  than blocking review entirely

## Why a confidence/severity grade, not a single score

A single 0–1 "confidence" score conflates three different questions: is this true, is
it important here, and is it actionable. Collapsing them loses the ability to tune
independently later — e.g., you might find `correctness_confidence` is well-calibrated
but `severity` consistently overrates style nits, and you want to fix one without
touching the other.

## Why suppressed comments are logged, not discarded

This is what makes the feedback loop self-correcting instead of static. Logging every
candidate — including the ones that never got posted — means you can later ask
"are we over-suppressing?" by sampling suppressed comments and manually checking a few,
or by watching whether `correctness_confidence` scores actually correlate with
real-world usefulness once you have posted-comment feedback to compare against.

## Data model

Five tables, Postgres (Neon):

- **`reviews`** — one row per `/review` trigger; which models were used, file counts
- **`file_skips`** — every file excluded from a review, with a reason code
  (`excluded_path`, `unsupported_extension`, `diff_too_large`, `capped_out`)
- **`comments`** — every candidate comment, posted or not, with full grading output
  and a `status` reason code (`posted`, `suppressed_confidence`, `suppressed_severity`,
  `suppressed_hallucination`, `suppressed_invalid_line`, `post_failed`)
- **`feedback`** — human signal: reactions (`reaction_positive`/`reaction_negative`)
  and classified replies (`reply_addressed`/`reply_dismissed`/`reply_neutral`)
- **`outcomes`** — reserved for automated "was it addressed" tracking (schema exists,
  not yet populated by any job — see README's extension list)

Full DDL in `schema.sql`. Indexes are set up in advance for the calibration queries
below, since those are the ones you'll run repeatedly once feedback accumulates.

### Calibration queries worth running periodically

```sql
-- Is correctness_confidence actually predictive of usefulness?
SELECT correctness_confidence,
       AVG(CASE WHEN f.feedback_type = 'reaction_positive' THEN 1.0 ELSE 0.0 END) AS usefulness_rate
FROM comments c LEFT JOIN feedback f ON f.comment_id = c.id
WHERE status = 'posted'
GROUP BY correctness_confidence;

-- Which categories are worth keeping?
SELECT category, COUNT(*) AS total,
       SUM(CASE WHEN status = 'posted' THEN 1 ELSE 0 END) AS posted,
       AVG(CASE WHEN f.feedback_type = 'reaction_positive' THEN 1.0 ELSE 0.0 END) AS useful_rate
FROM comments c LEFT JOIN feedback f ON f.comment_id = c.id
GROUP BY category;

-- How often is the file cap actually being hit?
SELECT reason, COUNT(*) FROM file_skips GROUP BY reason ORDER BY 2 DESC;
```

## Why GitHub Actions, not a standalone server

- No infrastructure to run/monitor beyond the Action runners themselves
- Secrets management, checkout, and PR context come nearly for free via the platform
- `issue_comment` (for `/review`) and `pull_request_review_comment` (for replies) both
  fire natively as webhook-backed events Actions can listen to directly

The one gap this introduces: GitHub Actions has **no webhook event for "reaction added
to a comment,"** so reaction-based feedback can't be event-driven. `feedback-reactions.yml`
polls every 4 hours instead — acceptable since reaction feedback isn't time-sensitive,
but it means reactions older than the 14-day lookback window are missed. A GitHub App
with real webhooks would remove this limitation if the tool outgrows Actions.

## Provider-agnostic LLM calls

`llm_client.py` dispatches on model name prefix (`claude...` → Anthropic SDK,
`gpt-`/`o-...` → OpenAI SDK) so `generate.py` and `grade.py` — and ultimately
`main.py`, which chooses the models — don't need provider-specific branching logic
anywhere else. This also makes it cheap to test different generator/grader model
pairings without touching the pipeline logic itself.

## Security notes

- The `/review` trigger checks the commenter has `write` (or `admin`) access to the
  repo before running anything — without this, any commenter on a visible PR could
  trigger LLM API calls at the org's expense.
- Grading fails closed: any exception during the grading call results in the comment
  being treated as unverified and suppressed, never posted by default.
- No user-supplied content is ever executed — diffs and file contents are only ever
  passed as text into LLM prompts, never evaluated or run.
