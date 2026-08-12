#!/usr/bin/env python3
"""notifyd - tour reminder sender.

Sends each upcoming tour appointment a reminder SMS about 24h before the
tour. Reads its operating config from the settings table on every tick.

Commands:
    python notifyd.py run       one tick: send due reminders
    python notifyd.py stats     DB overview


"""
import argparse
import socket
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "incident.db"
SENDER = "notifyd"

# Hour offsets relative to the server clock (host runs in America/Chicago).
# Unknown timezones are treated as server-local.
TZ_OFFSET_HOURS = {
    "America/Chicago": 0,
    "America/New_York": 1,
    "America/Denver": -1,
    "America/Los_Angeles": -2,
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_settings(conn):
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def in_quiet_hours(client_tz, settings):
    """True if it is currently quiet hours in the client's local time."""
    start = int(settings.get("quiet_start", 21))
    end = int(settings.get("quiet_end", 9))
    hour = (datetime.now().hour + TZ_OFFSET_HOURS.get(client_tz, 0)) % 24
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def due_appointments(conn, settings):
    """Appointments touring within 24h that have not been reminded yet."""
    now = datetime.now().isoformat(timespec="seconds")
    horizon = (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")
    limit = int(settings.get("batch_limit", 25))
    return conn.execute(
        """
        SELECT id, client_name, phone, tz, tour_at, source
        FROM appointments
        WHERE tour_at > ? AND tour_at <= ? AND last_reminded IS NULL
        ORDER BY tour_at ASC
        LIMIT ?
        """,
        (now, horizon, limit),
    ).fetchall()


def cmd_run(args):
    conn = get_db()
    settings = get_settings(conn)
    if settings.get("send_enabled", "true") != "true":
        print("sending disabled via settings; tick skipped")
        conn.close()
        return
    due = due_appointments(conn, settings)
    sent = 0
    deferred = 0
    for appt in due:
        if in_quiet_hours(appt["tz"], settings):
            deferred += 1   # stays unreminded; picked up again next tick
            continue
        first = (appt["client_name"] or "there").split() or ["there"]
        when = appt["tour_at"][:16].replace("T", " ")
        body = (f"Hi {first[0]}! Reminder: your apartment tour is at {when}. "
                f"Reply R to reschedule.")
        now = datetime.now().isoformat(timespec="seconds")
        # provider call happens here; treated as delivered when it returns
        with conn:
            conn.execute(
                "INSERT INTO messages (appointment_id, phone, sender, body, sent_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (appt["id"], appt["phone"], SENDER, body, now),
            )
            conn.execute(
                "UPDATE appointments SET last_reminded = ? WHERE id = ?",
                (now, appt["id"]),
            )
        print(f"  reminded {appt['phone']} for tour at {when}")
        sent += 1
    with conn:
        conn.execute(
            "INSERT INTO heartbeats (worker, ts) VALUES (?, ?)",
            (f"{SENDER}@{socket.gethostname()}",
             datetime.now().isoformat(timespec="seconds")),
        )
    conn.close()
    print(f"tick complete: {len(due)} due, {sent} sent, {deferred} deferred (quiet hours)")


def cmd_stats(args):
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
    now = datetime.now().isoformat(timespec="seconds")
    horizon = (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")
    upcoming = conn.execute(
        "SELECT COUNT(*) FROM appointments WHERE tour_at > ? AND tour_at <= ?",
        (now, horizon),
    ).fetchone()[0]
    print(f"appointments: {total} total, {upcoming} touring in the next 24h")
    for row in conn.execute(
            "SELECT sender, COUNT(*) AS c FROM messages GROUP BY sender ORDER BY c DESC"):
        print(f"messages[{row['sender']}]: {row['c']}")
    print("last 5 messages:")
    for row in conn.execute(
            "SELECT sent_at, sender, phone FROM messages ORDER BY id DESC LIMIT 5"):
        print(f"  {row['sent_at']}  {row['sender']:12s} {row['phone']}")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="notifyd tour reminders")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
