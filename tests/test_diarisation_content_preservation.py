"""Attribution must never replace clinically meaningful transcript punctuation."""
import pytest

from app.services.deepgram_streaming import (
    DeepgramResultEvent,
    DeepgramWordEvent,
    deepgram_event_words_or_fallback,
)


@pytest.mark.parametrize(
    ("transcript", "word_text"),
    [
        ("12.5 mg", "125 mg"),
        ("120/80", "120 80"),
        ("+1", "1"),
        ("-1", "1"),
    ],
)
def test_inexact_speaker_words_preserve_original_text_without_attribution(transcript, word_text):
    event = DeepgramResultEvent(
        text=transcript, is_final=True, speech_final=True, from_finalize=False,
        start=0, duration=1,
        words=(DeepgramWordEvent(word_text, "0", 0, 1),),
    )
    result = deepgram_event_words_or_fallback(event)
    assert len(result) == 1
    assert result[0].text == transcript
    assert result[0].speaker_id is None


def test_exact_word_content_retains_zero_speaker_id():
    words = (
        DeepgramWordEvent("Synthetic", "0", 0, 0.5),
        DeepgramWordEvent("sentence.", "0", 0.5, 1),
    )
    event = DeepgramResultEvent(
        text="Synthetic sentence.", is_final=True, speech_final=True,
        from_finalize=False, start=0, duration=1, words=words,
    )
    assert deepgram_event_words_or_fallback(event) == list(words)
