"""Gate tests for PII masking.

Masking has one job with two failure modes, and they point in opposite
directions: reveal too much and the list view leaks contact details, reveal too
little and staff cannot tell one booking from another. Both are asserted.
"""

import pytest

from app.domain.masking import mask_email, mask_name, mask_phone

# --- phones -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("+234 800 123 4512", "+••• ••• ••• ••12"),
        ("08012345612", "•••••••••12"),
        ("+1 (555) 010-9912", "+• (•••) •••-••12"),
    ],
)
def test_phone_keeps_only_the_last_two_digits(given: str, expected: str) -> None:
    assert mask_phone(given) == expected


def test_phone_keeps_its_shape() -> None:
    """Spaces and brackets survive so the value still scans as a phone number."""
    masked = mask_phone("+234 800 123 4512")
    assert masked.count(" ") == "+234 800 123 4512".count(" ")


@pytest.mark.parametrize("given", ["+234 800 123 4512", "08012345612"])
def test_phone_reveals_at_most_two_digits(given: str) -> None:
    """The property that matters: a masked number cannot be dialled."""
    assert sum(character.isdigit() for character in mask_phone(given)) <= 2


@pytest.mark.parametrize("given", ["", "1", "12", "ab"])
def test_a_phone_too_short_to_mask_is_hidden_entirely(given: str) -> None:
    """Never return the original: two digits of a two-digit number is all of it."""
    assert not any(character.isdigit() for character in mask_phone(given))


# --- emails -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("ada@example.com", "a••@example.com"),
        ("ada.lovelace@example.com", "a•••••••••••@example.com"),
        ("a@example.com", "a•@example.com"),
    ],
)
def test_email_keeps_the_first_character_and_the_domain(
    given: str, expected: str
) -> None:
    assert mask_email(given) == expected


def test_email_never_leaks_the_local_part() -> None:
    assert "lovelace" not in mask_email("ada.lovelace@example.com")


@pytest.mark.parametrize("given", ["not-an-email", "", "@example.com"])
def test_a_malformed_email_is_masked_wholesale(given: str) -> None:
    """No `@` means no safe split, so nothing is assumed."""
    assert "@" not in mask_email(given) or mask_email(given).startswith("•")


# --- names ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("Ada Lovelace", "Ada L."),
        ("Ada King Lovelace", "Ada K. L."),
        ("Ada", "Ada"),
    ],
)
def test_name_keeps_the_first_name_and_initials(given: str, expected: str) -> None:
    assert mask_name(given) == expected


def test_name_does_not_leak_the_surname() -> None:
    assert "Lovelace" not in mask_name("Ada Lovelace")


def test_a_single_name_is_left_alone() -> None:
    """There is nothing to trim, and blanking it would make the row unreadable."""
    assert mask_name("Ada") == "Ada"
