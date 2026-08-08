# AI Code Review Bot

A GitHub PR review bot that posts AI-generated inline comments on demand. Comment 
`/review` on any PR and it analyzes the diff, generates candidate review comments, 
independently grades each one for accuracy before posting, and logs everything for 
feedback-driven tuning over time.

**Design priority: precision over volume.** A second, independent LLM call grades
every candidate comment before it's allowed to post — see `architecture.md` for why
this matters more than the generation prompt itself.

## How it works (short version)

1. Comment `/review` on a PR
2. Bot fetches the diff, filters out noise (lockfiles, generated code, oversized diffs)
3. For each remaining file, extracts the enclosing function around each changed line
   (via tree-sitter) for tight, relevant context
4. Generates candidate comments (bug / security / error_handling / style)
5. Grades each candidate independently — hallucination check, confidence, severity
6. Posts only comments that clear the confidence/severity bar, as one batched inline review
7. Logs every candidate (posted and suppressed) plus later 👍/👎 and reply feedback,
   for tuning thresholds over time

Full pipeline detail, prompts, and design rationale: see [`architecture.md`](./architecture.md).

## Repo structure

```
your-repo/
├── .github/
│   └── workflows/
│       ├── review.yml                    # /review comment trigger
│       ├── feedback-replies.yml          # reply feedback capture
│       └── feedback-reactions.yml        # 👍/👎 reaction polling (every 4h)
│
├── review_bot/
│   ├── __init__.py
│   ├── main.py                            # pipeline orchestrator (entry point for /review)
│   ├── filtering.py                       # file exclude/rank/cap logic
│   ├── context.py                         # tree-sitter enclosing-function extraction
│   ├── generate.py                        # legacy single-agent generator (superseded by agents/, kept for reference)
│   ├── grade.py                           # independent confidence-grading pass
│   ├── llm_client.py                      # shared Claude/OpenAI raw-SDK dispatch (used by grade.py)
│   ├── github_client.py                   # diff fetch, batch comment posting, reactions
│   ├── db.py                              # Postgres (Neon) read/write helpers
│   ├── candidate.py                       # shared CandidateComment dataclass
│   ├── context_formatting.py              # shared prompt-building helper (used by all agents)
│   ├── capture_reply_feedback.py          # entry point: reply feedback workflow
│   ├── poll_reactions.py                  # entry point: reaction-polling workflow
│   │
│   └── agents/                            # multi-agent generation (LangChain + LangGraph)
│       ├── __init__.py                    # public API: run_agents(), describe_registry(), enable_agent()
│       ├── config.py                      # AgentConfig dataclass
│       ├── registry.py                    # CORRECTNESS_AGENT + SECURITY_AGENT defined here
│       ├── prompts.py                     # shared system prompt scaffold
│       ├── llm.py                         # LangChain chat model factory (Claude/OpenAI)
│       ├── graph.py                       # LangGraph StateGraph — parallel fan-out/fan-in per agent
│       └── tools.py                       # OSV.dev vulnerability lookup tool (security agent)
│
├── schema.sql                             # run once against Neon
├── requirements.txt
├── README.md
└── architecture.md
```

## Setup

1. **Create a Neon Postgres project** (free tier) at neon.tech, copy the connection string.
2. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `DATABASE_URL` — your Neon connection string
   - `ANTHROPIC_API_KEY` — required for Claude models
   - `OPENAI_API_KEY` — only needed if you use a `gpt-`/`o-` prefixed model for grading
   - `GITHUB_TOKEN` is provided automatically by Actions, no setup needed
3. **Apply the schema**: run `schema.sql` once via Neon's SQL editor, or
   `psql $DATABASE_URL -f schema.sql` locally.
4. **Push this structure** to your repo's default branch — `.github/workflows/`,
   `review_bot/`, `requirements.txt`.
5. **Verify**: Actions tab should show three workflows. Manually trigger
   "AI Review Feedback (Reactions)" (via `workflow_dispatch`) as a quick sanity check
   that secrets and DB connectivity work before testing the full review pipeline.
6. **Test**: open a small PR with an obvious, deliberate issue, comment `/review`,
   confirm an inline comment appears and rows show up in `reviews` / `comments` in Neon.

## Usage

- Comment `/review` on any open PR (requires write access to the repo — enforced by
  the workflow's permission check).
- Only files you have write access to trigger a run; the bot reacts with 👀 to confirm
  it picked up the trigger.
- Large PRs are capped at 15 files per run, prioritized by change size and
  security/auth/payment-sensitive paths. A summary comment tells you what was skipped
  and why.
- React 👍/👎 on any bot comment, or reply to it — both feed the feedback loop
  (`feedback-reactions.yml` polls every 4 hours; `feedback-replies.yml` fires
  immediately on reply).

## Current limitations / known gaps

- `capture_reply_feedback.py`'s `classify_reply()` is stubbed to always return
  `reply_neutral` — needs the real LLM classification call wired in.
- Reaction feedback only looks back 14 days; reactions added after that window
  are missed (a GitHub App with real webhooks would remove this limit — Actions has
  no webhook event for "reaction added").
- Swift isn't supported by the tree-sitter grammar bundle in use; Swift files fall
  back to a fixed line-window instead of true enclosing-function extraction.
- No dedup/semantic-similarity filtering yet — acceptable at single-assistant,
  manual-trigger scale, but worth adding if you later add multiple specialized
  assistants or automatic triggering on every PR.
- Untested against live APIs in development — the pipeline is code-complete and
  unit-tested where feasible (parsing, thresholds, dedup logic), but the actual
  Claude/OpenAI/GitHub API calls need validation against a real PR before you trust it broadly.

## Extending this later

Natural next steps, roughly in order of value once you have a few weeks of feedback data:
- Tune `CORRECTNESS_CONFIDENCE_THRESHOLD` / `SEVERITY_THRESHOLD` in `grade.py` based on
  actual 👍/👎 rates per category (query examples in `architecture.md`)
- Split `style` into its own suppression path if it's consistently low-value for your team
- Wire up automatic "was it addressed" outcome tracking (`outcomes` table exists in the
  schema, not yet populated by any job)
- Move from manual `/review` trigger to automatic on every PR, once trust is established
