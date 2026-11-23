#!/usr/bin/env python3
"""Adversarial robustness harness for mend.

Generates at least 3,000 patches across all four formats plus malformed ones,
runs `node main.ts` on each in a fresh scratch directory with a 10 second
timeout, and classifies the outcome:

    ok     exit 0/1/2 with stdout of exactly the shape SPEC.md section 2 gives
    crash  a stack trace, an exit outside 0/1/2, a hang, stdout that does not
           end in a newline, stdout that is not valid UTF-8, or stdout that is
           not the shape the exit code calls for
    hang   the run did not finish inside the timeout

Usage:
    python3 tests/robust.py                 # generate, run, report
    python3 tests/robust.py --jobs 16       # parallelism
    python3 tests/robust.py --limit 200     # first N cases only
    python3 tests/robust.py --tag targeted  # one generator family
    python3 tests/robust.py --replay tests/robust-failures.json

Exit status is 1 when any case crashed or hung, 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(ROOT, "main.ts")
NODE = os.environ.get("NODE_BIN", "node")
TIMEOUT = 10.0

# --------------------------------------------------------------------------- shape (SPEC.md section 2)

RE_EXIT2 = re.compile(r"^error: (usage|no-patch|format|parse [1-9][0-9]*)$")
RE_EDIT_ERR = re.compile(r"^error: (no-match|ambiguous|missing|exists|unsafe) ")
RE_FAILED = re.compile(r"^failed: ([0-9]+) of ([0-9]+) edits$")
RE_OK = re.compile(r"^ok: ([0-9]+) (file|files)$")
RE_CHG = re.compile(r"^[ADM] ")
RE_REN = re.compile(r"^R .+ -> .+$")
RE_TRACE = re.compile(r"(^|\n)\s+at\s|^\w*Error( |:|\[)|^\s+throw |\^+\s*$")


def check_shape(code: int, out: bytes, allow_multiline: bool = False) -> list[str]:
    """Every way `out` fails to be what SPEC.md says exit `code` prints.

    allow_multiline covers the one shape a patch can legally break: section 3 calls a path safe when it meets
    four conditions, and a path holding a line break meets all four, so its change or error line spans two
    lines. Those cases count the matching lines instead of demanding every line match.
    """
    try:
        text = out.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"stdout is not valid UTF-8 ({exc})"]
    if text == "":
        return ["stdout is empty"]
    problems: list[str] = []
    if not text.endswith("\n"):
        return ["stdout does not end with a newline"]
    if RE_TRACE.search(text):
        problems.append("stdout holds a stack trace")
    lines = text[:-1].split("\n")
    if code == 2:
        if len(lines) != 1:
            problems.append(f"exit 2 printed {len(lines)} lines, expected 1: {lines[:3]!r}")
        elif not RE_EXIT2.match(lines[0]):
            problems.append(f"exit 2 line is not spec-shaped: {lines[0]!r}")
        return problems
    if code == 1:
        if len(lines) < 2:
            problems.append(f"exit 1 printed {len(lines)} line(s), expected a failure summary")
            return problems
        m = RE_FAILED.match(lines[-1])
        if m is None:
            problems.append(f"exit 1 last line is not 'failed: <k> of <n> edits': {lines[-1]!r}")
            return problems
        k, n = int(m.group(1)), int(m.group(2))
        if n < 1:
            problems.append(f"exit 1 reported {n} edits")
        if k > n:
            problems.append(f"exit 1 reported {k} failures of {n} edits")
        body = lines[:-1]
        if allow_multiline:
            got = sum(1 for line in body if RE_EDIT_ERR.match(line))
            if k != got:
                problems.append(f"exit 1 says {k} failed but printed {got} error line(s)")
            return problems
        if k != len(body):
            problems.append(f"exit 1 says {k} failed but printed {len(body)} error line(s)")
        for line in body:
            if not RE_EDIT_ERR.match(line):
                problems.append(f"exit 1 line is not 'error: <code> <path>': {line!r}")
                break
        return problems
    # exit 0
    m = RE_OK.match(lines[-1])
    if m is None:
        problems.append(f"exit 0 last line is not 'ok: <n> files': {lines[-1]!r}")
        return problems
    n, word = int(m.group(1)), m.group(2)
    body = lines[:-1]
    if allow_multiline:
        got = sum(1 for line in body if RE_CHG.match(line) or RE_REN.match(line))
        if n != got:
            problems.append(f"exit 0 says {n} files but printed {got} change line(s)")
        return problems
    if n != len(body):
        problems.append(f"exit 0 says {n} files but printed {len(body)} change line(s)")
    if n == 1 and word != "file":
        problems.append("exit 0 says 'ok: 1 files'")
    if n != 1 and word != "files":
        problems.append(f"exit 0 says 'ok: {n} {word}'")
    for line in body:
        if not (RE_CHG.match(line) or RE_REN.match(line)):
            problems.append(f"exit 0 line is not 'A|D|M <path>' or 'R <old> -> <new>': {line!r}")
            break
    return problems


# --------------------------------------------------------------------------- cases

@dataclass
class Case:
    name: str
    tag: str
    argv: list = field(default_factory=list)
    stdin: bytes | None = None
    files: dict = field(default_factory=dict)   # path -> bytes
    allow_multiline: bool = False               # a path the patch names holds a line break

    def as_json(self) -> dict:
        def enc(v):
            return v.decode("utf-8", "backslashreplace") if isinstance(v, bytes) else v
        return {
            "name": self.name,
            "tag": self.tag,
            "argv": self.argv,
            "stdin": None if self.stdin is None else self.stdin[:200000].decode("utf-8", "backslashreplace"),
            "files": {p: enc(b) for p, b in self.files.items()},
            "allow_multiline": self.allow_multiline,
        }


@dataclass
class Outcome:
    case: Case
    status: str          # ok | crash | hang
    problems: list
    code: int | None
    seconds: float
    stdout: bytes
    stderr: bytes

    @property
    def signature(self) -> str:
        if self.status == "hang":
            return "HANG (>%.0fs)" % TIMEOUT
        bits = list(self.problems)
        err = self.stderr.decode("utf-8", "backslashreplace")
        m = re.search(r"^\s*(\w*Error[^\n]*)", err, re.M)
        if m:
            bits.append(m.group(1).strip()[:160])
        return " | ".join(bits) if bits else self.status


BASE_FILES = {
    "a.txt": "one\ntwo\nthree\n",
    "b.txt": "x\n",
    "dir/c.txt": "alpha\nbeta\ngamma\n",
    "crlf.txt": "one\r\ntwo\r\nthree\r\n",
    "empty.txt": "",
    "nonl.txt": "one\ntwo",
    "dup.txt": "same\nsame\nsame\n",
}


def enc_files(files: dict) -> dict:
    return {p: (v.encode("utf-8") if isinstance(v, str) else v) for p, v in files.items()}


def C(name, tag, text=None, argv=(), files=None, stdin=None, raw=None) -> Case:
    if raw is not None:
        body = raw
    elif text is None:
        body = None
    else:
        body = text.encode("utf-8") if isinstance(text, str) else text
    return Case(name=name, tag=tag, argv=list(argv), stdin=body, files=enc_files(files or {}))


# --------------------------------------------------------------------------- unified diffs

U_VARIANTS = ([], ["--check"], ["--strict"], ["--format=unified"], ["--check", "--strict"])

UNIFIED: list[tuple[str, str, dict, bool]] = [
    ("simple", "--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n", {}, True),
    ("create", "diff --git a/new.txt b/new.txt\nnew file mode 100644\n--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,2 @@\n+hello\n+world\n", {}, True),
    ("delete", "diff --git a/b.txt b/b.txt\ndeleted file mode 100644\n--- a/b.txt\n+++ /dev/null\n", {}, True),
    ("rename", "diff --git a/b.txt b/c.txt\nsimilarity index 100%\nrename from b.txt\nrename to c.txt\n", {}, True),
    ("move-hunks", "--- a/b.txt\n+++ b/d.txt\n@@ -1 +1 @@\n-x\n+y\n", {}, True),
    ("two-hunks", "--- a/a.txt\n+++ b/a.txt\n@@ -1,1 +1,1 @@\n-one\n+ONE\n@@ -3,1 +3,1 @@\n-three\n+THREE\n", {}, True),
    ("wrong-lineno", "--- a/a.txt\n+++ b/a.txt\n@@ -999,1 +999,1 @@\n-two\n+TWO\n", {}, True),
    ("tier1-trailing", "--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one   \n-two\t\n+TWO\n three\n", {}, True),
    ("tier2-indent", "--- a/dir/c.txt\n+++ b/dir/c.txt\n@@ -1,3 +1,3 @@\n  alpha\n-beta\n+beta!\n  gamma\n", {}, True),
    ("no-newline", "--- a/nonl.txt\n+++ b/nonl.txt\n@@ -1,2 +1,2 @@\n one\n-two\n\\ No newline at end of file\n+TWO\n\\ No newline at end of file\n", {}, True),
    ("add-at-end", "--- a/a.txt\n+++ b/a.txt\n@@ -3,0 +4,1 @@\n+four\n", {}, True),
    ("prose-fence", "Here is the patch:\n```diff\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n```\nDone.\n", {}, True),
    ("index-lines", "diff --git a/a.txt b/a.txt\nindex 1234567..89abcde 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -2 +2 @@\n-two\n+TWO\n", {}, True),
    ("two-sections", "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-x\n+y\n", {}, True),
    ("same-path-twice", "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n--- a/a.txt\n+++ b/a.txt\n@@ -3 +3 @@\n-three\n+THREE\n", {}, True),
    ("create-then-delete", "--- /dev/null\n+++ b/tmp.txt\n@@ -0,0 +1 @@\n+hi\n--- a/tmp.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-hi\n", {}, True),
    ("delete-then-create", "--- a/b.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n--- /dev/null\n+++ b/b.txt\n@@ -0,0 +1 @@\n+z\n", {}, True),
    ("count-mismatch", "--- a/a.txt\n+++ b/a.txt\n@@ -1,99 +1,99 @@\n-one\n+TWO\n three\n", {}, True),
    ("hunk-empty-body", "--- a/a.txt\n+++ b/a.txt\n@@ -0,0 +1,0 @@\n", {}, True),
    ("insert-at-top", "--- a/a.txt\n+++ b/a.txt\n@@ -0,0 +1,1 @@\n+first\n", {}, True),
    ("crlf-file", "--- a/crlf.txt\n+++ b/crlf.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n", {}, True),
    ("rename-no-diff", "--- a/b.txt\n+++ b/e.txt\n@@ -1 +1 @@\n-x\n+y\n", {}, True),
    ("devnull-both", "--- /dev/null\n+++ /dev/null\n@@ -0,0 +1 @@\n+x\n", {}, True),
    ("move-onto-existing", "--- a/b.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-x\n+y\n", {}, True),
    ("delete-missing", "--- a/nope.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n", {}, True),
    ("unsafe-dotdot", "--- a/../evil.txt\n+++ b/../evil.txt\n@@ -1 +1 @@\n-x\n+y\n", {}, True),
    ("unsafe-abs", "--- /etc/passwd\n+++ /etc/passwd\n@@ -1 +1 @@\n-x\n+y\n", {}, True),
    ("unsafe-empty-seg", "--- a//x.txt\n+++ b//x.txt\n@@ -1 +1 @@\n-x\n+y\n", {}, True),
    ("unsafe-backslash", "--- a/x\\\\y.txt\n+++ b/x\\\\y.txt\n@@ -1 +1 @@\n-x\n+y\n", {}, True),
    ("unicode-path", "--- a/héllo/файл.txt\n+++ b/héllo/файл.txt\n@@ -1 +1 @@\n-x\n+y\n", {}, True),
    ("context-only", "--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one\n two\n three\n", {}, True),
    ("bad-diff-line", "diff --git x y\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n", {}, False),
    ("dashdash-no-plus", "--- a/a.txt\n", {}, False),
    ("garbage-hunk", "--- a/a.txt\n+++ b/a.txt\n@@ nonsense @@\n-one\n+ONE\n", {}, False),
    ("hunk-no-section", "@@ -1 +1 @@\n-one\n+ONE\n", {}, False),
    ("header-tab", "--- a/a.txt\t2024-01-01 00:00:00\n+++ b/a.txt\t2024-01-02 00:00:00\n@@ -1 +1 @@\n-one\n+ONE\n", {}, True),
    ("new-mode-no-devnull", "diff --git a/new2.txt b/new2.txt\nnew file mode 100644\n@@ -0,0 +1 @@\n+fresh\n", {}, True),
    ("hunk-plus-only-empty", "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n", {}, True),
    ("ambiguous-dup", "--- a/dup.txt\n+++ b/dup.txt\n@@ -1 +1 @@\n-same\n+diff\n", {}, True),
    ("no-match", "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-absent\n+present\n", {}, True),
    ("move-then-edit", "--- a/b.txt\n+++ b/moved.txt\n@@ -1 +1 @@\n-x\n+xx\n--- a/moved.txt\n+++ b/moved.txt\n@@ -1 +1 @@\n+xx\n+yy\n", {}, True),
    ("nested-deep", "--- a/" + "d/" * 40 + "f.txt\n+++ b/" + "d/" * 40 + "f.txt\n@@ -0,0 +1 @@\n+deep\n", {}, True),
]


def gen_unified():
    out = []
    for name, text, files, valid in UNIFIED:
        f = dict(BASE_FILES)
        f.update(files)
        for i, argv in enumerate(U_VARIANTS):
            out.append(C(f"u-{name}-{i}", "unified", text, argv, f))
    return out


# --------------------------------------------------------------------------- SEARCH/REPLACE

SR_VARIANTS = ([], ["--check"], ["--strict"], ["--format=search-replace"])

SEARCH_REPLACE = [
    ("basic", "a.txt\n<<<<<<< SEARCH\n two\n=======\n TWO\n>>>>>>> REPLACE\n", True),
    ("create", "brand.txt\n<<<<<<< SEARCH\n=======\n+hello\n>>>>>>> REPLACE\n", True),
    ("multi", "a.txt\n<<<<<<< SEARCH\n one\n=======\n ONE\n>>>>>>> REPLACE\n\nb.txt\n<<<<<<< SEARCH\n x\n=======\n y\n>>>>>>> REPLACE\n", True),
    ("reuse-path", "a.txt\n<<<<<<< SEARCH\n one\n=======\n ONE\n>>>>>>> REPLACE\n<<<<<<< SEARCH\n two\n=======\n TWO\n>>>>>>> REPLACE\n", True),
    ("fenced", "```text\na.txt\n<<<<<<< SEARCH\n two\n=======\n TWO\n>>>>>>> REPLACE\n```\n", True),
    ("crlf-file", "crlf.txt\n<<<<<<< SEARCH\n two\n=======\n TWO\n>>>>>>> REPLACE\n", True),
    ("ambiguous", "dup.txt\n<<<<<<< SEARCH\n same\n=======\n diff\n>>>>>>> REPLACE\n", True),
    ("no-match", "a.txt\n<<<<<<< SEARCH\n absent\n=======\n present\n>>>>>>> REPLACE\n", True),
    ("missing-file", "nope.txt\n<<<<<<< SEARCH\n x\n=======\n y\n>>>>>>> REPLACE\n", True),
    ("unsafe", "../evil.txt\n<<<<<<< SEARCH\n x\n=======\n y\n>>>>>>> REPLACE\n", True),
    ("exists", "a.txt\n<<<<<<< SEARCH\n=======\n+new\n>>>>>>> REPLACE\n", True),
    ("markers-trailing-ws", "a.txt\n<<<<<<< SEARCH   \n two\n=======  \n TWO\n>>>>>>> REPLACE\t\n", True),
    ("blank-before-path", "\n\n a.txt \n\n<<<<<<< SEARCH\n two\n=======\n TWO\n>>>>>>> REPLACE\n", True),
    ("empty-replace", "a.txt\n<<<<<<< SEARCH\n two\n=======\n>>>>>>> REPLACE\n", True),
    ("multi-line-old", "dir/c.txt\n<<<<<<< SEARCH\n alpha\n beta\n=======\n ALPHA\n BETA\n>>>>>>> REPLACE\n", True),
    ("no-path", "<<<<<<< SEARCH\n two\n=======\n TWO\n>>>>>>> REPLACE\n", False),
    ("no-eq", "a.txt\n<<<<<<< SEARCH\n two\n>>>>>>> REPLACE\n", False),
    ("no-end", "a.txt\n<<<<<<< SEARCH\n two\n=======\n TWO\n", False),
    ("search-only", "a.txt\n<<<<<<< SEARCH\n", False),
    ("prose-only", "just some prose\nand nothing else\n", False),
    ("create-then-delete", "t.txt\n<<<<<<< SEARCH\n=======\n+hi\n>>>>>>> REPLACE\nt.txt\n<<<<<<< SEARCH\n hi\n=======\n>>>>>>> REPLACE\n", True),
]


def gen_search_replace():
    out = []
    for name, text, valid in SEARCH_REPLACE:
        for i, argv in enumerate(SR_VARIANTS):
            out.append(C(f"s-{name}-{i}", "search-replace", text, argv, BASE_FILES))
    return out


# --------------------------------------------------------------------------- V4A

V4A_VARIANTS = ([], ["--check"], ["--strict"], ["--format=v4a"])

V4A = [
    ("add", "*** Begin Patch\n*** Add File: fresh.txt\n+hello\n+world\n*** End Patch\n", True),
    ("delete", "*** Begin Patch\n*** Delete File: b.txt\n*** End Patch\n", True),
    ("update", "*** Begin Patch\n*** Update File: a.txt\n@@\n one\n-two\n+TWO\n three\n*** End Patch\n", True),
    ("update-anchor", "*** Begin Patch\n*** Update File: dir/c.txt\n@@ beta\n-beta\n+beta!\n*** End Patch\n", True),
    ("move", "*** Begin Patch\n*** Update File: b.txt\n*** Move to: moved.txt\n@@\n-x\n+y\n*** End Patch\n", True),
    ("move-only", "*** Begin Patch\n*** Update File: b.txt\n*** Move to: moved.txt\n*** End Patch\n", True),
    ("eof", "*** Begin Patch\n*** Update File: a.txt\n@@\n three\n+four\n*** End of File\n*** End Patch\n", True),
    ("insert-empty-o", "*** Begin Patch\n*** Update File: a.txt\n@@\n+zero\n*** End Patch\n", True),
    ("multi-sections", "*** Begin Patch\n*** Update File: a.txt\n@@\n-one\n+ONE\n*** Add File: z.txt\n+z\n*** Delete File: b.txt\n*** End Patch\n", True),
    ("blank-between", "*** Begin Patch\n\n*** Update File: a.txt\n@@\n-one\n+ONE\n\n*** End Patch\n\n", True),
    ("surrounding-ws", "  *** Begin Patch  \n*** Update File: a.txt\n@@\n-one\n+ONE\n  *** End Patch  \n", True),
    ("crlf-file", "*** Begin Patch\n*** Update File: crlf.txt\n@@\n-two\n+TWO\n*** End Patch\n", True),
    ("no-match", "*** Begin Patch\n*** Update File: a.txt\n@@\n-absent\n+present\n*** End Patch\n", True),
    ("missing", "*** Begin Patch\n*** Update File: nope.txt\n@@\n-x\n+y\n*** End Patch\n", True),
    ("exists", "*** Begin Patch\n*** Add File: a.txt\n+x\n*** End Patch\n", True),
    ("unsafe", "*** Begin Patch\n*** Add File: ../evil.txt\n+x\n*** End Patch\n", True),
    ("no-end", "*** Begin Patch\n*** Add File: f.txt\n+x\n", False),
    ("no-begin", "*** Add File: f.txt\n+x\n*** End Patch\n", False),
    ("garbage-line", "*** Begin Patch\n*** Add File: f.txt\n+x\nnot a body line\n*** End Patch\n", False),
    ("delete-with-body", "*** Begin Patch\n*** Delete File: b.txt\n+x\n*** End Patch\n", False),
    ("update-no-chunk", "*** Begin Patch\n*** Update File: a.txt\n*** End Patch\n", False),
    ("empty", "*** Begin Patch\n*** End Patch\n", False),
    ("only-markers", "*** Begin Patch\n*** End Patch\n*** Begin Patch\n", False),
]


def gen_v4a():
    out = []
    for name, text, valid in V4A:
        for i, argv in enumerate(V4A_VARIANTS):
            out.append(C(f"v-{name}-{i}", "v4a", text, argv, BASE_FILES))
    return out


# --------------------------------------------------------------------------- JSON

JSON_VARIANTS = ([], ["--check"], ["--format=json"])

JSON_CASES = [
    ("create", '[{"command":"create","path":"n.txt","file_text":"hi\\n"}]', True),
    ("str-replace", '[{"command":"str_replace","path":"a.txt","old_str":"two","new_str":"TWO"}]', True),
    ("str-replace-no-new", '[{"command":"str_replace","path":"a.txt","old_str":"two\\n"}]', True),
    ("insert", '[{"command":"insert","path":"a.txt","insert_line":1,"new_str":"mid\\n"}]', True),
    ("insert-top", '[{"command":"insert","path":"a.txt","insert_line":0,"new_str":"top\\n"}]', True),
    ("insert-end", '[{"command":"insert","path":"a.txt","insert_line":3,"new_str":"end"}]', True),
    ("insert-out-of-range", '[{"command":"insert","path":"a.txt","insert_line":99,"new_str":"x"}]', True),
    ("crlf-str", '[{"command":"str_replace","path":"crlf.txt","old_str":"two","new_str":"TWO"}]', True),
    ("multi", '[{"command":"create","path":"n1.txt","file_text":"a"},{"command":"str_replace","path":"n1.txt","old_str":"a","new_str":"b"}]', True),
    ("lines", '{"command":"create","path":"n.txt","file_text":"a\\n"}\n\n{"command":"str_replace","path":"n.txt","old_str":"a","new_str":"b"}\n', True),
    ("jsonl-blank", '\n\n{"command":"create","path":"n.txt","file_text":"a"}\n', True),
    ("ambiguous", '[{"command":"str_replace","path":"dup.txt","old_str":"same","new_str":"x"}]', True),
    ("no-match", '[{"command":"str_replace","path":"a.txt","old_str":"zzz","new_str":"x"}]', True),
    ("missing", '[{"command":"str_replace","path":"nope.txt","old_str":"a","new_str":"x"}]', True),
    ("exists", '[{"command":"create","path":"a.txt","file_text":"x"}]', True),
    ("unsafe", '[{"command":"create","path":"../x","file_text":"x"}]', True),
    ("unicode", '[{"command":"create","path":"héllo/файл.txt","file_text":"日本語\\n"}]', True),
    ("empty-array", "[]", False),
    ("truncated", '[{"command":"create","path":"n.txt","file_text":"hi"', False),
    ("truncated-2", '[', False),
    ("wrong-type-path", '[{"command":"create","path":5,"file_text":"x"}]', False),
    ("wrong-type-command", '[{"command":"rename","path":"a.txt"}]', False),
    ("empty-old-str", '[{"command":"str_replace","path":"a.txt","old_str":""}]', False),
    ("insert-float", '[{"command":"insert","path":"a.txt","insert_line":1.5,"new_str":"x"}]', False),
    ("insert-string", '[{"command":"insert","path":"a.txt","insert_line":"1","new_str":"x"}]', False),
    ("top-object", '{"command":"create","path":"n.txt","file_text":"x"}', False),
    ("array-of-null", '[null]', False),
    ("array-of-array", '[[{"command":"create","path":"n.txt","file_text":"x"}]]', False),
    ("nested-deep", "[" * 500 + "]" * 500, False),
    ("bad-jsonl-line", '{"command":"create","path":"n.txt","file_text":"x"}\nnope\n', False),
    ("no-file-text", '[{"command":"create","path":"n.txt"}]', False),
    ("extra-keys", '[{"command":"create","path":"n.txt","file_text":"x","extra":{"a":[1,2]}}]', True),
]


def gen_json():
    out = []
    for name, text, valid in JSON_CASES:
        for i, argv in enumerate(JSON_VARIANTS):
            out.append(C(f"j-{name}-{i}", "json", text, argv, BASE_FILES))
    return out


# --------------------------------------------------------------------------- targeted malformed inputs

def gen_targeted():
    out: list[Case] = []

    def add(name, raw, argv=(), files=None, multiline=False):
        c = C(name, "targeted", argv=argv, files=files or BASE_FILES, raw=raw)
        c.allow_multiline = multiline
        out.append(c)

    # empty and whitespace only, in every format
    for argv in ([], ["--format=unified"], ["--format=search-replace"], ["--format=v4a"], ["--format=json"]):
        tag = argv[0].split("=")[-1] if argv else "auto"
        add(f"t-empty-{tag}", b"", argv)
        add(f"t-ws-{tag}", b"   \n\t\n  \t \n", argv)
        add(f"t-ws-crlf-{tag}", b" \r\n\t\r\n", argv)
        add(f"t-one-newline-{tag}", b"\n", argv)

    # binary bytes and invalid UTF-8
    blobs = {
        "nul": b"\x00\x01\x02\x03",
        "ff": b"\xff\xfe\xfd",
        "lone-cont": b"abc\x80def\n",
        "truncated-mb": b"abc\xe2\x82\n",
        "overlong": b"abc\xc0\xaf\n",
        "utf16": "--- a/a.txt\n+++ b/a.txt\n".encode("utf-16"),
        "random-binary": bytes(range(256)) * 4,
    }
    for name, blob in blobs.items():
        for argv in ([], ["--format=json"], ["--format=unified"], ["--format=v4a"], ["--format=search-replace"]):
            add(f"t-bin-{name}-{argv[0].split('=')[-1] if argv else 'auto'}", blob, argv)

    # invalid UTF-8 inside a JSON string / inside a path
    add("t-bin-json-path", b'[{"command":"create","path":"a\xffb","file_text":"x"}]')
    add("t-bin-json-text", b'[{"command":"create","path":"n.txt","file_text":"\xff\xfe"}]')
    add("t-bin-unified-path", b"--- a/a\xff.txt\n+++ b/a\xff.txt\n@@ -1 +1 @@\n-x\n+y\n")
    add("t-bin-v4a-path", b"*** Begin Patch\n*** Add File: a\xff.txt\n+x\n*** End Patch\n")
    add("t-bin-sr-path", b"a\xff.txt\n<<<<<<< SEARCH\n x\n=======\n y\n>>>>>>> REPLACE\n")

    # very long lines: 1 MB
    long1m = "x" * (1024 * 1024)
    add("t-1mb-unified-line", ("--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-" + long1m + "\n+y\n").encode())
    add("t-1mb-plus-line", ("--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+" + long1m + "\n").encode())
    add("t-1mb-context", ("--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n " + long1m + "\n two\n").encode())
    add("t-1mb-json", ('[{"command":"create","path":"n.txt","file_text":"' + long1m + '"}]').encode())
    add("t-1mb-jsonl-old", ('{"command":"str_replace","path":"a.txt","old_str":"' + long1m + '","new_str":"x"}').encode())
    add("t-1mb-v4a", ("*** Begin Patch\n*** Add File: n.txt\n+" + long1m + "\n*** End Patch\n").encode())
    add("t-1mb-sr", ("a.txt\n<<<<<<< SEARCH\n" + long1m + "\n=======\n+y\n>>>>>>> REPLACE\n").encode())
    add("t-1mb-no-newline", ("--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-" + long1m).encode())
    add("t-1mb-spaces", ("--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+" + (" " * (1024 * 1024)) + "x\n").encode())
    add("t-1mb-argv-path", b"", ["--format=unified"], {"a.txt": "one\n", "x" * 200 + ".patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n"})

    # 10,000-line patches
    lines = [f"line{i}" for i in range(10000)]
    file10k = "\n".join(lines) + "\n"
    body = "\n".join(" " + l for l in lines)
    add("t-10k-unified-context", ("--- a/big.txt\n+++ b/big.txt\n@@ -1,10000 +1,10000 @@\n" + body + "\n").encode(), files={"big.txt": file10k})
    add("t-10k-unified-change", ("--- a/big.txt\n+++ b/big.txt\n@@ -1,10000 +1,10000 @@\n" + "\n".join("-" + l + "\n+" + l + "!" for l in lines) + "\n").encode(), files={"big.txt": file10k})
    add("t-10k-unified-hunks", ("--- a/big.txt\n+++ b/big.txt\n" + "".join(f"@@ -{i+1} +{i+1} @@\n-{l}\n+{l}!\n" for i, l in enumerate(lines))).encode(), files={"big.txt": file10k})
    add("t-10k-v4a", ("*** Begin Patch\n*** Add File: big2.txt\n" + "\n".join("+" + l for l in lines) + "\n*** End Patch\n").encode())
    add("t-10k-v4a-chunks", ("*** Begin Patch\n*** Update File: big.txt\n" + "".join(f"@@ {l}\n-{l}\n+{l}!\n" for l in lines) + "*** End Patch\n").encode(), files={"big.txt": file10k})
    add("t-10k-sr", ("big.txt\n<<<<<<< SEARCH\n" + "\n".join(lines) + "\n=======\n" + "\n".join(l + "!" for l in lines) + "\n>>>>>>> REPLACE\n").encode(), files={"big.txt": file10k})
    add("t-10k-sr-blocks", ("big.txt\n" + "".join(f"<<<<<<< SEARCH\n{l}\n=======\n{l}!\n>>>>>>> REPLACE\n" for l in lines)).encode(), files={"big.txt": file10k})
    add("t-10k-json-array", ("[" + ",".join(f'{{"command":"create","path":"f{i}.txt","file_text":"x"}}' for i in range(10000)) + "]").encode())
    add("t-10k-jsonl", ("".join(f'{{"command":"create","path":"f{i}.txt","file_text":"x"}}\n' for i in range(10000))).encode())
    add("t-10k-json-failing", ("[" + ",".join(f'{{"command":"str_replace","path":"a.txt","old_str":"nope{i}","new_str":"x"}}' for i in range(10000)) + "]").encode())
    add("t-10k-empty-lines", ("\n" * 10000 + "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n").encode())
    add("t-10k-prose", ("prose line\n" * 10000 + "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n").encode())

    # deeply nested paths
    for depth in (100, 500, 1500, 3000):
        p = "d/" * depth + "f.txt"
        add(f"t-deep-{depth}-u", f"--- /dev/null\n+++ b/{p}\n@@ -0,0 +1 @@\n+x\n".encode())
        add(f"t-deep-{depth}-j", json.dumps([{"command": "create", "path": p, "file_text": "x"}]).encode())
        add(f"t-deep-{depth}-v", f"*** Begin Patch\n*** Add File: {p}\n+x\n*** End Patch\n".encode())
    add("t-long-seg-u", ("--- /dev/null\n+++ b/" + "n" * 300 + ".txt\n@@ -0,0 +1 @@\n+x\n").encode())
    add("t-long-seg-j", json.dumps([{"command": "create", "path": "n" * 300 + ".txt", "file_text": "x"}]).encode())
    add("t-long-total-u", ("--- /dev/null\n+++ b/" + "/".join(["n" * 200] * 25) + "\n@@ -0,0 +1 @@\n+x\n").encode())

    # .. and absolute paths
    for p in ("..", "../x", "a/../../b", "./x", "/abs/x", "a/./b", "a//b", "", "a/", "/"):
        add(f"t-path-{p or 'empty'}-j", json.dumps([{"command": "create", "path": p, "file_text": "x"}]).encode())
        add(f"t-path-{p or 'empty'}-u", f"--- /dev/null\n+++ b/{p}\n@@ -0,0 +1 @@\n+x\n".encode())
        add(f"t-path-{p or 'empty'}-sr", f"{p}\n<<<<<<< SEARCH\n x\n=======\n y\n>>>>>>> REPLACE\n".encode())

    # unicode and control characters in paths
    weird = ["héllo", "日本語", "🎉", "a\u0301", "\u200bzero", "a\x01b", "a\x07b", "a\x1bb", "a\x7fb", "a\tb", "a b", "a\nb", "a\rb", "a\u202eb", "\ufeffbom"]
    for p in weird:
        ml = "\n" in p
        add("t-wp-j-" + p.encode("unicode_escape").decode(), json.dumps([{"command": "create", "path": p, "file_text": "x"}]).encode(), multiline=ml)
        add("t-wp-sr-" + p.encode("unicode_escape").decode(), f"{p}\n<<<<<<< SEARCH\n x\n=======\n y\n>>>>>>> REPLACE\n".encode(), multiline=ml)
    add("t-nul-path-j", b'[{"command":"create","path":"a\\u0000b","file_text":"x"}]')
    add("t-nul-path-u", b"--- /dev/null\n+++ b/a\x00b\n@@ -0,0 +1 @@\n+x\n")
    add("t-nul-path-v", b"*** Begin Patch\n*** Add File: a\x00b\n+x\n*** End Patch\n")

    # CRLF and mixed line endings
    add("t-crlf-unified", b"--- a/crlf.txt\r\n+++ b/crlf.txt\r\n@@ -1,3 +1,3 @@\r\n one\r\n-two\r\n+TWO\r\n three\r\n")
    add("t-crlf-v4a", b"*** Begin Patch\r\n*** Update File: crlf.txt\r\n@@\r\n-two\r\n+TWO\r\n*** End Patch\r\n")
    add("t-crlf-sr", b"crlf.txt\r\n<<<<<<< SEARCH\r\n two\r\n=======\r\n TWO\r\n>>>>>>> REPLACE\r\n")
    add("t-mixed-eol", b"--- a/a.txt\r\n+++ b/a.txt\n@@ -1 +1 @@\r\n-one\n+TWO\r\n")
    add("t-cr-only", b"--- a/a.txt\r+++ b/a.txt\r@@ -1 +1 @@\r-one\r+TWO\r")
    add("t-mixed-eol-json", b'[{"command":"create","path":"n.txt","file_text":"a\\r\\nb\\nc"}]')

    # hunks whose counts disagree with their bodies
    add("t-count-huge", ("--- a/a.txt\n+++ b/a.txt\n@@ -1,999999 +1,999999 @@\n-one\n+ONE\n").encode())
    add("t-count-zero-body", ("--- a/a.txt\n+++ b/a.txt\n@@ -1,0 +1,0 @@\n-one\n+ONE\n").encode())
    add("t-count-negative", ("--- a/a.txt\n+++ b/a.txt\n@@ --1 +1 @@\n-one\n+ONE\n").encode())
    add("t-count-huge-num", ("--- a/a.txt\n+++ b/a.txt\n@@ -" + "9" * 400 + " +1 @@\n-one\n+ONE\n").encode())
    add("t-count-no-comma", ("--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n").encode())
    add("t-count-trailing-text", ("--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@ function foo() {\n-one\n+ONE\n").encode())

    # @@ headers with garbage
    for hdr in ("@@", "@@@@", "@@ -a +b @@", "@@ 1 2 @@", "@@ -1,2 +3 @@", "@@ -1 +1", "@@ -1 +1 @@@"):
        add("t-hdr-" + hdr.replace(" ", "_"), f"--- a/a.txt\n+++ b/a.txt\n{hdr}\n-one\n+ONE\n".encode())

    # V4A without End Patch, SEARCH/REPLACE without markers
    add("t-v4a-no-end-2", ("*** Begin Patch\n*** Update File: a.txt\n@@\n-one\n+ONE\n").encode())
    add("t-v4a-end-inside", ("*** Begin Patch\n*** Update File: a.txt\n@@\n-one\n+ONE\n*** End Patch\n*** Add File: x.txt\n+y\n").encode())
    add("t-v4a-truncated-mid", ("*** Begin Patch\n*** Add File: x.txt\n+").encode())
    add("t-sr-no-markers", ("a.txt\none\ntwo\n").encode())
    add("t-sr-half", ("a.txt\n=======\n>>>>>>> REPLACE\n").encode())
    add("t-sr-end-only", ("a.txt\n>>>>>>> REPLACE\n").encode())

    # JSON truncated / wrong types
    add("t-json-trunc-1", b'[{"command":"create",')
    add("t-json-trunc-2", b'[{"command":"create","path":"n.txt","file_text":"hi"}]extra')
    add("t-json-trunc-3", b'[{"command":"insert","path":"a.txt","insert_line":')
    add("t-json-wrong-1", b'[{"command":"create","path":"n.txt","file_text":5}]')
    add("t-json-wrong-2", b'[{"command":"str_replace","path":"a.txt","old_str":5}]')
    add("t-json-wrong-3", b'[{"command":"insert","path":"a.txt","insert_line":null,"new_str":"x"}]')
    add("t-json-wrong-4", b'[{"command":5,"path":"a.txt"}]')
    add("t-json-wrong-5", b'[1,2,3]')
    add("t-json-wrong-6", b'["a"]')
    add("t-json-wrong-7", b'[true]')
    add("t-json-huge-int", b'[{"command":"insert","path":"a.txt","insert_line":1e999,"new_str":"x"}]')
    add("t-json-huge-int-2", b'[{"command":"insert","path":"a.txt","insert_line":99999999999999999999,"new_str":"x"}]')
    add("t-json-nan", b'[{"command":"insert","path":"a.txt","insert_line":NaN,"new_str":"x"}]')

    # the same path twice, create then delete
    add("t-twice-create", b'[{"command":"create","path":"n.txt","file_text":"a"},{"command":"create","path":"n.txt","file_text":"b"}]')
    add("t-twice-delete", b'[{"command":"create","path":"n.txt","file_text":"a"},{"command":"create","path":"n.txt","file_text":"b"},{"command":"str_replace","path":"n.txt","old_str":"a","new_str":"c"}]')
    add("t-create-delete-json", b'[{"command":"create","path":"n.txt","file_text":"a"},{"command":"str_replace","path":"n.txt","old_str":"a","new_str":""}]')
    add("t-create-delete-v4a", b"*** Begin Patch\n*** Add File: n.txt\n+a\n*** Delete File: n.txt\n*** End Patch\n")
    add("t-create-move-away", b"*** Begin Patch\n*** Add File: n.txt\n+a\n*** Update File: n.txt\n*** Move to: m.txt\n*** End Patch\n")
    add("t-move-back", b"*** Begin Patch\n*** Update File: b.txt\n*** Move to: m.txt\n*** End Patch\n*** Begin Patch\n*** Update File: m.txt\n*** Move to: b.txt\n*** End Patch\n")

    # paths the tree already uses for something that is not a file
    dtree = {"a.txt": "one\ntwo\nthree\n", "dir/c.txt": "alpha\n"}
    add("t-dir-create-u", b"--- /dev/null\n+++ b/dir\n@@ -0,0 +1 @@\n+x\n", files=dtree)
    add("t-dir-create-j", b'[{"command":"create","path":"dir","file_text":"x"}]', files=dtree)
    add("t-dir-create-v", b"*** Begin Patch\n*** Add File: dir\n+x\n*** End Patch\n", files=dtree)
    add("t-dir-create-sr", b"dir\n<<<<<<< SEARCH\n=======\n+x\n>>>>>>> REPLACE\n", files=dtree)
    add("t-dir-delete-u", b"--- a/dir\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n", files=dtree)
    add("t-dir-delete-v", b"*** Begin Patch\n*** Delete File: dir\n*** End Patch\n", files=dtree)
    add("t-dir-move-onto-u", b"--- a/a.txt\n+++ b/dir\n@@ -1 +1 @@\n-one\n+ONE\n", files=dtree)
    add("t-under-file-create-u", b"--- /dev/null\n+++ b/a.txt/b\n@@ -0,0 +1 @@\n+x\n", files=dtree)
    add("t-under-file-create-j", b'[{"command":"create","path":"a.txt/b","file_text":"x"}]', files=dtree)
    add("t-under-file-create-v", b"*** Begin Patch\n*** Add File: a.txt/b\n+x\n*** End Patch\n", files=dtree)
    add("t-under-file-move-v", b"*** Begin Patch\n*** Update File: a.txt\n*** Move to: a.txt/b\n*** End Patch\n", files=dtree)

    # command line edges
    for argv in (["--"], ["--", "-"], ["-"], ["--check", "--check"], ["--strict", "--strict"],
                 ["--format=json", "--format=json"], ["--format=nope"], ["--format="], ["--format"],
                 ["-x"], ["--check", "-x"], ["--", "--check"], ["a.patch", "b.patch"], ["-", "-"]):
        add("t-argv-" + "_".join(a.replace("-", "d") for a in argv), b"", argv)
    add("t-argv-missing-file", b"", ["nope.patch"])
    add("t-argv-dir-patch", b"", ["dir"])
    add("t-argv-file-and-dash", b"--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n", ["-", "a.patch"], {"a.patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n", **BASE_FILES})
    add("t-argv-patch-file", b"", ["p.patch"], {"p.patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n", **BASE_FILES})
    add("t-argv-patch-file-dashdash", b"", ["--", "p.patch"], {"p.patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+ONE\n", **BASE_FILES})

    return out


# --------------------------------------------------------------------------- mutation fuzz

MUT_CHARS = list("+-@*<>=\\\"'{}[],: \t\r\n0123456789abcXYZ_/.") + ["é", "日", "\x00", "\x01", "\ufffd"]


def mutate(rng: random.Random, text: str) -> str:
    lines = text.split("\n")
    for _ in range(rng.randint(1, 4)):
        op = rng.randint(0, 9)
        if op == 0 and lines:
            del lines[rng.randrange(len(lines))]
        elif op == 1 and lines:
            i = rng.randrange(len(lines))
            lines.insert(i, lines[i])
        elif op == 2 and lines:
            i, j = rng.randrange(len(lines)), rng.randrange(len(lines))
            lines[i], lines[j] = lines[j], lines[i]
        elif op == 3:
            lines.insert(rng.randrange(len(lines) + 1), "".join(rng.choice(MUT_CHARS) for _ in range(rng.randint(0, 30))))
        elif op == 4 and lines:
            i = rng.randrange(len(lines))
            s = list(lines[i])
            for _ in range(rng.randint(1, 5)):
                if s:
                    s[rng.randrange(len(s))] = rng.choice(MUT_CHARS)
            lines[i] = "".join(s)
        elif op == 5:
            lines = lines[: rng.randrange(len(lines) + 1)]
        elif op == 6 and lines:
            i = rng.randrange(len(lines))
            lines[i] = lines[i][: rng.randrange(len(lines[i]) + 1)]
        elif op == 7:
            lines.insert(rng.randrange(len(lines) + 1), rng.choice(["@@", "--- ", "+++ ", "diff --git ", "*** Begin Patch", "*** End Patch", "<<<<<<< SEARCH", "=======", ">>>>>>> REPLACE"]))
        elif op == 8 and lines:
            i = rng.randrange(len(lines))
            lines[i] = lines[i] + " " * rng.randint(1, 4)
        else:
            lines.append("")
    return "\n".join(lines)


def gen_mutations(seed=1234):
    rng = random.Random(seed)
    pool = [(t, n) for n, t, _f, valid in UNIFIED if valid]
    pool += [(t, n) for n, t, valid in SEARCH_REPLACE if valid]
    pool += [(t, n) for n, t, valid in V4A if valid]
    pool += [(t, n) for n, t, valid in JSON_CASES if valid]
    out = []
    for i in range(1200):
        text, name = pool[rng.randrange(len(pool))]
        m = mutate(rng, text)
        argv = rng.choice([[], ["--check"], ["--strict"], ["--format=unified"], ["--format=v4a"],
                           ["--format=json"], ["--format=search-replace"], ["--format=auto"]])
        out.append(C(f"m-{i:04d}-{name}", "mutation", m, argv, BASE_FILES))
    return out


# --------------------------------------------------------------------------- random structured fuzz

PATHS = ["a.txt", "b.txt", "dir/c.txt", "new.txt", "x/y/z.txt", "n.txt", "dup.txt",
         "../evil", "/abs", "a//b", "a/./b", "", "héllo.txt", "a\x01b", "sp ace.txt",
         "d/" + "n" * 120 + ".txt", "d/" * 20 + "f.txt", "a\tb", "a\\b", "..", "."]

TOKENS = ["one", "two", "three", "alpha", "beta", "gamma", "same", "x", "", " ", "  padded  ",
          "\t", "日本語", "a+b", "-dash", "@@", "***", "=======", "\\ No newline at end of file"]


def gen_random(seed=99):
    rng = random.Random(seed)
    out = []
    for i in range(1400):
        kind = rng.choice(["unified", "search-replace", "v4a", "json"])
        p = rng.choice(PATHS)
        body = [rng.choice(TOKENS) for _ in range(rng.randint(0, 8))]
        if kind == "unified":
            hdr = rng.choice([f"@@ -{rng.randint(0,5)},{rng.randint(0,5)} +{rng.randint(0,5)},{rng.randint(0,5)} @@",
                              "@@", "@@ -1 +1 @@", "@@ garbage"])
            lines = []
            if rng.random() < 0.7:
                lines += ["diff --git a/" + p + " b/" + p] if rng.random() < 0.4 else ["--- a/" + p, "+++ b/" + p]
            lines.append(hdr)
            for t in body:
                lines.append(rng.choice([" ", "-", "+", "", "\\"]) + t)
            text = "\n".join(lines) + "\n"
        elif kind == "search-replace":
            text = f"{p}\n<<<<<<< SEARCH\n" + "\n".join(body) + "\n=======\n" + "\n".join(body[::-1]) + "\n>>>>>>> REPLACE\n"
            if rng.random() < 0.3:
                text = text.replace("=======", "", 1)
        elif kind == "v4a":
            head = rng.choice(["*** Add File: ", "*** Delete File: ", "*** Update File: "])
            lines = ["*** Begin Patch", head + p]
            if head.startswith("*** Update") and rng.random() < 0.4:
                lines.append("*** Move to: " + rng.choice(PATHS))
            if rng.random() < 0.5:
                lines.append("@@ " + rng.choice(["", "anchor", "one"]))
            for t in body:
                lines.append(rng.choice([" ", "-", "+", ""]) + t)
            if rng.random() < 0.8:
                lines.append("*** End Patch")
            text = "\n".join(lines) + "\n"
        else:
            edits = []
            for _ in range(rng.randint(0, 4)):
                cmd = rng.choice(["create", "str_replace", "insert", "bogus"])
                q = rng.choice(PATHS)
                if cmd == "create":
                    edits.append({"command": "create", "path": q, "file_text": rng.choice(TOKENS)})
                elif cmd == "str_replace":
                    edits.append({"command": "str_replace", "path": q, "old_str": rng.choice(TOKENS), "new_str": rng.choice(TOKENS)})
                elif cmd == "insert":
                    edits.append({"command": "insert", "path": q, "insert_line": rng.randint(-2, 6), "new_str": rng.choice(TOKENS)})
                else:
                    edits.append({"command": "bogus", "path": q})
            if rng.random() < 0.75:
                text = json.dumps(edits)
            else:
                text = "\n".join(json.dumps(e) for e in edits) + "\n"
        argv = rng.choice([[], ["--check"], ["--strict"], ["--format=" + kind]])
        out.append(C(f"r-{i:04d}", "random", text, argv, BASE_FILES))
    return out


# --------------------------------------------------------------------------- running

def run_case(case: Case) -> Outcome:
    d = tempfile.mkdtemp(prefix="rb-")
    try:
        try:
            for p, b in case.files.items():
                fp = os.path.join(d, *p.split("/"))
                parent = os.path.dirname(fp)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(fp, "wb") as fh:
                    fh.write(b)
        except OSError as exc:
            return Outcome(case, "harness", [f"harness could not build the tree: {exc}"], None, 0.0, b"", b"")
        env = dict(os.environ)
        env.pop("MAKEFUN_ENV_URL", None)
        stdin = case.stdin if case.stdin is not None else b""
        t0 = time.perf_counter()
        try:
            r = subprocess.run([NODE, MAIN, *case.argv], cwd=d, input=stdin,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=TIMEOUT, env=env)
        except subprocess.TimeoutExpired:
            return Outcome(case, "hang", ["no exit within %.0fs" % TIMEOUT], None,
                           time.perf_counter() - t0, b"", b"")
        dt = time.perf_counter() - t0
        problems: list[str] = []
        if r.returncode not in (0, 1, 2):
            problems.append(f"exit {r.returncode}")
        else:
            problems.extend(check_shape(r.returncode, r.stdout, case.allow_multiline))
        if b"    at " in r.stdout or b"Error:" in r.stdout:
            problems.append("stack trace in stdout")
        if b"mend: internal" in r.stderr:
            # The last-resort guard answered in shape, but it should never have been needed.
            problems.append("uncaught throw (the internal guard caught it)")
        return Outcome(case, "ok" if not problems else "crash", problems, r.returncode, dt,
                       r.stdout, r.stderr)
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--replay", default="")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.replay:
        with open(args.replay) as fh:
            saved = json.load(fh)
        cases = [Case(name=c["name"], tag=c.get("tag", "replay"), argv=c["argv"],
                      stdin=None if c["stdin"] is None else c["stdin"].encode("utf-8", "surrogateescape"),
                      files={p: b.encode("utf-8", "surrogateescape") for p, b in c["files"].items()},
                      allow_multiline=c.get("allow_multiline", False))
                 for c in saved]
    else:
        cases = []
        cases += gen_unified()
        cases += gen_search_replace()
        cases += gen_v4a()
        cases += gen_json()
        cases += gen_targeted()
        cases += gen_mutations(args.seed or 1234)
        cases += gen_random(args.seed or 99)
    if args.tag:
        cases = [c for c in cases if c.tag == args.tag]
    if args.limit:
        cases = cases[: args.limit]

    total = len(cases)
    print(f"generated {total} cases", flush=True)
    if total < 3000 and not args.replay:
        print(f"WARNING: fewer than 3000 cases generated ({total})", flush=True)

    t0 = time.perf_counter()
    results: list[Outcome] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for i, out in enumerate(ex.map(run_case, cases), 1):
            results.append(out)
            if not args.quiet and i % 500 == 0:
                bad = sum(1 for r in results if r.status != "ok")
                print(f"  {i}/{total} run, {bad} bad", flush=True)
    elapsed = time.perf_counter() - t0

    ok = [r for r in results if r.status == "ok"]
    crashes = [r for r in results if r.status == "crash"]
    hangs = [r for r in results if r.status == "hang"]
    broken = [r for r in results if r.status == "harness"]

    print()
    print(f"cases            {total}")
    print(f"spec-shaped      {len(ok)}")
    print(f"crashes          {len(crashes)}")
    print(f"hangs            {len(hangs)}")
    if broken:
        print(f"harness errors   {len(broken)}")
    print(f"wall time        {elapsed:.1f}s")
    by_tag: dict[str, int] = {}
    for r in results:
        by_tag.setdefault(r.case.tag, 0)
        if r.status != "ok":
            by_tag[r.case.tag] += 1
    print("bad by family    " + ", ".join(f"{k}={v}" for k, v in sorted(by_tag.items()) if v) if any(by_tag.values()) else "bad by family    none")

    def brief(s: object, n: int = 120) -> str:
        t = str(s)
        return t if len(t) <= n else t[:n] + f"...(+{len(t)-n})"

    slow = sorted(results, key=lambda r: -r.seconds)[:5]
    print()
    print("slowest cases:")
    for r in slow:
        print(f"  {r.seconds:6.2f}s  {r.case.tag:15s} {brief(r.case.name, 60)}  argv={brief(r.case.argv)}")

    if crashes or hangs:
        print()
        print("crash signatures:")
        sigs: dict[str, list[Outcome]] = {}
        for r in crashes + hangs:
            sigs.setdefault(r.signature, []).append(r)
        for sig, group in sorted(sigs.items(), key=lambda kv: -len(kv[1])):
            print(f"  x{len(group):<4d} {brief(sig, 200)}")
            for r in group[:3]:
                print(f"          e.g. {r.case.tag}/{brief(r.case.name, 60)} argv={brief(r.case.argv)} exit={r.code}")
        fails_path = os.path.join(HERE, "robust-failures.json")
        with open(fails_path, "w") as fh:
            json.dump([r.case.as_json() for r in crashes + hangs], fh, indent=1)
        print(f"\nfailing cases saved to {fails_path}")
        return 1
    print("\nno crashes, no hangs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
