"""End-to-end smoke test of the ops console API against a running server.

    uv run python scripts/smoke_ops.py --email you@example.com

Creates its own booking and its own driver, walks the full lifecycle, then
deletes everything it made. It never touches a row it did not create.

That constraint is the whole point of this file existing. Doing this by hand
against `/admin/bookings` picks up whatever is at the top of the list, which on
a live database is a real customer — and `completed` is terminal, so there is
no API path back. Ask me how I know. Every id this script creates is tracked
and removed in a `finally`, so an assertion failure half way through still
cleans up.

The gate suite proves the same behaviour offline against SQLite. This proves it
against Postgres, through real HTTP, with the real middleware stack.
"""

import argparse
import asyncio
import getpass
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine

DEFAULT_BASE = "http://127.0.0.1:3000/api/v1"

# Obvious placeholders. If one of these ever survives cleanup, it is instantly
# recognisable in the table as something a script left behind.
TEST_CUSTOMER = "ZZ Smoketest"
TEST_EMAIL = "smoketest@easedrive.example"
TEST_PHONE = "+234 700 000 0001"
TEST_DRIVER = "ZZ Smoketest Driver"


class CheckFailedError(Exception):
    """A check did not hold."""


def call(
    base: str,
    method: str,
    path: str,
    token: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    request = urllib.request.Request(
        f"{base}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "content-type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        return error.code, json.loads(raw) if raw else None


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  {mark}  {label}{suffix}")
    if not condition:
        raise CheckFailedError(label)


async def cleanup(reference: str | None, driver_ids: list[uuid.UUID]) -> None:
    """Remove everything this run created. Runs even when a check fails."""
    settings = get_settings()
    if not settings.is_database_configured:
        print("\ncleanup skipped: DATABASE_URL is not set")
        return

    assert settings.database_url is not None
    engine = get_engine(settings.database_url)
    async with engine.begin() as connection:
        if reference:
            # Events cascade from the booking, but delete explicitly so the
            # count is reported rather than assumed.
            await connection.execute(
                text(
                    "DELETE FROM ease_booking_events WHERE booking_id IN "
                    "(SELECT id FROM ease_bookings WHERE reference = :ref)"
                ),
                {"ref": reference},
            )
            result = await connection.execute(
                text("DELETE FROM ease_bookings WHERE reference = :ref"),
                {"ref": reference},
            )
            print(f"\ncleanup: removed {result.rowcount} booking ({reference})")

        for driver_id in driver_ids:
            await connection.execute(
                text("DELETE FROM ease_drivers WHERE id = :id"), {"id": driver_id}
            )
        if driver_ids:
            print(f"cleanup: removed {len(driver_ids)} driver(s)")

    # Prove it: nothing bearing the test markers is left behind.
    async with engine.connect() as connection:
        stragglers = await connection.scalar(
            text(
                "SELECT count(*) FROM ease_bookings WHERE full_name = :name"
                " OR email = :email"
            ),
            {"name": TEST_CUSTOMER, "email": TEST_EMAIL},
        )
        drivers_left = await connection.scalar(
            text("SELECT count(*) FROM ease_drivers WHERE full_name = :name"),
            {"name": TEST_DRIVER},
        )
    print(f"cleanup: test bookings left={stragglers} test drivers left={drivers_left}")
    await engine.dispose()

    if stragglers or drivers_left:
        raise CheckFailedError("cleanup left test data behind")


async def run(base: str, email: str, password: str) -> int:
    reference: str | None = None
    driver_ids: list[uuid.UUID] = []

    try:
        print("=== sign in ===")
        status, login = call(
            base, "POST", "/auth/login", body={"email": email, "password": password}
        )
        check("admin can sign in", status == 200, f"HTTP {status}")
        token = login["accessToken"]
        check("role is admin", login["user"]["role"] == "admin", login["user"]["role"])

        print("\n=== create a booking through the public form endpoint ===")
        start = (datetime.now(UTC) + timedelta(days=7)).date()
        status, created = call(
            base,
            "POST",
            "/bookings",
            body={
                "fullName": TEST_CUSTOMER,
                "phone": TEST_PHONE,
                "email": TEST_EMAIL,
                "tripType": "airport",
                "pickupLocation": "Smoke Test Origin",
                "destination": "Smoke Test Destination",
                "startDate": start.isoformat(),
                "durationDays": 2,
                "passengers": 3,
                "notes": "Created by scripts/smoke_ops.py",
            },
        )
        check("booking accepted", status == 201, f"HTTP {status}")
        reference = created["reference"]
        print(f"  reference: {reference}")

        print("\n=== it appears in the console, masked ===")
        _, page = call(base, "GET", f"/admin/bookings?q={reference}", token)
        check("found by search", page["total"] == 1)
        row = page["items"][0]
        check("status starts new", row["status"] == "new", row["status"])
        check("phone is masked", TEST_PHONE not in json.dumps(page), row["phoneMasked"])
        check("email is masked", TEST_EMAIL not in json.dumps(page), row["emailMasked"])
        check("surname is trimmed", row["customerName"] == "ZZ S.", row["customerName"])

        print("\n=== detail returns the real values, and logs the look ===")
        _, detail = call(base, "GET", f"/admin/bookings/{reference}", token)
        check("real phone returned", detail["phone"] == TEST_PHONE)
        check("real email returned", detail["email"] == TEST_EMAIL)
        check(
            "a viewed event was written",
            any(event["eventType"] == "viewed" for event in detail["timeline"]),
        )

        print("\n=== vetting gate ===")
        status, driver = call(
            base,
            "POST",
            "/admin/drivers",
            token,
            {"fullName": TEST_DRIVER, "phone": "+234 700 000 0002"},
        )
        check("driver created", status == 201, f"HTTP {status}")
        driver_ids.append(uuid.UUID(driver["id"]))
        check("starts unvetted", driver["vettingStatus"] == "pending")
        check("and unassignable", driver["isAssignable"] is False)

        blocked_status, blocked = call(
            base,
            "POST",
            f"/admin/bookings/{reference}/assign",
            token,
            {"driverId": driver["id"]},
        )
        check(
            "unvetted driver refused",
            blocked_status == 422,
            f"HTTP {blocked_status}",
        )
        check(
            "with a readable reason",
            blocked["code"] == "driver_not_assignable",
            blocked["message"],
        )

        _, verified = call(
            base,
            "PATCH",
            f"/admin/drivers/{driver['id']}",
            token,
            {"vettingStatus": "verified"},
        )
        check("verifying makes them assignable", verified["isAssignable"] is True)

        print("\n=== lifecycle ===")
        _, assigned = call(
            base,
            "POST",
            f"/admin/bookings/{reference}/assign",
            token,
            {"driverId": driver["id"]},
        )
        check("assignment also matches the booking", assigned["status"] == "matched")
        for target in ("confirmed", "in_progress", "completed"):
            status, moved = call(
                base,
                "PATCH",
                f"/admin/bookings/{reference}/status",
                token,
                {"status": target},
            )
            check(f"-> {target}", moved["status"] == target, f"HTTP {status}")

        illegal_status, _ = call(
            base,
            "PATCH",
            f"/admin/bookings/{reference}/status",
            token,
            {"status": "new"},
        )
        check(
            "a terminal booking cannot be reopened",
            illegal_status == 409,
            f"HTTP {illegal_status}",
        )

        print("\n=== audit trail ===")
        _, final = call(base, "GET", f"/admin/bookings/{reference}", token)
        moves = [
            f"{event['fromStatus']}->{event['toStatus']}"
            for event in final["timeline"]
            if event["eventType"] == "status_changed"
        ]
        check(
            "every hop recorded",
            moves
            == [
                "new->matched",
                "matched->confirmed",
                "confirmed->in_progress",
                "in_progress->completed",
            ],
            " ".join(moves),
        )
        check(
            "each carries an actor",
            all(
                event["actorName"]
                for event in final["timeline"]
                if event["eventType"] != "created"
            ),
        )

        print("\n=== metrics ===")
        status, metrics = call(base, "GET", "/admin/metrics", token)
        check("metrics returned", status == 200)
        check(
            "median time to assign is now measurable",
            metrics["medianHoursToAssign"] is not None,
            f"{metrics['medianHoursToAssign']} hours",
        )
        check("recent list is masked too", TEST_PHONE not in json.dumps(metrics))

        call(base, "POST", "/auth/logout", body={"refreshToken": login["refreshToken"]})
        print("\nALL CHECKS PASSED")
        return 0

    except CheckFailedError as error:
        print(f"\nFAILED: {error}", file=sys.stderr)
        return 1
    finally:
        await cleanup(reference, driver_ids)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--stdin-password", action="store_true", help="read the password from stdin"
    )
    arguments = parser.parse_args()

    secret = (
        sys.stdin.readline().rstrip("\n")
        if arguments.stdin_password
        else getpass.getpass("Password: ")
    )
    sys.exit(asyncio.run(run(arguments.base, arguments.email, secret)))
