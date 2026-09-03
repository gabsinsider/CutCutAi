from cutai.editor import _ass_color, _emphasis


def test_ass_color_converts_rgb_to_ass_bgr():
    assert _ass_color('#FFCC00') == '&H0000CCFF'


def test_emphasis_detects_strong_terms():
    assert _emphasis('Isso foi incrível')
    assert not _emphasis('Uma frase comum')
