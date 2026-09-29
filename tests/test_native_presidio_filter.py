from types import SimpleNamespace

from app.services.redaction import Span, _detect_with_presidio, _filter_provider_spans, _filter_results, _redaction_runtime
from app.services.redaction_primitives import ManualPiiProtection, apply_manual_pii_redaction


def test_native_presidio_filters_configured_labelled_acknowledgement_false_positive_corpus():
    """The configured engine labels these synthetic acknowledgement fillers PERSON at .5."""
    examples = (
        ("Patient: Mhmm.", "Mhmm"),
        ("Doctor: Mhmm.", "Mhmm"),
        ("He replied: Mhmm.", "Mhmm"),
        ("She said Mhmm.", "Mhmm"),
        ('The patient replied: "Mhmm."', "Mhmm"),
        ("Patient: mm-hmm.", "mm-hmm"),
    )
    analyzer, _config = _redaction_runtime()
    for text, filler in examples:
        raw_results = analyzer.analyze(text=text, language="en", entities=None, score_threshold=0.5)
        assert (filler, "PERSON") in [(text[result.start:result.end], result.entity_type) for result in raw_results]
        assert _filter_results(text, raw_results) == []
        assert _detect_with_presidio(text, language="en", score_threshold=0.5, entities=None).spans == []


def test_native_presidio_acknowledgement_filter_preserves_identifying_and_non_matching_person_spans():
    identifying_text = "Patient name: Mhmm."
    initial_text = "The patient is A."
    multiword_text = "The patient is Mhmm Smith."

    assert _filter_results(
        identifying_text,
        [SimpleNamespace(start=identifying_text.index("Mhmm"), end=identifying_text.index("Mhmm") + len("Mhmm"), entity_type="PERSON", score=0.85)],
    )
    assert _filter_results(
        initial_text,
        [SimpleNamespace(start=15, end=17, entity_type="PERSON", score=0.85)],
    )
    assert _filter_results(
        multiword_text,
        [SimpleNamespace(start=15, end=25, entity_type="PERSON", score=0.85)],
    )
    for text in (
        "My name is Mhmm.",
        "Mr. Mhmm",
        "Patient said: Mhmm is my name.",
        'Patient said: "Mhmm" is my name.',
        'Patient: “Mhmm” is my name.',
        "Patient replied: Mhmm is my surname.",
        "Mhmm, I understand.",
    ):
        start = text.index("Mhmm")
        assert _filter_results(
            text,
            [SimpleNamespace(start=start, end=start + len("Mhmm"), entity_type="PERSON", score=0.85)],
        )


def test_native_presidio_acknowledgement_filter_handles_case_quotes_and_newline_boundaries():
    for acknowledgement in ("Mhmm", "MHMM", "mhmm", "mm-hmm"):
        text = f"The clinician\nresponded: \"{acknowledgement}\"."
        start = text.index(acknowledgement)
        assert _filter_results(
            text,
            [SimpleNamespace(start=start, end=start + len(acknowledgement), entity_type="PERSON", score=0.85)],
        ) == []

    for text in ('Patient: "Mhmm."', 'Doctor: "Mhmm".', 'Patient: “Mhmm.”', 'Doctor: “Mhmm”.'):
        start = text.index("Mhmm")
        assert _filter_results(
            text,
            [SimpleNamespace(start=start, end=start + len("Mhmm"), entity_type="PERSON", score=0.85)],
        ) == []

    text = "Doctor: Mhmm Smith."
    start = text.index("Mhmm")
    assert _filter_results(
        text,
        [SimpleNamespace(start=start, end=start + len("Mhmm"), entity_type="PERSON", score=0.85)],
    )
    assert _filter_results(
        "Patient: Mhmm.",
        [SimpleNamespace(start=9, end=13, entity_type="LOCATION", score=0.85)],
    )


def test_mhmm_acknowledgement_filter_does_not_apply_to_generic_or_manual_redaction():
    text = "Patient replied: Mhmm."
    start = text.index("Mhmm")

    assert _filter_provider_spans(
        text,
        [Span(start=start, end=start + len("Mhmm"), entity_type="PERSON", score=0.85)],
        score_threshold=0.0,
        entities=None,
    )
    assert _filter_provider_spans(
        text,
        [Span(start=start, end=start + len("Mhmm"), entity_type="LOCATION", score=0.85)],
        score_threshold=0.0,
        entities=None,
    )
    redacted, _, mappings = apply_manual_pii_redaction(
        transcript_text=text,
        dictation_text="",
        start_index=1,
        protections=[ManualPiiProtection("PERSON", "Mhmm")],
    )
    assert redacted == "Patient replied: [PHI-1]."
    assert mappings == [{"index": 1, "type": "PERSON", "value": "Mhmm", "placeholder": "[PHI-1]"}]
