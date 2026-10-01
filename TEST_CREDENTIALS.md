# Test / Demo Login Credentials

Local development only. These accounts are created by running:

```
.venv/Scripts/python.exe manage.py seed_demo_data
```

The command is idempotent — safe to re-run any time.

| Role | Username | Password | Scope |
|---|---|---|---|
| Owner | `owner` | `Passw0rd!2026` | Everything — all businesses, villas, financials, staff, audit log |
| Business Manager | `manager1` | `Passw0rd!2026` | Al Waab Properties only (both its villas) |
| Villa Staff | `staff1` | `Passw0rd!2026` | Villa 12 — Al Waab only |
| Villa Staff | `staff2` | `Passw0rd!2026` | Villa 7 — Muaither only |
| Accountant | `accountant1` | `Passw0rd!2026` | Al Waab Properties — financial data only, no villa/tenant management |

## What's seeded

- 2 businesses (Al Waab Properties, Pearl Residences), 6 villas, 16 partitions
- ~14 active tenants (a few partitions left vacant on purpose)
- 3 months of invoices per occupied partition — oldest fully paid, middle partially paid, most recent left unpaid (shows as overdue)
- Expenses across businesses/villas
- One cash handover already submitted and confirmed with a small discrepancy

## Sign in

`http://localhost:8000/accounts/login/` after running `manage.py runserver`.

Do not reuse these passwords anywhere real. This file documents a local development/demo database only.
