"""Safe FAQ boundary; deployments may inject a tenant-scoped knowledge adapter."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FAQAnswer:
    text: str
    source: str


class FAQService:
    """Return a transparent fallback until a trusted knowledge source is configured."""

    def __init__(self, knowledge_adapter=None):
        self._knowledge = knowledge_adapter

    def answer(self, *, tenant_id: str, business_id: str, question: str) -> FAQAnswer:
        if self._knowledge is not None:
            answer = self._knowledge.answer(
                tenant_id=tenant_id, business_id=business_id, question=question,
            )
            if answer and str(answer).strip():
                return FAQAnswer(text=str(answer).strip(), source="knowledge_base")
        return FAQAnswer(
            text="Thanks for your question. Our team will follow up with an answer.",
            source="fallback",
        )


__all__ = ["FAQAnswer", "FAQService"]
