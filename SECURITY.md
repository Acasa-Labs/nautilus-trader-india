# Security

This package places real orders with real money. That makes two kinds of
problem a security problem here, and the second one is the more likely.

---

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private vulnerability reporting:
[**Report a vulnerability**](https://github.com/Acasa-Labs/nautilus-trader-india/security/advisories/new)
on the Security tab.

Include what you did, what happened, and what you expected. A proof of concept
helps; if it involves an order, use the [sandbox](https://sandbox.dhan.co/v2/)
rather than a live account.

Expect an acknowledgement within a week. This is an alpha package maintained
by one person, so that is a realistic figure rather than a service level. If a
report is confirmed, the fix and the advisory go out together, and you are
credited unless you ask not to be.

---

## What counts

Anything that leaks a credential or defeats a guard:

- **A credential reaching a log, a `__repr__`, an exception message, a
  serialised config, or a captured fixture.** Credentials are resolved as one
  set at client construction and are meant never to appear anywhere else.
  `DhanCredentials.__repr__` redacts the token; the market feed URL carries
  the token as a query parameter and has a `redacted_feed_url` for that
  reason. A path that bypasses either is a vulnerability.
- **Anything that lets an order be submitted with fewer than both switches
  set.** `live_orders=True` on the config and `NAUTILUS_INDIA_LIVE_ORDERS=1`
  in the environment are two switches rather than one precisely so that no
  single accident — a stray import, a stray environment variable — can put a
  real order on an exchange. A way around either is the most serious class of
  bug this package can have.
- **A committed fixture carrying real account data** — a client id, an order
  id, a balance, a holding.
- A dependency advisory affecting the installed set.

## What does not

- **That the software may lose you money.** It is alpha, it says so in three
  places, and MIT means it comes with no warranty. Trading defects are
  ordinary bugs — open an issue. The README's gap table lists the known ones.
- **Dhan's own API behaviour.** If Dhan returns something wrong or leaks
  something, report it to Dhan. If this package *handles* their response
  unsafely, that is ours.
- **A stolen credential that was never in this repository.** Rotate it with
  Dhan.

---

## Handling credentials, if you are using this

**The access token is a 24-hour JWT.** `exp` is exactly `iat + 86400` —
measured, and confirmed against `GET /v2/profile`. The clock starts at
*minting*, not at a fixed hour, so a token minted during market hours dies
during market hours.

- Keep `DHAN_CLIENT_ID` and `DHAN_ACCESS_TOKEN` in the environment. `.env` is
  git-ignored; `.env.example` carries names only.
- Leave `client_id` and `access_token` as `None` on the client configs. They
  then resolve from the environment at connect time, which keeps the token out
  of a serialised config — and a Nautilus config is serialisable, so a token
  in one is a token in whatever wrote it out.
- Never paste a token, a client id, an order record or a screenshot of your
  order book into an issue, a pull request or a discussion.

**If you have already exposed a token: rotate it now, before anything else.**
Generate a new one in Dhan's dashboard, which invalidates the old. Twenty-four
hours is a long time to leave a live trading credential in a public thread,
and rewriting git history does not un-publish what was already fetched.

---

## Reporting to Dhan

For a problem with Dhan's API itself rather than with this package, go to
[Dhan's support](https://dhanhq.co/) rather than here. If it is a behaviour
this package should defend against, open a **Venue behaviour** issue too — a
measured API defect is exactly the kind of thing
[`docs/DHAN_API_NOTES.md`](docs/DHAN_API_NOTES.md) exists to record.
