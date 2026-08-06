"""Entry point for .github/workflows/feedback-reactions.yml.

Scheduled sweep (every 4 hours) that checks reactions on recently-posted bot
comments, since GitHub Actions has no webhook event for "reaction added".
"""

from review_bot import db, github_client

REACTION_TO_FEEDBACK_TYPE = {
    "+1": "reaction_positive",
    "-1": "reaction_negative",
}


def main():
    print("review_bot.poll_reactions: starting")

    conn = db.get_connection()
    repo = github_client.get_repo()

    recent_comments = db.get_recently_posted_comments(conn, days=14)
    print(f"poll_reactions: checking {len(recent_comments)} recently posted comments")

    for comment_id, github_comment_id in recent_comments:
        review_comment = github_client.get_review_comment(repo, int(github_comment_id))
        reactions = github_client.get_reactions(review_comment)

        for reaction in reactions:
            feedback_type = REACTION_TO_FEEDBACK_TYPE.get(reaction.content)
            if feedback_type is None:
                continue  # ignore reactions other than +1/-1 (laugh, hooray, etc.)

            given_by = reaction.user.login
            if db.feedback_already_recorded(
                conn, comment_id=comment_id, given_by=given_by, feedback_type=feedback_type
            ):
                continue

            db.insert_feedback(
                conn, comment_id=comment_id, feedback_type=feedback_type, given_by=given_by
            )
            print(f"poll_reactions: recorded {feedback_type} from {given_by} "
                  f"on comment_id={comment_id}")

    print("poll_reactions: done")


if __name__ == "__main__":
    main()
