"""Search tokenisation that works for Chinese as well as it does for English.

PostgreSQL's text-search parser splits on whitespace and punctuation. It has no
CJK dictionary and no word segmentation, so an unspaced run of Han characters
becomes a *single* lexeme::

    to_tsvector('simple', '請問明天的會議改到下午三點嗎')
        -> '請問明天的會議改到下午三點嗎':1

Searching 會議 then produces the lexeme ``'會議'``, which does not equal that
one long lexeme, and a message plainly containing the word returns nothing. The
usual fixes — ``zhparser``/SCWS for dictionary segmentation, ``pg_bigm`` for
CJK-aware 2-grams — are Postgres extensions requiring superuser
``CREATE EXTENSION``, and are unavailable on managed Supabase.

So we do what ``pg_bigm`` does, in application code: expand CJK runs into
overlapping bigrams before indexing, and expand the query the same way::

    '明天的會議'  ->  '明天 天的 的會 會議'
    query '會議'  ->  '會議'                   -> matches, on the GIN index

Both sides call :func:`searchable`, which is why it lives in one module rather
than being inlined at either end: write-time and query-time tokenisation
drifting apart yields a search index that silently matches nothing, with no
error anywhere to notice.

The cost is roughly one lexeme per character and occasional cross-boundary
false positives (searching 天的 above matches). That trade is right for an
inbox, which tolerates an extra result but never a missing one, and ``ts_rank``
pushes accidental matches down. Real word segmentation (``jieba``) would
improve precision later by rewriting the same column — a reindex, not a schema
change.
"""

from __future__ import annotations

#: Unicode ranges whose scripts are written without spaces between words, so a
#: whitespace parser cannot find word boundaries in them. Han (plus the
#: Japanese kana that interleave with it in the same sentence) and Hangul
#: syllables. Latin, Cyrillic, Greek, Arabic and Hebrew are space-delimited and
#: are left for Postgres to tokenise normally.
_UNSEGMENTED_RANGES: tuple[tuple[int, int], ...] = (
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0x20000, 0x2FA1F),  # CJK Extension B-F + Compatibility Supplement
)

#: Ceiling on the text handed to :func:`searchable`. Bigram expansion roughly
#: doubles the token count, so an unbounded body would inflate both the stored
#: search text and the GIN index. Callers truncate the body itself; this is the
#: backstop for anything that does not.
MAX_SEARCHABLE_CHARS = 64_000


def is_unsegmented(ch: str) -> bool:
    """Whether ``ch`` belongs to a script written without word spaces."""
    code = ord(ch)
    return any(low <= code <= high for low, high in _UNSEGMENTED_RANGES)


def bigrams(run: str) -> list[str]:
    """Overlapping 2-grams of ``run``; the run itself when it is one character.

    A single character has no bigram, and dropping it would make one-character
    text unsearchable, so it is emitted whole. It still will not be *found* by
    a bigram query — that edge is what the caller's substring fallback covers.
    """
    if len(run) < 2:
        return [run] if run else []
    return [run[i : i + 2] for i in range(len(run) - 1)]


def searchable(text: str) -> str:
    """Rewrite ``text`` into space-delimited tokens Postgres can index.

    Space-delimited scripts pass through untouched; unsegmented runs are
    expanded into overlapping bigrams. Must be applied identically to indexed
    text and to query text.
    """
    if not text:
        return ""
    if len(text) > MAX_SEARCHABLE_CHARS:
        text = text[:MAX_SEARCHABLE_CHARS]

    tokens: list[str] = []
    run: list[str] = []
    plain: list[str] = []

    def flush_run() -> None:
        if run:
            tokens.extend(bigrams("".join(run)))
            run.clear()

    def flush_plain() -> None:
        # Kept verbatim, separators included: Postgres tokenises this part
        # perfectly well and rewriting it would only lose information.
        if plain:
            chunk = "".join(plain).strip()
            if chunk:
                tokens.append(chunk)
            plain.clear()

    for ch in text:
        if is_unsegmented(ch):
            flush_plain()
            run.append(ch)
        else:
            flush_run()
            plain.append(ch)
    flush_run()
    flush_plain()
    return " ".join(tokens)


def has_unsegmented(text: str) -> bool:
    """Whether ``text`` contains any character needing bigram expansion."""
    return any(is_unsegmented(ch) for ch in text)


def needs_substring_fallback(query: str) -> bool:
    """Whether ``query`` cannot be satisfied by the bigram index alone.

    A lone CJK character produces no bigram, so full-text search cannot match
    it however the row was indexed. Callers use this to decide when the
    ``ILIKE`` path is the only one that can answer.
    """
    stripped = query.strip()
    if not stripped:
        return False
    return len(stripped) == 1 and is_unsegmented(stripped)
