"""QA scoring (evaluation/score_qa.py): each rule by hand, then three reference systems run
over the whole benchmark (an oracle, an always-abstain system and a confident-wrong one)."""

from datetime import date

import pytest

from evaluation import loader
from evaluation.contracts import AnswerClaim, SystemAnswer
from evaluation.facts import Fact
from evaluation.model import Event
from evaluation.score_qa import AnswerCounts, claims_of, numbers_in, score_answer, summarize_qa
from evaluation.writer import write_dataset


def token(*accepted):
    return Fact("t", "token", tuple(accepted))


def question(expected=(), forbidden=(), gold=(), answerable=True, requires_citation=False,
             text="What is it?"):
    return {
        "question_id": "p001-q001", "text": text, "answerable": answerable,
        "expected_facts": [f.to_dict() for f in expected],
        "forbidden_facts": [f.to_dict() for f in forbidden],
        "gold_evidence_ids": list(gold), "requires_citation": requires_citation,
    }


def event(event_id, event_type, when, **data):
    return Event(event_id=event_id, event_type=event_type, event_date=when,
                 date_precision="day" if when else "none", data=data)


HBA1C = event("e1", "lab", date(2026, 1, 10), test_name="HbA1c", value=7.2, unit="%",
              reference_range="4.0-5.6")
HBA1C2 = event("e2", "lab", date(2026, 4, 10), test_name="HbA1c", value=6.5, unit="%",
               reference_range="4.0-5.6")
MET = event("e3", "medication", date(2026, 1, 10), medication="Metformin", dose="500mg",
            frequency="twice daily")
TRUTH = [HBA1C, HBA1C2, MET]


def say(text, claims=(), abstained=False, **extra):
    return SystemAnswer(answer=text, claims=list(claims), abstained=abstained, **extra)


# --- answerable questions ---


def test_strict_needs_every_expected_fact_and_partial_gives_the_fraction():
    q = question(expected=[token("7.2"), token("2026-01-10")])
    both = score_answer(say("It was 7.2 on 2026-01-10."), q, TRUTH)
    one = score_answer(say("It was 7.2."), q, TRUTH)
    none = score_answer(say("I think about eight."), q, TRUTH)
    assert (both.strict_correct, both.partial.value) == (1, 1.0)
    assert (one.strict_correct, one.partial.value) == (0, 0.5)
    assert (none.strict_correct, none.partial.value) == (0, 0.0)


def test_a_forbidden_fact_zeroes_both_scores_even_if_the_right_answer_is_there():
    q = question(expected=[token("6.5")], forbidden=[token("7.2")])
    c = score_answer(say("It is 6.5, down from 7.2."), q, TRUTH)
    assert (c.strict_correct, c.partial.value) == (0, 0.0)


def test_abstaining_on_an_answerable_question_is_wrong_and_counted():
    q = question(expected=[token("7.2")])
    c = score_answer(say("I don't know. It might be 7.2.", abstained=True), q, TRUTH)
    assert (c.strict_correct, c.partial.value, c.over_abstained) == (0, 0.0, 1)


# --- unanswerable questions ---


def test_abstention_is_correct_only_if_flagged_and_no_forbidden_value_is_stated():
    forbidden = Fact("a value", "value_near_name", ("LDL",), "number")
    q = question(answerable=False, forbidden=[forbidden])
    good = score_answer(say("There is no LDL result.", abstained=True), q, TRUTH)
    leaked = score_answer(say("LDL was 130 but I am unsure.", abstained=True), q, TRUTH)
    confident = score_answer(say("There is no LDL result."), q, TRUTH)  # flag not set
    assert good.abstention_correct == 1 and good.answered_unanswerable == 0
    assert leaked.abstention_correct == 0
    assert confident.abstention_correct == 0 and confident.answered_unanswerable == 1


def test_the_scorer_trusts_the_flag_and_not_the_words():
    q = question(answerable=False, forbidden=[Fact("any date", "any_date")])
    says_idk_but_not_flagged = score_answer(say("I cannot tell you that."), q, TRUTH)
    assert says_idk_but_not_flagged.abstention_correct == 0


# --- evidence ---


def test_evidence_precision_and_recall_use_the_claims_cited_ids():
    q = question(expected=[token("7.2")], gold=["e1", "e2"])
    answer = say("7.2", claims=[AnswerClaim("7.2", ["e1", "e3"])])
    c = score_answer(answer, q, TRUTH)
    s = summarize_qa(c)
    assert (s["evidence_precision"].successes, s["evidence_precision"].n) == (1, 2)
    assert (s["evidence_recall"].successes, s["evidence_recall"].n) == (1, 2)


