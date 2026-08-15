"""Staff roles and what each one may do.

Two roles, because two is what the business actually has today: someone who
works the booking queue, and someone who also administers the people and
drivers behind it. A third role invented now would be a guess with a migration
attached.

Permission questions are asked as functions rather than by comparing to a role
literal at each call site, so adding a role later means editing this file
instead of grepping for `== "admin"`.
"""

from enum import StrEnum


class UserRole(StrEnum):
    """A staff member's role."""

    #: Works bookings: view, assign, change status, add internal notes.
    OPS = "ops"
    #: Everything OPS can do, plus managing drivers and staff accounts.
    ADMIN = "admin"


def can_manage_drivers(role: UserRole) -> bool:
    """True when the role may create, edit or deactivate drivers."""
    return role is UserRole.ADMIN


def can_manage_users(role: UserRole) -> bool:
    """True when the role may administer staff accounts."""
    return role is UserRole.ADMIN


def can_work_bookings(role: UserRole) -> bool:
    """True when the role may view and progress bookings.

    Every current role can. The function exists so the endpoint reads as a
    permission check rather than as a comment, and so a future read-only role
    has one place to be excluded.
    """
    return role in {UserRole.OPS, UserRole.ADMIN}
