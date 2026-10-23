"""Recursive character chunking for the naive-RAG baseline (YGH-69).

Cut at the coarsest separator that is present (blank line, then line break, then sentence end,
then space, then anywhere), merge the pieces back up to `size` characters, and carry the last
`overlap` characters' worth of whole pieces into the next chunk. Parameters are fixed by the
owner's decision (500 and 50) and are not tuned on results. Nothing here knows about health
records: it is the generic technique, applied to raw document text.
"""

SEPARATORS = ("\n\n", "\n", ". ", " ", "")
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP,
               separators: tuple = SEPARATORS) -> list:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    sep = next(s for s in separators if s == "" or s in text)
    rest = separators[separators.index(sep) + 1:]
    pieces = []
    for part in (text.split(sep) if sep else list(text)):
        if len(part) > size and rest:
            pieces.extend(split_text(part, size, 0, rest))  # no overlap inside a long piece
        else:
            pieces.append(part)
    return _merge(pieces, sep, size, overlap)


def _merge(pieces: list, sep: str, size: int, overlap: int) -> list:
    chunks, current, length = [], [], 0
    for piece in pieces:
        extra = len(piece) + (len(sep) if current else 0)
        if current and length + extra > size:
            chunks.append(sep.join(current))
            # keep only a tail that fits the overlap and leaves room for the new piece
            while current and (length > overlap or length + len(piece) + len(sep) > size):
                length -= len(current[0]) + (len(sep) if len(current) > 1 else 0)
                current.pop(0)
            extra = len(piece) + (len(sep) if current else 0)
        current.append(piece)
        length += extra
    if current:
        chunks.append(sep.join(current))
    return [c.strip() for c in chunks if c.strip()]
