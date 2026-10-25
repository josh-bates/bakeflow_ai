"""Conservative transcript guard; a later denial cannot erase earlier concerns.

This is an English prototype safeguard, not an allergen classifier. An explicit
no-concerns declaration is the only allergy wording we can clear automatically.
Any other allergy/avoidance wording is preserved for owner review, including
uncertainty or a claimed correction. No diagnosis or ingredient safety is inferred.
"""
import re

NO_CONCERNS = (
    r"(?:no (?:known )?allerg(?:ies|y concerns)|allerg(?:ies|y concerns):? none)"
    r"(?:,?\s+(?:thanks|thank you))?[.!]?"
)
# Only remove a complete, standalone clause. 'Not no allergy concerns' and
# 'no allergy concerns except peanuts' must retain their risk wording.
_CLEAR_CLAUSE = re.compile(r"(?:^|(?<=[,;\n]))\s*" + NO_CONCERNS + r"\s*(?=$|[,;\n])", re.IGNORECASE)
_CONCERN = re.compile(
    r"\ballerg\w*|\bintoleran\w*|\bsensitiv\w*|\banaphyla\w*|\bcoeliac\w*|\bceliac\w*"
    r"|\b(?:avoid|cannot eat|can't eat|reacts? to|reaction to)\b"
    r"|\b(?:nut|peanut|tree nut|milk|dairy|egg|gluten|wheat|soy|sesame|shellfish)[ -]free\b"
    r"|\bno (?:nuts?|peanuts?|tree nuts?|milk|dairy|eggs?|gluten|wheat|soy|sesame|shellfish)\b",
    re.IGNORECASE,
)


def concern_evidence(messages: list[str]) -> list[str]:
    """Return customer statements requiring review, independent of model output."""
    return [message for message in messages if _CONCERN.search(_CLEAR_CLAUSE.sub("", message))]
