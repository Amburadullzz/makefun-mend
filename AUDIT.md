# AUDIT.md — SPEC.md, sentence by sentence, against main.ts

Scope: `main.ts` in this directory, audited against `SPEC.md` (version 1). Practice set: 100 of 100
(`node check.ts practice.json` → `100 of 100 passed: practice score 1000`).

Method. Every sentence of SPEC.md was turned into one requirement line below, with the section it comes
from, its status, and where in `main.ts` it lives. For every requirement practice cannot exercise, a check
was written under `tests/` and run; `main.ts` was changed only where it disagreed with the spec's plain
reading. Two independent verifications back the table:

* `tests/audit.json` — 88 hand-built checks, one per spec sentence practice does not show, run with
  `node check.ts tests/audit.json` (88 of 88 pass). Each check carries a `why` naming its sentence.
* `tests/ref.py` — a second implementation of SPEC.md written from the spec text; it scores 100 of 100
  against practice.json and was then fuzzed against `main.ts` by `tests/fuzz.py` (plausible patches plus
  mutations) and `tests/fuzz2.py` (token-soup patches, to compare parse errors and their precedence):
  about 6,000 generated cases, 0 disagreements after the fixes below.

Counts: **109 requirement lines — 104 IMPLEMENTED, 0 MISSING, 5 AMBIGUOUS.** Two of the 104 were MISSING
before this audit and are fixed here (7.2 and 10.2; see "Fixes").

---

## Section 0 — how a check is run (preface)

| # | Requirement | Status | Where |
|---|---|---|---|
| 0.1 | Every check runs `node main.ts [arguments]` once, in a fresh directory holding the check's files. | IMPLEMENTED | `run()` L858-945 takes the working directory as the tree root and uses only relative paths |
| 0.2 | The check compares the exit code, everything on stdout, and every file afterwards (path and bytes); standard error is never compared. | IMPLEMENTED | stdout is one `writeSync(1, …)` L954; a normal run writes nothing to fd 2 |

## Section 1 — Invocation

| # | Requirement | Status | Where |
|---|---|---|---|
| 1.1 | `node main.ts [--format=NAME] [--check] [--strict] [PATCH]`. | IMPLEMENTED | `run()` L858-874 |
| 1.2 | The working directory is the root of the tree; every path in a patch is relative to it. | IMPLEMENTED | L877 (`readFileSync(file ?? 0)`); L937-941 (`rmSync`/`mkdirSync`/`writeFileSync` on the path as written) |
| 1.3 | PATCH is a path relative to the working directory; absent or `-` means standard input. | IMPLEMENTED | L874-880 |
| 1.4 | `--format=NAME` reads the patch in one format: `unified`, `search-replace`, `v4a`, `json`; `auto` is the default and detects it. | IMPLEMENTED | `FORMATS` L18; L867; L882 |
| 1.5 | `--check` does everything but write: same output and exit code, no file created, changed or deleted. | IMPLEMENTED | L935 gates every write; rows and exit code are computed identically either way |
| 1.6 | `--strict` matches lines exactly (tier 0 only). | IMPLEMENTED | `findTier` L137-142; `applyHunks` L400; `v4aEdits` L696 |
| 1.7 | Options and PATCH may come in any order; `--` ends the options and every later argument is positional. | IMPLEMENTED | L864-872 |
| 1.8 | Usage error: an argument starting with `-` that is not `-` and not one of the options (before `--`), an option given twice, a `--format` value other than the five names, or more than one positional. | IMPLEMENTED | L866-873 |

## Section 2 — Output and exit codes

