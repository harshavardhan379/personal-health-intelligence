"""Recursive chunking (evaluation/systems/chunking.py)."""

from evaluation.systems.chunking import CHUNK_OVERLAP, CHUNK_SIZE, split_text


def test_the_fixed_parameters_are_the_agreed_ones():
    assert (CHUNK_SIZE, CHUNK_OVERLAP) == (500, 50)


def test_short_text_is_one_chunk_and_empty_text_is_none():
    assert split_text("HbA1c: 9.8 %") == ["HbA1c: 9.8 %"]
    assert split_text("   \n ") == []


def test_no_chunk_exceeds_the_size_even_for_one_endless_word():
    for text in ("word " * 400, "x" * 1700, ("line of text\n" * 90) + "y" * 900):
        chunks = split_text(text, size=100, overlap=10)
        assert chunks and all(0 < len(c) <= 100 for c in chunks), text[:20]


def test_every_line_survives_whole_when_lines_are_short():
    lines = [f"Analyte{n}: {n}.{n} mg/dL (ref 1.0-5.0)" for n in range(60)]
    chunks = split_text("\n".join(lines), size=200, overlap=40)
    joined = "\n".join(chunks)
    assert all(line in joined for line in lines)
    for line in lines:  # and no line is cut in two
        assert any(line in c for c in chunks)


def test_paragraph_breaks_are_preferred_over_line_breaks():
    para_a = "\n".join(c * 40 for c in "abc")  # 3 lines, 122 characters
    para_b = "\n".join(c * 40 for c in "def")
    # size 200 holds one paragraph and a bit more: cutting at lines first would run the
    # first line of B into A's chunk
    chunks = split_text(para_a + "\n\n" + para_b, size=200, overlap=0)
    assert chunks == [para_a, para_b]


def test_neighbouring_chunks_overlap_by_whole_pieces_within_the_limit():
    lines = [f"entry number {n:03d}" for n in range(40)]  # 16 characters each
    chunks = split_text("\n".join(lines), size=100, overlap=40)
    assert len(chunks) > 2
    for before, after in zip(chunks, chunks[1:]):
        shared = [line for line in lines if line in before and line in after]
        assert shared, (before, after)  # the boundary is repeated
        assert len("\n".join(shared)) <= 40


def test_no_overlap_means_each_line_appears_once():
    lines = [f"entry number {n:03d}" for n in range(40)]
    chunks = split_text("\n".join(lines), size=100, overlap=0)
    joined = "\n".join(chunks)
    assert all(joined.count(line) == 1 for line in lines)


def test_chunking_is_deterministic():
    text = "Lab report.\n\nHbA1c: 9.8 %\nCreatinine: 0.8 mg/dL. " * 30
    assert split_text(text) == split_text(text)


def test_a_new_piece_is_never_squeezed_in_beside_a_carried_over_tail_that_leaves_no_room():
    text = "a" * 45 + "\n" + "b" * 60  # the overlap would keep the 45, but 45 + 60 > 100
    chunks = split_text(text, size=100, overlap=50)
    assert all(len(c) <= 100 for c in chunks)
    assert chunks == ["a" * 45, "b" * 60]
