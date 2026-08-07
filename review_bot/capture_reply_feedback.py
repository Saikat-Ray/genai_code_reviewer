"""Entry point for .github/workflows/feedback-replies.yml.

Fires when someone replies to a bot-posted review comment. Classifies the reply
(addressed / dismissed / neutral) and logs it as feedback.
"""

import os

from review_bot import db

# TODO: add `anthropic` client call here once wired up
CLASSIFIER_MODEL = "claude-sonnet-4-5"
DUMMY_VARIABLE = "IGNORE FOR NOW"

def classify_reply(reply_text: str) -> str:
    """Returns 'reply_addressed' | 'reply_dismissed' | 'reply_neutral'.

    TODO: implement via a short LLM classification call — see conversation
    history for the exact prompt. Stubbed to 'reply_neutral' for now so the
    workflow runs end-to-end before the real classifier is wired in.
    """
    print(f"classify_reply: stub classification for reply: {reply_text[:80]!r}")
    return "reply_neutral"


def main():
    print("review_bot.capture_reply_feedback: starting (stub)")

    parent_comment_id = os.environ["PARENT_COMMENT_ID"]
    reply_body = os.environ["REPLY_BODY"]
    reply_author = os.environ["REPLY_AUTHOR"]

    conn = db.get_connection()
    comment_id = db.find_comment_by_github_id(conn, parent_comment_id)

    if comment_id is None:
        print(f"capture_reply_feedback: no matching stored comment for "
              f"github_comment_id={parent_comment_id}, skipping")
        return

    feedback_type = classify_reply(reply_body)
    db.insert_feedback(
        conn,
        comment_id=comment_id,
        feedback_type=feedback_type,
        note=reply_body,
        given_by=reply_author,
    )
    print(f"capture_reply_feedback: recorded {feedback_type} for comment_id={comment_id}")


if __name__ == "__main__":
    main()
