# notifyd — Incident Forensics Exercise

`notifyd` is a small tour-reminder service. Unlike most debugging exercises,
**this code was independently audited and no defects were found** — and yet
operations keeps filing tickets. Your job is to explain what is actually
happening, using evidence.

You get two things:

- `notifyd.py` — the service (audited, believed correct)
- `incident.db` — a snapshot of the production database, taken just now

## The rules 

- **Every claim needs proof.** A diagnosis must come with the query (or
  command) and its output that demonstrates it. We will re-run your queries.
- **Unsupported claims count against you.** "This function looks buggy" with
  no evidence from the database is worse than saying nothing. If you believe
  the code is at fault, you must demonstrate it with data.
- **Any tools are allowed, including AI assistants.** Use whatever you'd use
  on the job. The deliverable is an evidence chain, not code style — tools
  don't change what counts as proof.
- Timebox: **45 minutes live, or the deadline stated in your invitation
  email if you received this as a take-home.**

## Deliverable

An `INCIDENT.md` with, for each ticket: the root cause, the evidence (query +
output), the immediate fix, and how you'd prevent the class of problem from
recurring.

## The tickets

**N1 — Reminders went nocturnal.** Starting about four days ago, reminders
only go out late at night, and clients with morning tours get no reminder at
all. Nobody deployed anything — the running code is exactly what was audited.

**N2 — Chronic double reminders.** For months, a subset of clients has been
receiving two reminders for the same tour, a few minutes apart. It's always
the same kind of client, but nobody has figured out what they have in common.

**N3 — Dana never gets reminded.** Dana Whitfield has booked three tours with
us and has never received a single reminder. Her phone number is verified
working. Her next tour is within the next 24 hours — as part of your fix,
make sure she gets this one.

## Useful starting points

```
python notifyd.py stats
python notifyd.py run          # safe: operates only on the local snapshot
sqlite3 incident.db            # or any sqlite client / python REPL
```

Schema: `appointments`, `messages`, `settings`, `settings_audit`, `heartbeats`.
