from cutai.metadata import suggest_metadata


def test_metadata_always_includes_viral():
    description, tags = suggest_metadata("Um momento impressionante durante a final do campeonato.")
    assert description
    assert "#viral" in tags


def test_empty_transcript_has_fallback():
    description, tags = suggest_metadata("")
    assert description
    assert len(tags) >= 3

