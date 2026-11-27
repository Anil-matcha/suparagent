import pytest

from app.channels.text import chunk_text


def test_short_text_is_one_piece():
    assert chunk_text("hello there", 100) == ["hello there"]


def test_empty_text_makes_no_pieces():
    assert chunk_text("  \n ", 100) == []


def test_every_piece_fits_and_nothing_is_lost_or_empty():
    paragraphs = [f"Paragraph {i}. " + "word " * 40 for i in range(12)]
    pieces = chunk_text("\n\n".join(paragraphs), 300)
    assert len(pieces) > 1
    assert all(0 < len(p) <= 300 for p in pieces)
    joined = " ".join(pieces)
    for i in range(12):
        assert f"Paragraph {i}." in joined


def test_cuts_at_a_paragraph_before_a_sentence_or_word():
    first = "A" * 120
    pieces = chunk_text(first + "\n\n" + "B" * 120, 200)
    assert pieces == [first, "B" * 120]


def test_a_word_longer_than_the_limit_is_cut_anywhere_rather_than_lost():
    pieces = chunk_text("x" * 500, 120)
    assert all(len(p) <= 120 for p in pieces)
    assert "".join(pieces) == "x" * 500


def test_a_cut_inside_a_code_fence_closes_and_reopens_it():
    code = "\n".join(f"line_{i} = {i}" for i in range(60))
    pieces = chunk_text("Intro\n\n```python\n" + code + "\n```\n\nOutro", 200)
    assert len(pieces) > 2
    for piece in pieces:
        assert len(piece) <= 200
        assert piece.count("```") % 2 == 0, piece
    assert pieces[-1].endswith("Outro")


def test_a_tiny_limit_is_refused():
    with pytest.raises(ValueError):
        chunk_text("text", 10)
