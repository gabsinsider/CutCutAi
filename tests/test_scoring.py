from cutai.scoring import audio_score, combine_scores, transcript_score


def test_emotional_text_scores_higher():
    calm, _ = transcript_score("hoje vamos conversar sobre o assunto")
    strong, reasons = transcript_score("Olha! Isso é inacreditável, que momento histórico!")
    assert strong > calm
    assert reasons


def test_weights_are_applied():
    score = combine_scores(audio=100, transcript=0, scene=0)
    assert score.total == 35


def test_audio_peak_adds_reason():
    score, reasons = audio_score(0.02, 0.40)
    assert score > 0
    assert reasons