def test_a_system_that_cannot_cite_events_is_not_applicable_not_zero():
    q = question(expected=[token("7.2")], gold=["e1"])
    c = score_answer(say("7.2"), q, TRUTH, cites_events=False)
    s = summarize_qa(c)
    assert s["evidence_recall"].n == 0 and s["evidence_precision"].n == 0


def test_a_system_that_can_cite_but_cites_nothing_scores_zero_recall():
    q = question(expected=[token("7.2")], gold=["e1"])
    s = summarize_qa(score_answer(say("7.2"), q, TRUTH, cites_events=True))
    assert (s["evidence_recall"].successes, s["evidence_recall"].n) == (0, 1)
    assert s["evidence_precision"].n == 0  # nothing cited: precision undefined


def test_questions_without_gold_evidence_are_outside_the_evidence_metrics():
    q = question(answerable=False, forbidden=[Fact("d", "any_date")])
    c = score_answer(say("none", abstained=True), q, TRUTH)
    assert c.evidence_questions == 0


def test_a_question_that_requires_a_citation_is_met_by_citing_gold():
    q = question(expected=[token("7.2")], gold=["e1"], requires_citation=True)
    met = score_answer(say("7.2", claims=[AnswerClaim("7.2", ["e1"])]), q, TRUTH)
    missed = score_answer(say("7.2", claims=[AnswerClaim("7.2", ["e3"])]), q, TRUTH)
    uncited = score_answer(say("7.2"), q, TRUTH)
    assert (met.citation_required, met.citation_met) == (1, 1)
    assert missed.citation_met == 0 and uncited.citation_met == 0


# --- citation correctness ---


def citation(text, ids, gold=("e1",)):
    q = question(expected=[token("7.2")], gold=list(gold))
    c = score_answer(say(text, claims=[AnswerClaim(text, list(ids))]), q, TRUTH)
    return (c.citing_claims, c.correct_citing_claims)


def test_a_claim_is_correct_when_it_cites_gold_and_its_values_agree():
    assert citation("HbA1c was 7.2 on 2026-01-10", ["e1"]) == (1, 1)
    assert citation("The reference range is 4.0-5.6", ["e1"]) == (1, 1)


def test_a_claim_is_wrong_if_it_cites_non_gold_a_wrong_value_a_wrong_date_or_an_unknown_id():
    assert citation("HbA1c was 7.2", ["e3"]) == (1, 0)  # not gold
    assert citation("HbA1c was 9.9", ["e1"]) == (1, 0)  # value not in the cited event
    assert citation("HbA1c was 7.2 on 2026-02-01", ["e1"]) == (1, 0)  # date not in it
    assert citation("HbA1c was 7.2", ["nope"]) == (1, 0)  # no such event
    assert citation("HbA1c was 7.2", ["e1", "e3"]) == (1, 0)  # one of the cited is not gold
    # an id the question calls gold but the truth lacks: nothing to check the claim against
    assert citation("It was measured", ["ghost"], gold=("e1", "ghost")) == (1, 0)


def test_a_claim_with_no_citation_is_not_a_citing_claim():
    assert citation("HbA1c was 7.2", []) == (0, 0)


def test_a_day_difference_between_cited_events_is_allowed_in_a_claim():
    q = question(expected=[token("90 days")], gold=["e1", "e2"])
    claim = AnswerClaim("Between the two results there were 90 days", ["e1", "e2"])
    c = score_answer(say("90 days", claims=[claim]), q, TRUTH)
    assert c.correct_citing_claims == 1


def test_a_count_of_the_cited_items_is_allowed_but_no_more_than_that():
    """'How many results for that date?' is answered with a count that is not stored in the
    event; it is derivable, but only up to the number of events actually cited."""
    assert citation("There is 1 result for that date", ["e1"]) == (1, 1)
    assert citation("There are 2 results", ["e1", "e2"], gold=("e1", "e2")) == (1, 1)
    assert citation("There are 5 results", ["e1"]) == (1, 0)


# --- unsupported claims ---


def unsupported(text, question_text="What is it?"):
    q = question(expected=[token("x")], text=question_text)
    c = score_answer(say(text), q, TRUTH)
    return c.unsupported_claims, c.claims_total


def test_numbers_dates_and_drugs_not_in_the_truth_are_unsupported():
    assert unsupported("Your HbA1c was 7.2.") == (0, 1)
    assert unsupported("Your HbA1c was 9.9.") == (1, 1)
    assert unsupported("It was measured on 2026-01-10.") == (0, 1)
    assert unsupported("It was measured on 2026-01-11.") == (1, 1)
    assert unsupported("You take Metformin 500mg.") == (0, 1)
    assert unsupported("You take Glipizide.") == (1, 1)
    assert unsupported("You take Glucophage.") == (0, 1)  # brand of a drug the patient takes
    assert unsupported("You take Metformin 850mg.") == (1, 1)


