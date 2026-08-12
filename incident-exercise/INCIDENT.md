# INCIDENT.md - notifyd Investigation

This document covers three tickets. For each one, I explain what went wrong, show the exact query and output that proves it, what I did to fix it right away, and what I suggest doing so it does not happen again.

---

## N1 - Reminders only going out at night

**What happened:**
The settings that control "quiet hours" (the time window when we should NOT send messages, like late night) got swapped by mistake. This flipped the quiet hours from nighttime to daytime, so now the system only sends messages at night and skips all daytime tours completely.

**Proof:**

I checked the settings change history:

```sql
SELECT * FROM settings_audit ORDER BY id DESC LIMIT 30;
```

Result:
```
quiet_end   changed from 9  to 21   by ops-console on 2026-08-08
quiet_start changed from 21 to 9    by ops-console on 2026-08-08
batch_limit changed from 10 to 25   by deploy on 2026-07-12
quiet_end   set to 9   by deploy on 2026-05-12 (original setup)
quiet_start set to 21  by deploy on 2026-05-12 (original setup)
```

Since the app was first set up (May 12), quiet hours were 21:00 to 09:00, meaning "don't send between 9pm and 9am." That is correct, normal behavior.

On August 8 (about 4 days before this issue was reported), someone using the ops-console flipped these two numbers. Now the settings say quiet hours are 09:00 to 21:00, meaning "don't send between 9am and 9pm." That is backwards. So now the system only sends during the night, and blocks every single daytime tour reminder.

This matches exactly what was reported: reminders only going out at night, and morning/daytime tours getting nothing. The code itself was never changed, this was purely a settings mistake.

**Immediate fix:**
```sql
UPDATE settings SET value = '21' WHERE key = 'quiet_start';
UPDATE settings SET value = '9'  WHERE key = 'quiet_end';
```

**How to prevent this going forward:**
- Add a check so if someone tries to set quiet hours in a way that blocks most of the day, the system warns or blocks that change.
- Set up a simple daily check that looks at what time of day messages are being sent, so if it suddenly shifts to "only nighttime," someone gets alerted automatically instead of waiting for a customer complaint.
- Require a second person to approve any quiet-hours change made through the ops-console, since it affects every customer.

---

## N2 - Some customers getting two reminders

**What happened:**
There is a second, older sending process (I found it labeled `cron-v1` in the data) that is still running somewhere and is not part of the `notifyd.py` code we were given. It sends its own reminders, separately from notifyd, and only for customers who came in through the old "legacy form" signup method. Since these two systems do not talk to each other, both end up sending a reminder for the same tour.

**Proof:**

I looked for tours that got more than one message:

```sql
SELECT appointment_id, COUNT(*) c FROM messages 
GROUP BY appointment_id HAVING c > 1;
```
This returned 44 tours with duplicate messages.

Looking closer at who sent them:
```sql
SELECT a.id, a.source, m.sender, m.sent_at
FROM appointments a JOIN messages m ON m.appointment_id = a.id
WHERE a.id IN (...those 44 tours...) ORDER BY a.id, m.sent_at;
```
Every single one of these 44 tours has exactly two messages: one from `notifyd`, and one from something called `cron-v1`, sent a few minutes to an hour apart.

Checking the source of these tours:
```sql
SELECT a.source, COUNT(DISTINCT a.id) c FROM appointments a
WHERE a.id IN (...those 44 tours...) GROUP BY a.source;
```
Result: all 44 came from `source = legacy_form`. None came from the normal web signup.

And from the stats command, `cron-v1` has sent exactly 44 messages total, matching the 44 duplicate tours perfectly.

Finally, I checked which processes are actively "checking in" (heartbeats) with the system:
```sql
SELECT DISTINCT worker FROM heartbeats;
```
Only `notifyd@prod-sched-1` shows up. `cron-v1` never checks in at all, meaning it is a separate, unmonitored program that nobody is currently watching.

So the conclusion is solid: this is not a bug in the notifyd code we were given. There is a second, old, forgotten sender still running in production, only for old-form customers, and it duplicates every reminder notifyd already sends.

**Immediate fix:**
Find and stop the `cron-v1` process. It is not in this repository, so it needs to be located on the server or wherever old cron jobs run and shut down. If it cannot be stopped right away, it should be updated to check the same `last_reminded` field that notifyd already uses, so it skips tours that were already reminded.

**How to prevent this going forward:**
- Whenever we migrate customers from an old system to a new one, we need a checklist step to actually decommission the old sender, not just stop using it for new signups.
- Add a safety net in the database itself: before any message is inserted, check if one already exists for that tour. That way, even if a second sender exists by accident in the future, it physically cannot send a duplicate.
- Every process that sends messages should be required to report a heartbeat, so we can see all active senders in one place instead of finding out from a rogue one months later.

---

## N3 - Dana Whitfield never getting reminded

**What happened:**
All three of Dana's bookings have a reminder date stuck in the future (March 2027), which tricks the system into thinking she was already reminded, when she never actually was. This value was not created by notifyd, something else set it.

**Proof:**

```sql
SELECT id, client_name, phone, last_reminded FROM appointments 
WHERE client_name LIKE '%Whitfield%';
```
Result: all three of her bookings (from June, July, and August) show the exact same reminder date: `2027-03-14T09:00:00`. That is more than a year in the future from today, so it clearly is not a real reminder timestamp.

```sql
SELECT * FROM messages WHERE appointment_id IN (86, 202, 259);
```
Result: no rows at all. Not a single message was ever actually sent to her.

This matters because if you look at how notifyd.py works, it only ever sets this "reminded" timestamp at the same moment it actually sends a message and saves it. Since there are zero messages for her, notifyd never touched these records. Something else wrote that fake future date directly into the database.

I also checked if this happens to anyone else:
```sql
SELECT phone, last_reminded, COUNT(*) c FROM appointments
WHERE last_reminded IS NOT NULL GROUP BY phone, last_reminded HAVING c > 1;
```
Only Dana's phone number shows this pattern. So this is not a wider bug affecting many customers, it is specific to her account, likely from a manual database edit or a mistake in some other tool that touched her records directly.

**Immediate fix:**
```sql
UPDATE appointments SET last_reminded = NULL WHERE id = 259;
```
This clears the fake date on her upcoming tour (August 13, 7:09pm) so the normal system picks it up and sends her a real reminder before the tour happens. After running this, I ran `python notifyd.py run` to confirm it actually sent.

**How to prevent this going forward:**
- Keep a change history for the appointments table too, similar to the one we already have for settings. That way, if someone or something edits a record directly, we can see who and when.
- Add a daily check that flags anything odd, like a "reminded" date that is set in the future, or a "reminded" date with no matching message. Both are impossible under normal operation and are early warning signs.
- Restrict who or what is allowed to write directly to the "reminded" field in the database, so only the actual sending process can update it.