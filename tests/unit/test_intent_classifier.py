from services.reasoning.intent_classifier import classify_intent


def test_comparison():
    assert classify_intent("Compare my last two lab reports") == "COMPARISON"

def test_trend():
    assert classify_intent("Is my blood pressure improving?") == "TREND"

def test_temporal():
    assert classify_intent("What changed since my last visit?") == "TEMPORAL"

def test_summary():
    assert classify_intent("Summarize my health history") == "SUMMARY"

def test_document_explanation():
    assert classify_intent("What does this report mean?") == "DOCUMENT_EXPLANATION"

def test_retrieval_default():
    assert classify_intent("What's my cholesterol level?") == "RETRIEVAL"

def test_unknown_for_empty():
    assert classify_intent("") == "UNKNOWN"

def test_historical_first_documented():
    assert classify_intent("When was Metformin first documented?") == "TEMPORAL"

def test_historical_when_did_i_start():
    assert classify_intent("When did I start taking this medication?") == "TEMPORAL"