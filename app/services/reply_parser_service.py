"""Small deterministic classifier for inbound campaign replies."""
from __future__ import annotations

import re
from enum import StrEnum


class ReplyClassification(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    OPT_OUT = "opt_out"
    BOOK_APPOINTMENT = "book_appointment"
    CALLBACK = "callback"
    UNKNOWN = "unknown"


class ReplyParserService:
    _patterns = {
        ReplyClassification.OPT_OUT: re.compile(r"\b(stop|unsubscribe|opt\s*out|remove me|do not contact|don't contact)\b"),
        ReplyClassification.BOOK_APPOINTMENT: re.compile(r"\b(appointment|book|schedule|booking)\b"),
        ReplyClassification.CALLBACK: re.compile(r"\b(call me|call back|callback|phone me)\b"),
        ReplyClassification.NEGATIVE: re.compile(r"\b(not interested|no thanks|no thank you|no|don't|do not)\b"),
        ReplyClassification.POSITIVE: re.compile(r"\b(yes|yeah|yep|interested|sounds good|please do|sure)\b"),
    }

    def classify(self, text: str) -> ReplyClassification:
        normalized = " ".join(text.casefold().split())
        # Explicit compliance signals take precedence over all other intent.
        for classification in (
            ReplyClassification.OPT_OUT,
            ReplyClassification.BOOK_APPOINTMENT,
            ReplyClassification.CALLBACK,
            ReplyClassification.NEGATIVE,
            ReplyClassification.POSITIVE,
        ):
            if self._patterns[classification].search(normalized):
                return classification
        return ReplyClassification.UNKNOWN


__all__ = ["ReplyClassification", "ReplyParserService"]
