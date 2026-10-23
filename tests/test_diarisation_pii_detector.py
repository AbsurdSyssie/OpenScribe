"""Exercise the installed native detector with synthetic speaker labels."""
import pytest

from app.services.redaction import _detect_with_presidio


@pytest.mark.parametrize(
    ("text", "labels", "name"),
    [
        (
            "Speaker 1: I have had a cough.\nSpeaker 2: When did it start?",
            ("Speaker 1", "Speaker 2"),
            None,
        ),
        (
            "?: It began yesterday.\nSpeaker 1: My name is Alice Smith.",
            ("?", "Speaker 1"),
            "Alice Smith",
        ),
    ],
)
def test_native_pii_detection_distinguishes_synthetic_speaker_markers_from_names(text, labels, name):
    result = _detect_with_presidio(text, language="en", score_threshold=0.35, entities=None)
    label_ranges = [(text.index(label), text.index(label) + len(label)) for label in labels]
    assert not any(
        span.start < end and span.end > start
        for span in result.spans
        for start, end in label_ranges
    )
    if name:
        start = text.index(name)
        assert any(span.start <= start and span.end >= start + len(name) for span in result.spans)
