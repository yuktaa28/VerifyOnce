"""Name matching across documents.

Two names match when they differ only in case, spacing, punctuation, honorifics,
initials ("Ravi K. Patil" vs "Ravi Patil" vs "R. Patil"), or word order
("PATIL RAVI" vs "Ravi Patil"). A different spelling of a word is a real conflict
("Ishan Rao" vs "Ishan Rau"), and so is a different initial ("Ravi K Patil" vs
"Ravi S Patil").
"""

from __future__ import annotations

import re
import unicodedata

_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "sri", "smt"}


def name_tokens(name: str) -> list[str]:
    """Lower-case words of a name, without accents, punctuation or leading honorifics."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[\W_]+", " ", text.casefold())
    tokens = text.split()
    while len(tokens) > 1 and tokens[0] in _HONORIFICS:
        tokens = tokens[1:]
    return tokens


def _token_match(a: str, b: str) -> bool:
    """Equal words, or an initial that matches the start of the other word."""
    return a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))


def _is_subsequence(short: list[str], long: list[str]) -> bool:
    i = 0
    for token in long:
        if i < len(short) and _token_match(short[i], token):
            i += 1
    return i == len(short)


def names_match(a: str, b: str) -> bool:
    """True when two names plausibly belong to the same person (see module docstring)."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb or sorted(ta) == sorted(tb):
        return True
    if len(ta) == 1 or len(tb) == 1:
        return False  # a single word cannot be checked against a full name
    if not (_token_match(ta[0], tb[0]) and _token_match(ta[-1], tb[-1])):
        return False
    # Middle names may be dropped, but those that appear must agree in order.
    middle_a, middle_b = ta[1:-1], tb[1:-1]
    if len(middle_a) > len(middle_b):
        middle_a, middle_b = middle_b, middle_a
    return _is_subsequence(middle_a, middle_b)
