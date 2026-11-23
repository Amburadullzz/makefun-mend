# tests/ — the audit's checks and tools

Everything here exists to audit `main.ts` against `SPEC.md` in the places `practice.json` cannot reach.
Nothing here is imported by `main.ts`; `runner.ts`, `check.ts`, `practice.json` and `SPEC.md` are untouched.

## The checks

* `audit.json` — 88 hand-built checks, one per SPEC.md sentence the practice set does not show. Each check
  carries a `why` field quoting the sentence it tests. Run them exactly like the practice set:

      node check.ts tests/audit.json
      node check.ts tests/audit.json a-order      # one family

  Families: `a-order` (the order of the errors in §2, usage errors), `a-noedit` (a patch with no edit at
  all), `a-unsafe` (unsafe paths, named as written), `a-r` (the R line and its sort key), `a-unchanged`
  (a file whose bytes end up unchanged), `a-sort` (UTF-8 byte order), `a-check` (`--check` writes nothing),
  `a-crlf` (CRLF files, final breaks), `a-tier2` (F/P re-indentation), `a-nearest` (nearest occurrence and
  ties), `a-empty-o` (empty-O insertion), `a-backslash` (the `\` line), `a-parse-lines` (§6 parse line
  numbers), `a-v4a-parse`, `a-v4a-cursor`, `a-json-form` (JSON Lines vs array, CRLF folding), `a-insert`
  (`insert_line` bounds), `a-sr-path` (§7 path rules), `a-marker` (marker whitespace, create, ambiguous),
  `a-order-edits` (§10 order and atomicity).

* `build_audit.py` — writes `audit.json` from the check definitions (edit this, not the JSON).

## The second implementation and the fuzzers

* `ref.py` — an independent implementation of SPEC.md written from the spec text. Scored against the
  practice set with `refcheck.py` (100 of 100), then used as an oracle.
* `refcheck.py` — `python3 tests/refcheck.py practice.json`: scores `ref.py` against the practice set.
* `fuzz.py` — `python3 tests/fuzz.py [n] [seed]`: generates plausible patches (all four formats) plus random
  mutations and compares `main.ts` with `ref.py`.
* `fuzz2.py` — `python3 tests/fuzz2.py [n] [seed]`: random token soup, to compare parse errors and the
  precedence of the exit-2 lines.
* `anomaly.py` — `python3 tests/anomaly.py [n] [seed]`: looks for crashes (exit outside 0-2, exit 1 with no
  output, a malformed `ok:` line).
* `diffrun.ts` — runs `main.ts` on a JSON list of cases and writes the results as JSON (used by the fuzzers).
* `probe.ts` — `node tests/probe.ts cases.json`: prints what `main.ts` does on each case, for exploring.
* `cases-probe*.json` — the exploratory case lists this audit was built from.

## Results at the end of the audit

    node check.ts practice.json          -> 100 of 100 passed: practice score 1000
    node check.ts tests/audit.json       -> 88 of 88 passed: practice score 1000
    python3 tests/refcheck.py practice.json -> 100 of 100 agree
    python3 tests/fuzz.py 200 31         -> 200 of 200 agree (0 differ)
    python3 tests/fuzz2.py 200 31        -> 200 of 200 agree (0 differ)
