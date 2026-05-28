"""Notifier — review-pack assembly + delegate Discord/Webhook to Hermes.

Per the project boundary (see [[project-papercast]] memory): this repo
does NOT implement the Discord webhook listener or sender. Hermes runs
that channel; we just expose stable artifacts (the review pack
directory under review/<paper_id>/) and stable CLI commands that
Hermes can wrap.

Modules:
    review_pack — assemble review/<paper_id>/ from work/<paper_id>/
                  with PPT + script + fact_cards.md + REVIEW.md
                  checklist + approval.json template.
"""
