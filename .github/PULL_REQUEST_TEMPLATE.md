## What this changes

<!-- One idea per pull request. Reviewing "and also" is how a real problem
     hides behind four cosmetic ones. -->

## What I verified, and what I did not

<!-- Required, and the most useful part of this template.

     No test in this repository opens a socket, so "the tests pass" is true of
     code that has never spoken to Dhan. Say what you actually ran it against:

       - unit tests only
       - against the sandbox (which is not production)
       - against a live account (say whether an order was placed, and whether
         anything filled)

     "I could not test this against a live account" is a welcome sentence.
     Silence in its place is not. -->

## Checklist

- [ ] Tests were written first, and they fail without this change
- [ ] `pytest` passes and `ruff check .` is clean
- [ ] No credential, token, client id, account number or real order record is
      in the diff
- [ ] Any new fixture is **captured, not composed**, is scrubbed, declares its
      `provenance`, and sits in the directory matching that claim
      (see `tests/dhan/fixtures/envelope/README.md`)
- [ ] No NautilusTrader source has been copied into this repository — it is
      LGPL-3.0 and this package is MIT
- [ ] No rule in `CLAUDE.md` is weakened. If one is, the measurement showing
      that its reason no longer holds is below

## Anything that changed a documented behaviour

<!-- If this alters something stated in README.md, ROADMAP.md,
     docs/DHAN_API_NOTES.md or docs/UPSTREAM_GAPS.md, update that page in the
     same pull request. A doc that drifts from the code is worse than no doc,
     and this repository's docs are load-bearing: people read the gap table to
     decide what to trust with money. -->
