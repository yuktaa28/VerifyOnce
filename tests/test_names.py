import pytest

from backend.names import name_tokens, names_match


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Ravi K. Patil", "Ravi Patil"),  # dropped middle initial
        ("Ravi K. Patil", "RAVI K PATIL"),  # case and punctuation
        ("  Ravi   Patil ", "Ravi Patil"),  # whitespace
        ("R. Patil", "Ravi Patil"),  # first-name initial
        ("Ravi Kumar Patil", "Ravi K Patil"),  # middle initial vs full middle name
        ("R. K. Patil", "Ravi Kumar Patil"),  # all initials
        ("PATIL RAVI", "Ravi Patil"),  # surname first
        ("Shri Ravi Patil", "Ravi Patil"),  # honorific
        ("Kavyā Nair", "Kavya Nair"),  # accents
    ],
)
def test_same_person_names_match(a, b):
    assert names_match(a, b)
    assert names_match(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Ishan Rao", "Ishan Rau"),  # one letter differs: a real conflict
        ("Ravi K Patil", "Ravi S Patil"),  # different middle initials
        ("Ravi Patil", "Ravindra Patil"),  # different first name
        ("A. Patil", "Ravi Patil"),  # initial does not fit
        ("Ravi", "Ravi Patil"),  # single word cannot be checked
        ("", "Ravi Patil"),
    ],
)
def test_real_conflicts_are_flagged(a, b):
    assert not names_match(a, b)
    assert not names_match(b, a)


def test_tokens_normalise_case_punctuation_and_spacing():
    assert name_tokens("  Ravi  K.  PATIL ") == ["ravi", "k", "patil"]