| # | Requirement | Status | Where |
|---|---|---|---|
| 2.1 | Standard output holds lines, each ended by `\n`, and nothing else. | IMPLEMENTED | L954 |
| 2.2 | Exit 2 writes nothing and prints exactly one line. | IMPLEMENTED | L870, L873, L879, L883, L892; no write can happen before L935 |
| 2.3 | `error: usage` on a usage error. | IMPLEMENTED | L870, L873 |
| 2.4 | `error: no-patch` when the PATCH file cannot be read. | IMPLEMENTED | L876-880 |
| 2.5 | `error: format` when `--format=auto` detects no format. | IMPLEMENTED | L882-883 |
| 2.6 | `error: parse <N>`, `<N>` the 1-based number of the patch line named in that format's section. | IMPLEMENTED | `Exit2` is raised at the naming site: L300, L318, L325, L335, L376 (unified); L535, L545 (S/R); L598, L600, L602, L623, L634, L644, L667, L674, L681, L683 (V4A); L812, L819, L821, L824, L837 (JSON) |
| 2.7 | A patch in which the reader finds no edit at all is `error: parse 1`. | IMPLEMENTED | L890 |
| 2.8 | The four exit-2 lines are tried in that order: usage wins over everything, and a parse error is reported before any file is looked at. | IMPLEMENTED | L864-894; the `Tree` is only built at L895, after all parsing |
| 2.9 | Exit 1 writes nothing and prints one `error: <code> <path>` per failed edit, in the patch's order. | IMPLEMENTED | L897-911 (failures pushed in edit order) |
| 2.10 | Then one line `failed: <k> of <n> edits`. | IMPLEMENTED | L911 |
| 2.11 | Code `no-match`. | IMPLEMENTED | L424, L563, L708, L738, L788, L802 |
| 2.12 | Code `ambiguous`. | IMPLEMENTED | L564, L789 |
| 2.13 | Code `missing`. | IMPLEMENTED | L469, L477, L487, L647, L691, L765 |
| 2.14 | Code `exists`. | IMPLEMENTED | L463, L478, L555, L638, L692, L772 |
| 2.15 | Code `unsafe`, naming the path as the patch wrote it after any prefix section 6 removes. | IMPLEMENTED | `pathOf` L259-263 strips `a/`/`b/` before the check; throws at L459, L460, L553, L637, L646, L688, L689, L763, L771 |
| 2.16 | The path on an error line is the path the failure concerns (the missing file, the path that exists, the file the lines were not found in). | IMPLEMENTED | L424 (the file searched), L469/L477 (missing), L463/L478 (exists), L647/L691, L765 |
| 2.17 | Exit 0: the tree is written; one line per changed path, in byte order of the paths (UTF-8). | IMPLEMENTED | L912-934 (`Buffer.compare` on the UTF-8 bytes, not JS string order) |
| 2.18 | Then `ok: <n> files`, or `ok: 1 file` when n is 1. | IMPLEMENTED | L944 |
| 2.19 | `A <path>`, `D <path>`, `M <path>` as defined. | IMPLEMENTED | L917 |
| 2.20 | `R <old> -> <new>` for a successful move edit when old existed before and not after, and new did not exist before and does after; it replaces that pair's D and A lines and sorts by `<new>`. | IMPLEMENTED | L919-933: the `taken` set (L920-928) removes the pair, `key: m.to` (L926) is the sort key |
| 2.21 | A file whose bytes are its bytes before is not listed; a patch that changes nothing prints only `ok: 0 files`. | IMPLEMENTED | L916 |

## Section 3 — Files, lines and paths