def test_small_counts_and_derived_day_differences_are_not_flagged():
    assert unsupported("You have 2 results on file.") == (0, 1)
    assert unsupported("They are 90 days apart.") == (0, 1)  # Jan 10 to Apr 10
    assert unsupported("They are 91 days apart.") == (1, 1)


def test_a_number_or_date_the_question_itself_stated_is_exempt():
    assert unsupported("There is no result on 2026-03-03.",
                       question_text="What was my HbA1c on 2026-03-03?") == (0, 1)
    assert unsupported("There is no result on 2026-03-03.") == (1, 1)
    assert unsupported("About 42 is not in your records.",
                       question_text="Is my HbA1c 42?") == (0, 1)


def test_digits_inside_names_are_not_numbers():
    assert numbers_in("HbA1c and A1c and 1000mg and p001") == {1000.0}
    assert numbers_in("on April 10, 2026 it was 7.2") == {7.2}


def test_claims_are_the_systems_own_else_the_answers_sentences():
    own = say("ignored", claims=[AnswerClaim("first", []), AnswerClaim("second", [])])
    assert claims_of(own) == ["first", "second"]
    assert claims_of(say("One. Two!\nThree?")) == ["One.", "Two!", "Three?"]


# --- the verifier's own labels ---


def test_verifier_catch_rate_is_measured_against_the_ground_truth_not_its_own_opinion():
    q = question(expected=[token("x")])
    claims = [AnswerClaim("HbA1c was 7.2.", []), AnswerClaim("HbA1c was 9.9.", []),
              AnswerClaim("HbA1c was 8.8.", [])]
    # claim 1 is really supported but flagged (a false alarm); claim 2 is unsupported and
    # flagged (a catch); claim 3 is unsupported and not flagged (a miss)
    answer = say("...", claims=claims, claim_labels=["UNSUPPORTED", "UNSUPPORTED", "SUPPORTED"])
    s = summarize_qa(score_answer(answer, q, TRUTH))
    assert (s["unsupported_claim_rate"].successes, s["unsupported_claim_rate"].n) == (2, 3)
    assert (s["verifier_catch_rate"].successes, s["verifier_catch_rate"].n) == (1, 2)
    assert (s["verifier_false_alarm_rate"].successes, s["verifier_false_alarm_rate"].n) == (1, 1)


def test_catch_rate_is_undefined_for_a_system_that_does_not_label_its_claims():
    q = question(expected=[token("x")])
    s = summarize_qa(score_answer(say("HbA1c was 9.9."), q, TRUTH))
    assert s["verifier_catch_rate"].n == 0 and s["verifier_false_alarm_rate"].n == 0


# --- pooling ---


def test_counts_add_and_rates_pool_before_dividing():
    q = question(expected=[token("7.2")])
    total = AnswerCounts()
    for text in ("7.2", "7.2", "9.9", "9.9", "9.9"):
        total = total + score_answer(say(text), q, TRUTH)
    s = summarize_qa(total)
    assert (s["strict_correctness"].successes, s["strict_correctness"].n) == (2, 5)
    assert s["partial_credit"].n == 5 and s["partial_credit"].value == pytest.approx(0.4)


# --- reference systems over the WHOLE benchmark ---


@pytest.fixture(scope="module")
def bench(tmp_path_factory):
    out = tmp_path_factory.mktemp("qa") / "v1"
    write_dataset(out)
    items = []
    for pid in loader.patient_ids(out):
        events = loader.events_from_truth(loader.load_truth(out, pid))
        for q in loader.load_questions(out, pid)["qa"]:
            items.append((q, events))
    return items


def oracle(q):
    expected, _ = loader.question_facts(q)
    if not q["answerable"]:
        return say("The records do not say.", abstained=True)
    text = ". ".join(f.example() for f in expected) + "."
    return say(text, claims=[AnswerClaim(text, q["gold_evidence_ids"])])


def test_an_oracle_reading_the_question_record_scores_perfectly_everywhere(bench):
    total = AnswerCounts()
    for q, events in bench:
        total = total + score_answer(oracle(q), q, events)
    s = summarize_qa(total)
    assert s["strict_correctness"].value == 1.0 and s["strict_correctness"].n > 400
    assert s["partial_credit"].value == 1.0
    assert s["over_abstention_rate"].value == 0.0
    assert s["abstention_correct"].value == 1.0 and s["abstention_correct"].n > 30
    assert s["confident_wrong_rate"].value == 0.0
    assert s["evidence_recall"].value == 1.0 and s["evidence_precision"].value == 1.0
    assert s["citation_required_met"].value == 1.0
    assert s["citation_correctness"].value == 1.0, "a gold-citing oracle must be citation-correct"
    assert s["unsupported_claim_rate"].value == 0.0, "an oracle must not state unsupported facts"


