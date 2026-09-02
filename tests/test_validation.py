import pytest

from cutai.validation import extract_url, validate_source_url


def test_detects_youtube():
    url, platform = validate_source_url("https://www.youtube.com/live/abc")
    assert platform == "YouTube"
    assert url.endswith("/abc")


def test_rejects_credentials():
    with pytest.raises(ValueError):
        validate_source_url("https://user:secret@example.com/live")


def test_extracts_url_from_issue():
    assert extract_url("## Link\nhttps://tiktok.com/@x/live\n") == "https://tiktok.com/@x/live"