| # | Requirement | Status | Where |
|---|---|---|---|
| 3.1 | Files and patches are UTF-8 text. | IMPLEMENTED | `readFileSync(…, 'utf8')` L90, L877; `writeFileSync` with a string L940 |
| 3.2 | A path is safe when not empty, not starting with `/`, holding no `\`, and with no `/`-separated segment that is empty, `.` or `..`. | IMPLEMENTED | `isSafePath` L56-58 |
| 3.3 | Directories are created as files need them. | IMPLEMENTED | L939 |
| 3.4 | A file's break is CRLF when its first line feed follows a carriage return, LF otherwise; its lines are the text split at that break. | IMPLEMENTED | `ftOf` L69-77 |
| 3.5 | It ends with a break when its text ends with that break; that last empty piece is not a line. | IMPLEMENTED | `ftOf` L71-75 |
| 3.6 | An empty file has no lines and no final break; a file of no lines is written empty. | IMPLEMENTED | `ftOf` L70; `ftText` L80 |
| 3.7 | A changed file is written with its own break between lines and a final one exactly when it ended with one, unless section 6 says otherwise for a unified hunk. | IMPLEMENTED | `ftText` L79-81; `applyHunks` overrides `final` only at L429 |
| 3.8 | A file an edit creates is written with LF breaks and ends with one unless the format says otherwise; a JSON `create` writes `file_text` byte for byte. | IMPLEMENTED | `createdFT` L447 (`br: '\n'`); L556; L639; L769 |
| 3.9 | A patch's lines are its text split at `\n`, each with one trailing `\r` removed, without the empty piece after a final `\n`; numbered from 1. | IMPLEMENTED | `patchLines` L49-54 |

## Section 4 — Detecting the format

| # | Requirement | Status | Where |
|---|---|---|---|
| 4.1 | A blank line is one that is empty or only whitespace. | IMPLEMENTED | `blank` L35 |
| 4.2 | Rule 1: the first non-blank line, trimmed, is `*** Begin Patch` → `v4a`. | IMPLEMENTED | `detect` L847-849 |
| 4.3 | Rule 2: the first non-whitespace character of the first non-blank line is `[` or `{` → `json`. | IMPLEMENTED | L850-851 |
| 4.4 | Rule 3: some line, its trailing whitespace removed, is `<<<<<<< SEARCH` → `search-replace`. | IMPLEMENTED | L852 |
| 4.5 | Rule 4: some line starts with `diff --git `, or some line starts with `--- ` and the next starts with `+++ ` → `unified`. | IMPLEMENTED | L853 |
| 4.6 | Otherwise `error: format`. | IMPLEMENTED | L883 |
| 4.7 | A format named with `--format` is read as that format whatever the text holds. | IMPLEMENTED | L882 |

## Section 5 — Matching lines

| # | Requirement | Status | Where |
|---|---|---|---|
| 5.1 | Unified hunks, SEARCH/REPLACE blocks and V4A chunks name old lines O (one or more) replaced by new lines N. | **AMBIGUOUS** — see A1: 6.21 and 8.9 define behaviour for an *empty* O, contradicting "one or more". `main.ts` allows an empty O and applies those rules. | `bodyParts` L172-187 |
| 5.2 | O is found at the first of the three tiers at which it occurs at least once. | IMPLEMENTED | `findTier` L137-142; `applyHunks` L416-423; `v4aEdits` L717-737 |
| 5.3 | tier 0 all lines equal; tier 1 equal with trailing spaces and tabs removed; tier 2 equal with leading and trailing spaces and tabs removed. | IMPLEMENTED | `lineEq` L112-117 |
| 5.4 | With `--strict` only tier 0 is tried. | IMPLEMENTED | L138, L400, L696 |
| 5.5 | When O occurs at no tier the edit fails with `no-match`. | IMPLEMENTED | L424, L563, L708, L738 |
| 5.6 | An occurrence is a position i where O matches the file's lines i, i+1, …; occurrences may overlap. | IMPLEMENTED | `occurrences` L119-134 steps by 1 |
| 5.7 | Each format says which occurrence it takes when there are several. | IMPLEMENTED | nearest (6.20) L413-423; first at or after the cursor (8.9) L718-732; exactly one or `ambiguous` (7.8) L564 |
| 5.8 | N replaces the k matched lines. | IMPLEMENTED | L427, L568, L740 |
| 5.9 | A context line is written as the file's own matched line, never the patch's. | IMPLEMENTED | `renderN` L201-203 |
| 5.10 | At tier 2 every other line of N is re-indented: F = leading run of the file's first matched line, P = that of O's first line; a line of N that is not empty, not only whitespace, and starts with P has that P replaced by F; any other line is written as it is. | IMPLEMENTED | `renderN` L196-208 (the rule is L206) |
| 5.11 | At tiers 0 and 1 the lines of N are written as they are. | IMPLEMENTED | L206 (the re-indent is gated on `tier === 2`) |

## Section 6 — Unified diffs

| # | Requirement | Status | Where |
|---|---|---|---|
| 6.1 | A section starts at a `diff --git a/<X> b/<Y>` line, or at a `--- ` line followed by a `+++ ` line that is not already part of a section's header. | **AMBIGUOUS** — see A2 (a second `---`/`+++` pair while still in a header). | `parseUnified` L298-320 |
| 6.2 | `--- <path>` / `+++ <path>` name the old and new path: the text after the four characters, up to the first tab, surrounding spaces removed; `/dev/null` is no file; a leading `a/` or `b/` is removed from either. | IMPLEMENTED | `pathOf` L259-263 (the tab cut comes first, so "spaces" and "whitespace" are the same here) |
| 6.3 | `rename from` / `rename to` name the paths when no `---`/`+++` pair does. | IMPLEMENTED | L349-360; L452-453 |
| 6.4 | `new file mode` makes a creation, `deleted file mode` a deletion. | IMPLEMENTED | L361-370; L454-455 |
| 6.5 | The `diff --git` paths are used when nothing else names one. | IMPLEMENTED | L452-453 |
| 6.6 | `index`, `old mode`, `new mode`, `similarity index`, `dissimilarity index`, `Index:` and any other line are ignored. | IMPLEMENTED | L369-372 (every unrecognised header line falls through) |
| 6.7 | A `--- ` line not followed by `+++ `, inside a section's header, is `error: parse <the line after it>`. | IMPLEMENTED | L334-336 |
| 6.8 | A `diff --git` line not of that form is `error: parse <its line>`. | IMPLEMENTED | L299-300 |
| 6.9 | A hunk starts at `@@ -A[,B] +C[,D] @@` (optionally followed by text); any other line starting with `@@` is `error: parse <its line>`, and so is a hunk before any section. | IMPLEMENTED | `HUNK_RE` L256; L317-318; L323-325; L374-376 |
| 6.10 | B and D are not used. | IMPLEMENTED | `HUNK_RE` L256 captures only A and C |
| 6.11 | The body is the lines starting with ` `, `-`, `+` or `\`, or empty; an empty line is a context line with empty text. | IMPLEMENTED | `isBodyLine` L265-269; `bodyOf` L271-277 |
| 6.12 | The body ends at the first other line, at a `diff --git ` line, at a `--- ` line followed by `+++ `, or at the end; empty lines at the end of a body are not part of it. | IMPLEMENTED | L286-291 (`closeBody`); L384-390 |
| 6.13 | Outside bodies, lines that are not part of a section header are ignored. | IMPLEMENTED | L369-372; L389-390 |
| 6.14 | Creation: the new path does not exist (else `exists`); the file is the `+` lines of all its hunks in order. | IMPLEMENTED | L461-466; `createdFT` L435-448 |
| 6.15 | Deletion: the old path exists (else `missing`); it is deleted and its hunks are not read. | IMPLEMENTED | L467-472 (parsed for parse errors, never applied) |
| 6.16 | Move: old exists (else `missing`), new does not (else `exists`); hunks applied to the old lines, result written at the new path, old path deleted. | IMPLEMENTED | L473-483 |
| 6.17 | Otherwise a change of the one path, which exists (else `missing`). | IMPLEMENTED | L485-488 |
| 6.18 | Hunks apply in order, each to the result of the ones before. | IMPLEMENTED | L401 (`lines` is threaded through the loop) |
| 6.19 | d is the sum over this section's hunks applied so far of (N's length − O's length). | IMPLEMENTED | L399, L409, L428 |
| 6.20 | When O is not empty, the occurrence nearest to `A - 1 + d`; of two equally near, the earlier. | IMPLEMENTED | L413-423; `nearest` L146-155 (strict `<` keeps the earlier) |
| 6.21 | When O is empty, N is inserted after the first `A + d` lines (all of them when the file has fewer). | IMPLEMENTED | L405-412 |
| 6.22 | A `\` line refers to the body line before it. When the matched lines run to the end (or, for an empty O, the insertion is at the end) the result ends with a break unless a `\` line follows the hunk's last context or `+` line; when they do not, the file's final break stays as it was. A created file ends with a break unless a `\` follows its last `+` line. | **AMBIGUOUS** — see A3 ("follows" = immediately after?). `main.ts` requires the `\` to be the very next body line. | `noNewlineFlag` L189-192; L426-429; L441-443 |
| 6.23 | Any failure of a hunk fails the section with that hunk's code, and the section changes nothing. | IMPLEMENTED | L897-910 (the whole edit is rolled back from the snapshot at L899) |

## Section 7 — SEARCH/REPLACE blocks

| # | Requirement | Status | Where |
|---|---|---|---|
| 7.1 | The block form `<path>`, `<<<<<<< SEARCH`, old lines, `=======`, new lines, `>>>>>>> REPLACE`. | IMPLEMENTED | `searchReplaceEdits` L508-571 |
| 7.2 | The three marker lines are matched with their trailing whitespace removed. | IMPLEMENTED (was MISSING — fixed, see F1) | `trimEndWS` L39; L514, L521, L529 |
| 7.3 | The path is the nearest line before the SEARCH line and after the previous block's REPLACE line (or the start) that is not blank and whose trimmed form does not start with three backticks; the path is that line trimmed. | IMPLEMENTED | L537-542 |
| 7.4 | With no such line the block takes the previous block's path; the first block with none is `error: parse <its SEARCH line>`. | IMPLEMENTED | L543-545 |
| 7.5 | A SEARCH line with no `=======` and then `>>>>>>> REPLACE` after it is `error: parse <the SEARCH line>`. | IMPLEMENTED | L535 |
| 7.6 | Lines outside blocks are otherwise ignored. | IMPLEMENTED | L513-516 |
| 7.7 | Old lines empty: the file is created with the new lines (else `exists`). | IMPLEMENTED | L554-557 |
| 7.8 | Otherwise the file exists (else `missing`), and the old lines occur at their tier exactly once, or `ambiguous`; the new lines replace them. | IMPLEMENTED | L559-570 |

## Section 8 — V4A patches

| # | Requirement | Status | Where |
|---|---|---|---|
| 8.1 | The first non-blank line is `*** Begin Patch`, else `error: parse <that line>` (`error: parse 1` when there is none). | IMPLEMENTED | L598-600 |
| 8.2 | The last non-blank line is `*** End Patch`, else `error: parse <the number of patch lines plus 1>`; both are compared with their surrounding whitespace removed. | IMPLEMENTED | L601-602 |
| 8.3 | Blank lines at the end of a section are not part of it. | IMPLEMENTED | L626 |
| 8.4 | Sections start at `*** Add File: `, `*** Delete File: `, `*** Update File: `; the path is the rest of the line with its surrounding spaces removed. | IMPLEMENTED | L608-625 (`.trim()`; see the note under "Ambiguities" on "spaces") |
| 8.5 | Blank lines between sections are ignored. | IMPLEMENTED | L606-607, plus 8.3's strip at L626 |
| 8.6 | Any other line is `error: parse <its line>`, with the per-section rules: an Add section's lines start with `+` or are empty; a Delete section has no lines; an Update section may start with `*** Move to: <path>` then chunks; a chunk starts at an `@@` line (the text after `@@`, trimmed, is the anchor; empty is no anchor) or, for the first chunk, at a body line; body lines start with ` `, `-` or `+` or are empty; `*** End of File` ends a chunk; an Update section with no chunk and no Move to is `error: parse <its Update File line>`. | **AMBIGUOUS** — see A4 (`*** End of File` compared exactly or trimmed?). `main.ts` trims it (L666). | L625-683 |
| 8.7 | Add File: the path does not exist (else `exists`); the file is the section's lines, ended by a line break. | IMPLEMENTED | L630-640 |
| 8.8 | Delete File: the path exists (else `missing`); it is deleted. | IMPLEMENTED | L642-649 |
| 8.9 | Update File: the path exists (else `missing`); with Move to the new path does not (else `exists`); a cursor starts at line 0; an anchored chunk first finds the first line at or after the cursor equal to the anchor at the first tier where one is (else `no-match`) and the cursor moves past it; the chunk takes the first occurrence of O at or after the cursor at the first tier with one; an end-of-file chunk takes only an occurrence ending at the last line; with an empty O, N is inserted at the cursor (at the end for an end-of-file chunk); the cursor then moves to just after the lines written; with Move to the result is written at the new path and the old one deleted. | IMPLEMENTED | L653-751: cursor L695; anchor L697-709; body L711-741; empty O L712-717; move L744-749 |

## Section 9 — JSON edits

| # | Requirement | Status | Where |
|---|---|---|---|
| 9.1 | The patch is one JSON array of edit objects, or JSON Lines: one edit object a line, blank lines ignored. | IMPLEMENTED | `jsonEdits` L810-841 (array form when the first non-blank line starts with `[`) |
| 9.2 | In array form any fault is `error: parse <the line where the array starts>`; in JSON Lines form a line that does not hold one valid edit object is `error: parse <that line>`. | IMPLEMENTED | L812, L819, L821, L824, L837 |
| 9.3 | An edit object has a string `path` and a `command`; keys other than those below are ignored. | IMPLEMENTED | L757-762 |
| 9.4 | `create`: the file does not exist (else `exists`); it is written with `file_text`'s bytes. | IMPLEMENTED | L768-775 |
| 9.5 | `str_replace`: `old_str` a non-empty string, `new_str` a string (absent is empty); the file exists (else `missing`); in a CRLF file the text and both strings are read with every `\r\n` as `\n` and the result is written back with `\r\n`; `old_str` occurs exactly once (positions may overlap), else `no-match` or `ambiguous`; no tier applies. | IMPLEMENTED | L776-795 |
| 9.6 | `insert`: `insert_line` an integer, `new_str` a string; the file exists (else `missing`); `insert_line` from 0 to the number of lines (else `no-match`); `new_str`'s lines (split at `\n`, one trailing `\r` removed, no empty piece after a final `\n`) are inserted after line `insert_line` (0 is the top); the file's final break stays as it was. | IMPLEMENTED | L796-807 |
| 9.7 | A wrong command or a missing or mistyped key is a parse fault. | IMPLEMENTED | L762, L810 |

## Section 10 — Order and atomicity

| # | Requirement | Status | Where |
|---|---|---|---|
| 10.1 | Edits apply in the patch's order to the tree as earlier edits left it. | IMPLEMENTED | L898-910 over `Tree.now` (L85-107) |
| 10.2 | Every path an edit names is checked for safety before anything else about it. | **AMBIGUOUS** — see A5 (is the `---` path of a `new file mode` creation a path the edit "names"?). Resolved to the plain reading: all named paths are checked. Was MISSING for the extra path — fixed, see F2. | L456-460 |
| 10.3 | A failed edit changes nothing, and the edits after it still run, so every failure is reported. | IMPLEMENTED | L899-909 |
| 10.4 | When any edit failed, no file is written (exit 1); when none failed every change is written, or with `--check` none is. | IMPLEMENTED | L911 (returns before writing); L935 |

---

## Fixes

Both fixes are pinned by checks in `tests/audit.json` that fail on the previous code and pass now.

### F1 — §7.2: the SEARCH/REPLACE markers were matched with only spaces and tabs removed

The spec says "The three marker lines are matched with their trailing whitespace removed", and it says
"trailing spaces and tabs" only where it means exactly that (tier 1, §5.3). `main.ts` reused the tier-1
helper for the markers, so a marker line ending in any other whitespace (a form feed, say) was not
recognised and the block was never found.

```diff
+/** Trailing whitespace removed (the SEARCH/REPLACE markers, section 7.2). */
+const trimEndWS = (s: string): string => s.trimEnd();
...
-    if (trimEndST(lines[i]!) !== SR_SEARCH) {
+    if (trimEndWS(lines[i]!) !== SR_SEARCH) {
-      if (trimEndST(lines[j]!) === SR_EQ) {
+      if (trimEndWS(lines[j]!) === SR_EQ) {
-        if (trimEndST(lines[j]!) === SR_END) {
+        if (trimEndWS(lines[j]!) === SR_END) {
```

Check `a-marker/trailing-form-feed`: before the fix `error: parse 1` (no block found); after `M f`.
Check `a-marker/trailing-whitespace` (trailing spaces and tabs) passed before and after, so the practice
score could not show this.

### F2 — §10.2: a creation or deletion checked only the one path it works on

"Every path an edit names is checked for safety before anything else about it." A `new file mode` section
with `--- a/../x` names `../x`; `main.ts` only checked the new path, so it created the file instead of
reporting `unsafe`.

```diff
-    const named = isCreate ? [newPath] : isDelete ? [oldPath] : [oldPath, newPath];
-    for (const p of named) if (p === null || !isSafePath(p)) throw new Fail('unsafe', p ?? '');
+    // Section 10: every path the edit names is checked for safety before anything else about it, in the
+    // order the patch names them. A creation or a deletion still needs the one path it works on.
+    const essential = isCreate ? newPath : oldPath;
+    if (essential === null) throw new Fail('unsafe', '');
+    for (const p of [oldPath, newPath]) if (p !== null && !isSafePath(p)) throw new Fail('unsafe', p);
```

Check `a-unsafe/create-checks-the-old-path`: before `A ok.txt`; after `error: unsafe ../x`.
A `/dev/null` creation or deletion still passes, because the `/dev/null` side is `null` and is skipped.

Practice score after each fix, and at the end: 100 of 100 (1000).

---

## The three most ambiguous places in SPEC.md

**A1 — §5.1 "O (one or more)" against §6.21 and §8.9.** §5 says O is one or more lines; §6.21 ("When O is
empty, N is inserted after the first A + d lines") and §8.9 ("When O is empty, N is inserted at the cursor")
describe an empty O in detail. Either those two sentences are dead text, or §5's "one or more" is loose.
`main.ts` takes the second reading: a hunk whose body has no context or `-` lines is an insertion. Every
check with an `@@ -n,0 +m,k @@` header and only `+` lines depends on it, and the practice set confirms the
second reading.

**A2 — §6.1 "a `--- ` line followed by a `+++ ` line that is not already part of a section's header".** If a
section already has a `---`/`+++` pair and no hunk yet, a second pair is either (a) still part of that
header, so §6.2's "name the old and new path" applies again and the last pair wins for one section, or
(b) a new section. `main.ts` takes (a). The readings differ for two back-to-back `---`/`+++` pairs with no
hunk between them: one edit on the second path, or two edits on both.

**A3 — §6.22 "unless a `\` line follows the hunk's last context or `+` line".** "Follows" can mean
immediately after (a `\` line is written under the line it annotates, which is what the preceding sentence
"A `\` line … refers to the body line before it" suggests), or merely somewhere after it in the body.
`main.ts` takes the immediate reading: a `\` that sits after a `-` line is ignored and the hunk's final
break then follows the rule for a hunk that reaches the end of the file. The other reading would make such a
hunk drop the final break.

Two further ones, smaller. **§8.6's `*** End of File`** is the only marker the spec does not say to compare
"with their surrounding whitespace removed" (it says so for `*** Begin Patch` and `*** End Patch`), so
` *** End of File` is arguably a context line rather than a marker; `main.ts` trims it and treats it as a
marker. And **§6.2/§8.4's "surrounding spaces removed"** leaves open whether a path ending in a tab keeps
that tab; `main.ts` strips all surrounding whitespace, which cannot matter for §6.2 (the path is cut at the
first tab first) and could only matter for a V4A path line written with a trailing tab.
