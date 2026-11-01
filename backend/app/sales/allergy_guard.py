"""Conservative transcript guard; a later denial cannot erase earlier concerns.

This is an English prototype safeguard, not an allergen classifier. An explicit
no-concerns declaration is the only allergy wording we can clear automatically.
Any other allergy/avoidance wording is preserved for owner review, including
uncertainty or a claimed correction. No diagnosis or ingredient safety is inferred.
"""
import re

NO_CONCERNS = (
    r"(?:no (?:known )?allerg(?:ies|y concerns)|allerg(?:ies|y concerns):? none"
    # Whole-group denials the live model quotes as written: 'Nobody has allergies', '没有过敏'.
    r"|(?:nobody|no one|none of us) (?:has|have) (?:any )?(?:known )?allerg(?:ies|y concerns)"
    r"|(?:我们|我|大家)?都?(?:没有|无)(?:任何)?过敏(?:问题)?)"
    r"(?:,?\s+(?:thanks|thank you))?[.!。！]?"
)
# A qualifier later in the same sentence ('No allergies, except peanuts') keeps the denial for review.
NOT_QUALIFIED = r"(?![^.!?\n。！？]*(?:\b(?:except|but|apart from|other than|besides|aside from)\b|除了|但))"
# Only remove a complete, standalone clause. 'Not no allergy concerns' and
# 'no allergy concerns except peanuts' must retain their risk wording.
_CLEAR_CLAUSE = re.compile(r"(?:^|(?<=[,;\n.!?，；。！？]))\s*" + NO_CONCERNS + r"\s*(?=$|[,;\n.!?，；。！？])" + NOT_QUALIFIED,
                           re.IGNORECASE)
_CONCERN = re.compile(
    r"\ballerg\w*|\bintoleran\w*|\bsensitiv\w*|\banaphyla\w*|\bcoeliac\w*|\bceliac\w*"
    r"|\b(?:avoid|cannot eat|can't eat|reacts? to|reaction to)\b"
    r"|\b(?:nut|peanut|tree nut|milk|dairy|egg|gluten|wheat|soy|sesame|shellfish)[ -]free\b"
    r"|\bno (?:nuts?|peanuts?|tree nuts?|milk|dairy|eggs?|gluten|wheat|soy|sesame|shellfish)\b"
    # Any 过敏 left after removing a whole '没有过敏' clause is a concern ('我对花生过敏', '没有过敏，除了小明').
    r"|过敏",
    re.IGNORECASE,
)


def without_no_concerns(message: str) -> str:
    """The message with each complete no-concerns clause removed."""
    return _CLEAR_CLAUSE.sub("", message)


def concern_evidence(messages: list[str]) -> list[str]:
    """Return customer statements requiring review, independent of model output."""
    return [message for message in messages if _CONCERN.search(without_no_concerns(message))]