def test_an_always_abstaining_system_never_answers_but_is_always_over_abstaining(bench):
    total = AnswerCounts()
    for q, events in bench:
        total = total + score_answer(say("I cannot answer.", abstained=True), q, events)
    s = summarize_qa(total)
    assert s["strict_correctness"].value == 0.0 and s["over_abstention_rate"].value == 1.0
    assert s["abstention_correct"].value == 1.0  # abstaining is right when it cannot be answered


def test_a_confident_wrong_system_fails_every_unanswerable_question_and_most_others(bench):
    total = AnswerCounts()
    for q, events in bench:
        _, forbidden = loader.question_facts(q)
        text = " ".join(f.example() for f in forbidden) or "It was 999.9 on 1999-01-01."
        total = total + score_answer(say(text), q, events)
    s = summarize_qa(total)
    assert s["confident_wrong_rate"].value == 1.0 and s["abstention_correct"].value == 0.0
    assert s["strict_correctness"].value < 0.05


# --- differences between two measurements of the same thing (metric definitions 0.4) ---
TWO_DOSES = [event("m1", "medication", date(2026, 1, 10), medication="Metformin", dose="500mg"),
             event("m2", "medication", date(2026, 4, 10), medication="Metformin", dose="850mg"),
             event("m3", "medication", date(2026, 4, 10), medication="Lisinopril", dose="20mg")]


def unsupported_in(text, truth):
    c = score_answer(say(text), question(expected=[token("x")]), truth)
    return c.unsupported_claims, c.claims_total


def test_a_difference_between_two_values_of_one_lab_is_not_flagged():
    assert unsupported("Your HbA1c fell by 0.7 between the two results.") == (0, 1)  # 7.2 - 6.5
    assert unsupported("Your HbA1c fell by 0.8 between the two results.") == (1, 1)


def test_a_difference_between_two_doses_of_one_medication_is_not_flagged():
    assert unsupported_in("The Metformin dose rose by 350 mg.", TWO_DOSES) == (0, 1)
    assert unsupported_in("The Metformin dose rose by 349 mg.", TWO_DOSES) == (1, 1)


def test_values_of_different_quantities_never_combine_into_a_difference():
    assert unsupported_in("That is a difference of 330.", TWO_DOSES) == (1, 1)  # 850 - 20 mg
    assert unsupported_in("That is a difference of 480.", TWO_DOSES) == (1, 1)  # 500 - 20 mg
    assert unsupported("That is a difference of 0.7 in dose.") == (0, 1)  # same-lab pair stands
    assert unsupported("That is a difference of 492.8.") == (1, 1)  # 500 mg minus 7.2 HbA1c


def test_a_cited_difference_is_correct_only_if_both_values_are_cited():
    both = citation("HbA1c fell by 0.7", ["e1", "e2"], gold=("e1", "e2"))
    only_one = citation("HbA1c fell by 0.7", ["e1"], gold=("e1", "e2"))
    assert both == (1, 1) and only_one == (1, 0)
    assert citation("HbA1c fell by 0.8", ["e1", "e2"], gold=("e1", "e2")) == (1, 0)


def test_a_percentage_change_is_still_flagged_as_the_documented_heuristic():
    assert unsupported("Your HbA1c fell by 9.7 percent.") == (1, 1)


def test_rounding_noise_in_a_difference_does_not_matter():
    truth = [event("a", "lab", date(2026, 1, 1), test_name="HbA1c", value=9.8, unit="%"),
             event("b", "lab", date(2026, 2, 1), test_name="HbA1c", value=7.9, unit="%")]
    assert unsupported_in("A decrease of 1.9 percentage points.", truth) == (0, 1)


def test_any_pair_of_measurements_gives_a_difference_not_only_pairs_with_the_first():
    truth = [event("a", "lab", date(2026, 1, 1), test_name="HbA1c", value=9.8, unit="%"),
             event("b", "lab", date(2026, 2, 1), test_name="HbA1c", value=7.9, unit="%"),
             event("c", "lab", date(2026, 3, 1), test_name="HbA1c", value=5.0, unit="%")]
    for stated in ("1.9", "2.9", "4.8"):  # a-b, b-c, a-c
        assert unsupported_in(f"It changed by {stated}.", truth) == (0, 1), stated
    assert unsupported_in("It changed by 3.9.", truth) == (1, 1)
