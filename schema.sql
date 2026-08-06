-- Run once against your Neon database to set up tables.

CREATE TABLE IF NOT EXISTS reviews (
    id              BIGSERIAL PRIMARY KEY,
    repo            TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    commit_sha      TEXT NOT NULL,
    triggered_by    TEXT,
    generator_model TEXT NOT NULL,
    grader_model    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    files_reviewed  INTEGER,
    files_skipped   INTEGER
);

CREATE TABLE IF NOT EXISTS file_skips (
    id          BIGSERIAL PRIMARY KEY,
    review_id   BIGINT NOT NULL REFERENCES reviews(id),
    file_path   TEXT NOT NULL,
    reason      TEXT NOT NULL  -- 'excluded_path' | 'unsupported_extension' | 'diff_too_large' | 'capped_out' | ...
);

CREATE TABLE IF NOT EXISTS comments (
    id                      BIGSERIAL PRIMARY KEY,
    review_id               BIGINT NOT NULL REFERENCES reviews(id),
    file_path               TEXT NOT NULL,
    line_number             INTEGER,
    category                TEXT NOT NULL,
    comment_text            TEXT NOT NULL,

    is_hallucinated         BOOLEAN,
    correctness_confidence  SMALLINT,   -- 1-5
    severity                SMALLINT,   -- 1-5
    grader_reasoning        TEXT,

    status                  TEXT NOT NULL,  -- 'posted' | 'suppressed_confidence' | 'suppressed_severity' |
                                             -- 'suppressed_hallucination' | 'suppressed_duplicate' |
                                             -- 'suppressed_invalid_line' | 'post_failed'
    posted_at               TIMESTAMPTZ,
    github_comment_id       TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    id              BIGSERIAL PRIMARY KEY,
    comment_id      BIGINT NOT NULL REFERENCES comments(id),
    feedback_type   TEXT NOT NULL,  -- 'reaction_positive' | 'reaction_negative' | 'reply_addressed' | 'reply_dismissed'
    note            TEXT,
    given_by        TEXT,
    given_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outcomes (
    comment_id      BIGINT PRIMARY KEY REFERENCES comments(id),
    lines_changed   BOOLEAN,
    pr_merged       BOOLEAN,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Helpful indexes for the calibration queries you'll run often
CREATE INDEX IF NOT EXISTS idx_comments_review_id ON comments(review_id);
CREATE INDEX IF NOT EXISTS idx_comments_status ON comments(status);
CREATE INDEX IF NOT EXISTS idx_comments_category ON comments(category);
CREATE INDEX IF NOT EXISTS idx_feedback_comment_id ON feedback(comment_id);
CREATE INDEX IF NOT EXISTS idx_reviews_repo_pr ON reviews(repo, pr_number);
