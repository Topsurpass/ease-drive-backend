"""Partial redaction for contact details shown in list views.

The ops console's booking list is the widest PII surface in the product: one
request returns every customer's name, phone and email at once. Most of the
time nobody needs those — staff are scanning routes, dates and statuses — so
the list returns masked values and the detail endpoint returns the real ones,
against an audit row.

Masking keeps enough to *recognise* a record ("yes, that's the number ending
12") and not enough to *use* one. Anyone who needs the full value opens the
booking, and that look is logged.

Pure string functions with no imports from the rest of the app, so they can be
unit-tested exhaustively and reused by any layer.
"""

from typing import Final

#: Digits of a phone number left visible at the end.
PHONE_TAIL: Final[int] = 2

#: Characters of an email local part left visible at the start.
EMAIL_HEAD: Final[int] = 1

BULLET: Final[str] = "•"


def mask_phone(phone: str) -> str:
    """Mask all but the last few digits, keeping the shape recognisable.

    ``+234 800 123 4512`` -> ``+234 •••• •• 12``

    Non-digits are preserved so the result still looks like a phone number
    rather than a run of dots, which is what makes it scannable in a table.
    A number too short to mask meaningfully is replaced entirely rather than
    returned intact.
    """
    digits = [character for character in phone if character.isdigit()]
    if len(digits) <= PHONE_TAIL:
        return BULLET * len(phone)

    keep_from = len(digits) - PHONE_TAIL
    seen = 0
    masked: list[str] = []

    for character in phone:
        if not character.isdigit():
            masked.append(character)
            continue
        masked.append(character if seen >= keep_from else BULLET)
        seen += 1

    return "".join(masked)


def mask_email(email: str) -> str:
    """Keep the first character and the domain.

    ``ada.lovelace@example.com`` -> ``a•••••••••••@example.com``

    The domain survives because it is rarely the sensitive part and is often
    what staff are checking (a corporate booking, a repeat customer). Anything
    without an ``@`` is masked wholesale rather than guessed at.
    """
    local, separator, domain = email.partition("@")
    if not separator or not local:
        return BULLET * len(email)

    head = local[:EMAIL_HEAD]
    return f"{head}{BULLET * max(len(local) - EMAIL_HEAD, 1)}@{domain}"


def mask_name(full_name: str) -> str:
    """Keep first name and the initial of each remaining part.

    ``Ada Lovelace`` -> ``Ada L.``

    Unlike phone and email, a first name is not a contact route — it cannot be
    used to reach anyone — and staff need it to talk about the booking at all.
    So this trims identifiability rather than hiding the value.
    """
    parts = full_name.split()
    if len(parts) <= 1:
        return full_name

    initials = " ".join(f"{part[0]}." for part in parts[1:] if part)
    return f"{parts[0]} {initials}".strip()
