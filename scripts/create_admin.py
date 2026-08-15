"""Create a staff account from the command line.

There is no self-registration endpoint, deliberately: this table is what stands
between the open internet and every customer's phone number. The first admin is
made here, and everyone after that is made by an admin through the console.

    uv run python scripts/create_admin.py ada@example.com --name "Ada Lovelace"

The password is prompted for, never passed as an argument — an argument lands
in shell history and in the process list, where any other user on the box can
read it. Pass --stdin-password to pipe one in from a secret manager instead.

Re-running with an existing email updates that account's password, name and
role rather than failing, so this doubles as a password reset for the case
where nobody can get in to use the console.
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import get_engine, get_sessionmaker
from app.domain.roles import UserRole
from app.models.user import User
from app.schemas.auth import MIN_PASSWORD_LENGTH
from app.services.auth import normalise_email


def _read_password(from_stdin: bool) -> str | None:
    """Prompt twice, or read one line from stdin."""
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
        return password or None

    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.")
        return None
    return password


async def main(email: str, full_name: str, role: str, from_stdin: bool) -> int:
    settings = get_settings()
    if not settings.is_database_configured:
        print("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
        return 2

    password = _read_password(from_stdin)
    if password is None:
        return 2

    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        return 2

    assert settings.database_url is not None
    engine = get_engine(settings.database_url)
    factory = get_sessionmaker(settings.database_url)

    address = normalise_email(email)

    async with factory() as session:
        existing = await session.scalar(select(User).where(User.email == address))

        if existing is None:
            session.add(
                User(
                    email=address,
                    password_hash=hash_password(password),
                    full_name=full_name.strip(),
                    role=role,
                )
            )
            action = "created"
        else:
            existing.password_hash = hash_password(password)
            existing.full_name = full_name.strip()
            existing.role = role
            # A reset is pointless if the account is still locked out or
            # deactivated, which is usually exactly why someone is running this.
            existing.is_active = True
            existing.failed_login_count = 0
            existing.locked_until = None
            action = "updated"

        await session.commit()

    await engine.dispose()
    print(f"{action} {role} account for {address}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--name", required=True, help="full name, e.g. 'Ada Lovelace'")
    parser.add_argument(
        "--role",
        default=UserRole.ADMIN.value,
        choices=[role.value for role in UserRole],
    )
    parser.add_argument(
        "--stdin-password",
        action="store_true",
        help="read the password from stdin instead of prompting",
    )
    arguments = parser.parse_args()
    sys.exit(
        asyncio.run(
            main(
                arguments.email,
                arguments.name,
                arguments.role,
                arguments.stdin_password,
            )
        )
    )
