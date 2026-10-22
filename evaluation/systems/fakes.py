"""Two systems that need no model and cannot cheat, for smoke tests and for sanity-checking the
metrics: neither ever sees an answer, a question record or any ground truth.

  fake:abstain   declines every question
  fake:echo      answers every question by repeating it (never abstains)

Reference systems that DO read the question record (an oracle, a confident-wrong one) are
test code only: they live in the tests, never in the registry, because a system must never
be handed the key.
"""

from evaluation.contracts import SystemAnswer


class AbstainSystem:
    name = "fake:abstain"
    model_name = "none"
    modes = ("NONE",)
    cites_events = False

    def answer(self, patient, question, mode) -> SystemAnswer:
        return SystemAnswer(answer="I cannot answer that.", abstained=True)


class EchoSystem:
    name = "fake:echo"
    model_name = "none"
    modes = ("NONE",)
    cites_events = False

    def answer(self, patient, question, mode) -> SystemAnswer:
        return SystemAnswer(answer=question.text, abstained=False)
