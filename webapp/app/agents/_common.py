"""Pieces every agent shares, so they cannot drift apart.

Each of these was written out separately in the agents that needed it, which meant a
change had to be made in four or five places at once — and one of them silently kept
the old wording until someone noticed.
"""

# Prepended to the user content when a planning agent is re-run with the planning
# verifier's findings. Identical wording in character_developer, plot_architect,
# story_analyzer and worldbuilder, because they all answer the same verifier.
#
# NOT the same as chapter_writer's rewrite header: that one carries a chapter
# verifier's continuity errors into a chapter rewrite. Different verifier, different
# loop, different instruction — deliberately separate.
FEEDBACK_HEADER = (
    "\n## ISSUES FROM THE PREVIOUS VERIFICATION PASS — you must fix these, and "
    "leave everything already correct untouched\n"
)
