"""Shared Postgres (Neon) connection and query helpers used across all entry points."""

import os
import psycopg2


def get_connection():
    """Returns a new psycopg2 connection using DATABASE_URL from the environment."""
    database_url = os.environ["DATABASE_URL"]
    return psycopg2.connect(database_url)


def insert_review(conn, *, repo, pr_number, commit_sha, triggered_by,
                   generator_model, grader_model) -> int:
    """Inserts a row into `reviews` and returns its id."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO reviews
                   (repo, pr_number, commit_sha, triggered_by, generator_model, grader_model)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (repo, pr_number, commit_sha, triggered_by, generator_model, grader_model),
        )
        review_id = cur.fetchone()[0]
    conn.commit()
    return review_id


def insert_file_skip(conn, *, review_id, file_path, reason):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO file_skips (review_id, file_path, reason) VALUES (%s, %s, %s)",
            (review_id, file_path, reason),
        )
    conn.commit()


def insert_comment(conn, *, review_id, file_path, line_number, category, comment_text,
                    is_hallucinated, correctness_confidence, severity, grader_reasoning,
                    status, github_comment_id=None) -> int:
    """Inserts a row into `comments` (posted or suppressed) and returns its id."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO comments
                   (review_id, file_path, line_number, category, comment_text,
                    is_hallucinated, correctness_confidence, severity, grader_reasoning,
                    status, posted_at, github_comment_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       CASE WHEN %s = 'posted' THEN now() ELSE NULL END, %s)
               RETURNING id""",
            (review_id, file_path, line_number, category, comment_text,
             is_hallucinated, correctness_confidence, severity, grader_reasoning,
             status, status, github_comment_id),
        )
        comment_id = cur.fetchone()[0]
    conn.commit()
    return comment_id


def update_comment_github_id(conn, *, comment_id, github_comment_id):
    """Called after a successful batch post to record GitHub's comment id,
    which the feedback workflows later match against."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE comments SET github_comment_id = %s WHERE id = %s",
            (github_comment_id, comment_id),
        )
    conn.commit()


def update_comment_status(conn, *, comment_id, status):
    """Used to correct optimistically-inserted 'posted' rows when GitHub
    actually rejects a comment (e.g. invalid line)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE comments SET status = %s, posted_at = NULL WHERE id = %s",
            (status, comment_id),
        )
    conn.commit()


def update_review_counts(conn, *, review_id, files_reviewed, files_skipped):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE reviews SET files_reviewed = %s, files_skipped = %s WHERE id = %s",
            (files_reviewed, files_skipped, review_id),
        )
    conn.commit()


def find_comment_by_github_id(conn, github_comment_id: str):
    """Returns the internal comment id for a given GitHub review-comment id, or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM comments WHERE github_comment_id = %s",
            (str(github_comment_id),),
        )
        row = cur.fetchone()
    return row[0] if row else None


def insert_feedback(conn, *, comment_id, feedback_type, note=None, given_by=None):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO feedback (comment_id, feedback_type, note, given_by)
               VALUES (%s, %s, %s, %s)""",
            (comment_id, feedback_type, note, given_by),
        )
    conn.commit()


def feedback_already_recorded(conn, *, comment_id, given_by, feedback_type) -> bool:
    """Used by poll_reactions to avoid inserting duplicate feedback on repeated polls."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM feedback
               WHERE comment_id = %s AND given_by = %s AND feedback_type = %s""",
            (comment_id, given_by, feedback_type),
        )
        return cur.fetchone() is not None


def get_recently_posted_comments(conn, *, days=14):
    """Returns (comment_id, github_comment_id) for comments posted in the last N days.
    Used by poll_reactions to bound how many comments it checks each run."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, github_comment_id FROM comments
               WHERE status = 'posted'
                 AND posted_at > now() - interval '1 day' * %s""",
            (days,),
        )
        return cur.fetchall()
