"""tests/build_audit.py — writes tests/audit.json: hand-built checks, one per SPEC.md sentence that
practice.json cannot show. Each check names the sentence it tests in `why`.

Run them with:  node check.ts tests/audit.json
"""
import json
import os

C = []


def chk(family, name, why, argv, stdin, files, exit, out, changes=None):
    C.append({
        'key': '%s/%s' % (family, name),
        'group': family.split('-')[0],
        'family': family,
        'why': why,
        'argv': argv,
        'stdin': stdin,
        'files': files,
        'expect': {'exit': exit, 'stdout': out, 'changes': changes or {}},
    })


# ---------------------------------------------------------------- section 2: the order of the errors
chk('a-order', 'usage-beats-no-patch', '2: a usage error wins over everything', ['--bogus', 'nope.patch'],
    None, {}, 2, 'error: usage\n')
chk('a-order', 'no-patch-beats-format', '2: no-patch is tried before format', ['nope.patch'],
    None, {}, 2, 'error: no-patch\n')
chk('a-order', 'parse-before-any-file', '2: a parse error is reported before any file is looked at',
    ['--format=unified'], '--- a/nofile\n+++ b/nofile\n@@ -1,1 +1,1 @@\n-x\n+y\n@@ bogus\n', {}, 2,
    'error: parse 6\n')
chk('a-order', 'usage-beats-parse', '2: a usage error wins over a parse error', ['--format=bogus', 'p.patch'],
    None, {'p.patch': '@@ bogus\n'}, 2, 'error: usage\n')
chk('a-order', 'two-positionals', '1: more than one positional argument is a usage error', ['a.patch', 'b.patch'],
    None, {'a.patch': 'x'}, 2, 'error: usage\n')
chk('a-order', 'format-twice', '1: an option given twice is a usage error',
    ['--format=json', '--format=unified'], '', {}, 2, 'error: usage\n')
chk('a-order', 'dashdash', '1: -- ends the options, so - is the PATCH and reads stdin',
    ['--format=json', '--', '-'], '{"command":"create","path":"a","file_text":"x"}\n', {}, 0,
    'A a\nok: 1 file\n', {'a': 'x'})

# ---------------------------------------------------------------- section 2: a patch with no edit at all
chk('a-noedit', 'unified', '2: a patch in which the reader finds no edit at all is error: parse 1',
    ['--format=unified'], 'hello\nworld\n', {}, 2, 'error: parse 1\n')
chk('a-noedit', 'search-replace', '2: no edit at all, SEARCH/REPLACE', ['--format=search-replace'],
    'hello\nworld\n', {}, 2, 'error: parse 1\n')
chk('a-noedit', 'v4a', '2: no edit at all, V4A', ['--format=v4a'], '*** Begin Patch\n*** End Patch\n', {}, 2,
    'error: parse 1\n')
chk('a-noedit', 'v4a-blank', '8.1: error: parse 1 when there is no first non-blank line', ['--format=v4a'],
    '\n\n   \n', {}, 2, 'error: parse 1\n')
chk('a-noedit', 'json-array', '2: no edit at all, an empty JSON array', ['--format=json'], '[]\n', {}, 2,
    'error: parse 1\n')
chk('a-noedit', 'json-blank', '2: no edit at all, JSON Lines with only blank lines', ['--format=json'],
    '\n\n   \n', {}, 2, 'error: parse 1\n')
chk('a-noedit', 'auto-no-format', '4: otherwise it is error: format', [], 'hello\nworld\n', {}, 2,
    'error: format\n')

# ---------------------------------------------------------------- section 2: unsafe paths
chk('a-unsafe', 'unified-prefix', '2: the line names the path as written, after the a/ prefix is removed',
    ['--format=unified'], '--- a/../x\n+++ b/../x\n@@ -1 +1 @@\n-a\n+b\n', {}, 1,
    'error: unsafe ../x\nfailed: 1 of 1 edits\n')
chk('a-unsafe', 'v4a-absolute', '3.2: a path that starts with / is not safe', ['--format=v4a'],
    '*** Begin Patch\n*** Add File: /tmp/zz\n+x\n*** End Patch\n', {}, 1,
    'error: unsafe /tmp/zz\nfailed: 1 of 1 edits\n')
chk('a-unsafe', 'json-backslash', '3.2: a path that holds a backslash is not safe', ['--format=json'],
    '{"command":"create","path":"a\\\\b","file_text":"x"}\n', {}, 1,
    'error: unsafe a\\b\nfailed: 1 of 1 edits\n')
chk('a-unsafe', 'json-empty-segment', '3.2: an empty path segment is not safe', ['--format=json'],
    '{"command":"create","path":"a//b","file_text":"x"}\n', {}, 1,
    'error: unsafe a//b\nfailed: 1 of 1 edits\n')
chk('a-unsafe', 'create-checks-the-old-path', '10.2: every path an edit names is checked for safety first',
    ['--format=unified'],
    'diff --git a/ok.txt b/ok.txt\nnew file mode 100644\n--- a/../x\n+++ b/ok.txt\n@@ -0,0 +1 @@\n+hi\n',
    {}, 1, 'error: unsafe ../x\nfailed: 1 of 1 edits\n')

# ---------------------------------------------------------------- section 2: the R line and its sort key
chk('a-r', 'sort-key', '2: R replaces the pair\'s D and A lines and sorts by <new>', ['--format=v4a'],
    '*** Begin Patch\n*** Update File: m.txt\n*** Move to: b.txt\n*** Add File: c.txt\n+z\n'
    '*** Delete File: d.txt\n*** End Patch\n', {'m.txt': 'hello\n', 'd.txt': 'gone\n'}, 0,
    'R m.txt -> b.txt\nA c.txt\nD d.txt\nok: 3 files\n',
    {'m.txt': None, 'b.txt': 'hello\n', 'c.txt': 'z\n', 'd.txt': None})
chk('a-r', 'unchanged-bytes', '2.13: R even when the moved file\'s bytes are unchanged', ['--format=unified'],
    'diff --git a/a.txt b/b.txt\nsimilarity index 100%\nrename from a.txt\nrename to b.txt\n',
    {'a.txt': 'x\n'}, 0, 'R a.txt -> b.txt\nok: 1 file\n', {'a.txt': None, 'b.txt': 'x\n'})
chk('a-r', 'created-then-moved', '2.13: no R when <old> did not exist before the patch', ['--format=v4a'],
    '*** Begin Patch\n*** Add File: t\n+z\n*** Update File: t\n*** Move to: u\n*** End Patch\n', {}, 0,
    'A u\nok: 1 file\n', {'u': 'z\n'})

# ---------------------------------------------------------------- section 2: unchanged bytes, byte order
chk('a-unchanged', 'hunk-noop', '2: a file whose bytes end up unchanged is not listed', ['--format=unified'],
    '--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n-b\n+b\n', {'f': 'a\nb\n'}, 0, 'ok: 0 files\n')
chk('a-unchanged', 'str-replace-noop', '2: a str_replace that changes nothing lists nothing', ['--format=json'],
    '{"command":"str_replace","path":"a","old_str":"x","new_str":"x"}\n', {'a': 'x\n'}, 0, 'ok: 0 files\n')
chk('a-sort', 'utf8-byte-order', '2: the change lines are in byte order of the paths (UTF-8)',
    ['--format=json'],
    '[{"command":"create","path":"\ufffd","file_text":"a"},'
    '{"command":"create","path":"\ud800\udc00","file_text":"b"},'
    '{"command":"create","path":"z","file_text":"c"}]\n', {}, 0,
    'A z\nA \ufffd\nA \ud800\udc00\nok: 3 files\n',
    {'z': 'c', '\ufffd': 'a', '\ud800\udc00': 'b'})

# ---------------------------------------------------------------- section 1: --check writes nothing
chk('a-check', 'creates-nothing', '1: --check does everything but write: the same output, no file',
    ['--check', '--format=v4a'], '*** Begin Patch\n*** Add File: deep/a.txt\n+x\n*** End Patch\n', {}, 0,
    'A deep/a.txt\nok: 1 file\n')
chk('a-check', 'failure-writes-nothing', '1: --check with a failing edit writes nothing', ['--check', '--format=json'],
    '{"command":"create","path":"n","file_text":"q"}\n'
    '{"command":"str_replace","path":"a","old_str":"zz"}\n', {'a': 'x\n'}, 1,
    'error: no-match a\nfailed: 1 of 2 edits\n')

# ---------------------------------------------------------------- section 3: line breaks and final breaks
chk('a-crlf', 'patch-lines', '3.9: a patch line has one trailing \\r removed', ['--format=unified'],
    '--- a/f\r\n+++ b/f\r\n@@ -1 +1 @@\r\n-a\r\n+b\r\n', {'f': 'a\n'}, 0, 'M f\nok: 1 file\n',
    {'f': 'b\n'})
chk('a-crlf', 'file-keeps-its-break', '3.7: a changed file is written with its own line break', ['--format=unified'],
    '--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n', {'f': 'a\r\nc\r\n'}, 0, 'M f\nok: 1 file\n',
    {'f': 'b\r\nc\r\n'})
chk('a-crlf', 'created-file-uses-lf', '3.8: a file an edit creates is written with LF breaks',
    ['--format=v4a'], '*** Begin Patch\n*** Add File: n\n+x\n+y\n*** End Patch\n', {}, 0,
    'A n\nok: 1 file\n', {'n': 'x\ny\n'})
chk('a-crlf', 'end-of-file-gains-a-break', '6.22: matched lines to the end -> the result ends with a break',
    ['--format=unified'], '--- a/f\n+++ b/f\n@@ -2 +2 @@\n-b\n+c\n', {'f': 'a\r\nb'}, 0,
    'M f\nok: 1 file\n', {'f': 'a\r\nc\r\n'})
chk('a-crlf', 'no-lines-is-empty', '3.6: a file of no lines is written empty', ['--format=unified'],
    '--- a/f\n+++ b/f\n@@ -1,2 +0,0 @@\n-a\n-b\n', {'f': 'a\nb'}, 0, 'M f\nok: 1 file\n', {'f': ''})

# ---------------------------------------------------------------- section 5: tier 2 re-indentation
chk('a-tier2', 'p-swapped-for-f', '5.8: a line of N that starts with P has that P replaced by F',
    ['--format=unified'],
    '--- a/f\n+++ b/f\n@@ -1,2 +1,3 @@\n     a\n-    b\n+    c\n+\tz\n', {'f': '  a\n  b\n'}, 0,
    'M f\nok: 1 file\n', {'f': '  a\n  c\n\tz\n'})
chk('a-tier2', 'empty-p-prepends-f', '5.8: with P empty every non-whitespace line of N gets F prepended',
    ['--format=unified'], '--- a/f\n+++ b/f\n@@ -1,2 +1,3 @@\n a\n-b\n+c\n+d\n', {'f': '\ta\nb\n'}, 0,
    'M f\nok: 1 file\n', {'f': '\ta\n\tc\n\td\n'})
chk('a-tier2', 'context-keeps-the-file-line', '5.7: a context line is written as the file\'s own matched line',
    ['--format=search-replace'], 'f\n<<<<<<< SEARCH\n a\n b\n=======\n a\n c\n>>>>>>> REPLACE\n',
    {'f': '\ta\n\tb\n'}, 0, 'M f\nok: 1 file\n', {'f': '\ta\n\tc\n'})

# ---------------------------------------------------------------- section 6: nearest occurrence
chk('a-nearest', 'tie-takes-the-earlier', '6.20: of two equally near, the earlier', ['--format=unified'],
    '--- a/f\n+++ b/f\n@@ -2 +2 @@\n-x\n+X\n', {'f': 'x\nq\nx\n'}, 0, 'M f\nok: 1 file\n',
    {'f': 'X\nq\nx\n'})
chk('a-nearest', 'd-shifts-the-target', '6.20: nearest to A - 1 + d, d summed over earlier hunks',
    ['--format=unified'], '--- a/f\n+++ b/f\n@@ -1 +1 @@\n+q\n@@ -3 +3 @@\n-x\n+X\n',
    {'f': 'x\nq\nx\n'}, 0, 'M f\nok: 1 file\n', {'f': 'x\nq\nq\nX\n'})

# ---------------------------------------------------------------- section 6: empty O
chk('a-empty-o', 'mid-file', '6.21: N is inserted after the first A + d lines', ['--format=unified'],
    '--- a/f\n+++ b/f\n@@ -2,0 +3,1 @@\n+mid\n', {'f': 'a\nb\nc\n'}, 0, 'M f\nok: 1 file\n',
    {'f': 'a\nb\nmid\nc\n'})
chk('a-empty-o', 'past-the-end', '6.21: all of them when the file has fewer', ['--format=unified'],
    '--- a/f\n+++ b/f\n@@ -99,0 +100,1 @@\n+tail\n', {'f': 'a\nb\n'}, 0, 'M f\nok: 1 file\n',
    {'f': 'a\nb\ntail\n'})
chk('a-empty-o', 'at-the-top', '6.21: A + d is 0 inserts at the top', ['--format=unified'],
    '--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+top\n', {'f': 'a\nb\n'}, 0, 'M f\nok: 1 file\n',
    {'f': 'top\na\nb\n'})
chk('a-empty-o', 'at-the-end-of-an-empty-file', '6.21/6.22: insertion at the end of an empty file',
    ['--format=unified'], '--- a/f\n+++ b/f\n@@ -0,0 +1 @@\n+x\n', {'f': ''}, 0, 'M f\nok: 1 file\n',
    {'f': 'x\n'})

# ---------------------------------------------------------------- section 6: the backslash line
chk('a-backslash', 'no-final-break', '6.22: a \\ line after the last + line keeps the file without a break',
    ['--format=unified'], '--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n\\ No newline at end of file\n',
    {'f': 'a\nb'}, 0, 'M f\nok: 1 file\n', {'f': 'a\nc'})
chk('a-backslash', 'break-added-without-it', '6.22: matched lines to the end -> the result ends with a break',
    ['--format=unified'], '--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n-b\n+c\n', {'f': 'a\nb'}, 0,
    'M f\nok: 1 file\n', {'f': 'a\nc\n'})
chk('a-backslash', 'after-an-old-line-only', '6.22: a \\ after a - line does not cover the last context line',
    ['--format=unified'], '--- a/f\n+++ b/f\n@@ -1,2 +1,1 @@\n a\n-b\n\\ No newline at end of file\n',
    {'f': 'a\nb'}, 0, 'M f\nok: 1 file\n', {'f': 'a\n'})
chk('a-backslash', 'created-file', '6.22: a created file ends with a break unless a \\ follows its last + line',
    ['--format=unified'], '--- /dev/null\n+++ b/n\n@@ -0,0 +1,2 @@\n+x\n+y\n\\ No newline at end of file\n',
    {}, 0, 'A n\nok: 1 file\n', {'n': 'x\ny'})
chk('a-backslash', 'not-at-the-end', '6.22: when the matched lines do not reach the end the final break stays',
    ['--format=unified'], '--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+A\n\\ No newline at end of file\n',
    {'f': 'a\nb'}, 0, 'M f\nok: 1 file\n', {'f': 'A\nb'})

# ---------------------------------------------------------------- section 6: parse error line numbers
chk('a-parse-lines', 'dashdash-without-plus', '6.7: parse <the line after it>', ['--format=unified'],
    'diff --git a/x b/x\n--- a/x\nindex 123\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n', {'x': 'a\n'}, 2,
    'error: parse 3\n')
chk('a-parse-lines', 'dashdash-last-line', '6.7: parse <the line after it> when it is the last line',
    ['--format=unified'], 'diff --git a/x b/x\n--- a/x\n', {'x': 'a\n'}, 2, 'error: parse 3\n')
chk('a-parse-lines', 'bad-diff-git', '6.8: parse <its line>', ['--format=unified'],
    'diff --git x y\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n', {'x': 'a\n'}, 2, 'error: parse 1\n')
chk('a-parse-lines', 'hunk-before-a-section', '6.9: a hunk before any section is parse <its line>',
    ['--format=unified'], '@@ -1 +1 @@\n-a\n+b\n', {'x': 'a\n'}, 2, 'error: parse 1\n')
chk('a-parse-lines', 'bad-hunk-header', '6.9: any other line starting with @@ is parse <its line>',
    ['--format=unified'], '--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n@@ bogus\n', {'x': 'a\n'}, 2,
    'error: parse 6\n')
chk('a-parse-lines', 'hunk-header-without-text', '6.9: a hunk header with no text after it',
    ['--format=unified'], '--- a/f\n+++ b/f\n@@ -1 +1 @@\n-x\n+y\n', {'f': 'x\n'}, 0, 'M f\nok: 1 file\n',
    {'f': 'y\n'})

# ---------------------------------------------------------------- section 8: V4A parse error lines
chk('a-v4a-parse', 'first-not-begin', '8.1: parse <that line>', ['--format=v4a'],
    '\n\nbogus\n*** End Patch\n', {}, 2, 'error: parse 3\n')
chk('a-v4a-parse', 'no-end-patch', '8.2: parse <the number of patch lines plus 1>', ['--format=v4a'],
    '*** Begin Patch\n*** Add File: a\n+x\n', {}, 2, 'error: parse 4\n')
chk('a-v4a-parse', 'update-with-no-chunk', '8.6: parse <its Update File line>', ['--format=v4a'],
    '*** Begin Patch\n*** Update File: a\n*** End Patch\n', {'a': 'x\n'}, 2, 'error: parse 2\n')
chk('a-v4a-parse', 'add-section-bad-line', '8.6: any other line is parse <its line>', ['--format=v4a'],
    '*** Begin Patch\n*** Add File: a\n+x\nbogus\n*** End Patch\n', {}, 2, 'error: parse 4\n')
chk('a-v4a-parse', 'end-of-file-with-no-chunk', '8.6: *** End of File ends a chunk', ['--format=v4a'],
    '*** Begin Patch\n*** Update File: a\n*** End of File\n*** End Patch\n', {'a': 'x\n'}, 2,
    'error: parse 3\n')
chk('a-v4a-parse', 'delete-section-has-no-lines', '8.6: a Delete File section has no lines', ['--format=v4a'],
    '*** Begin Patch\n*** Delete File: d.txt\n+x\n*** End Patch\n', {'d.txt': 'x\n'}, 2, 'error: parse 3\n')

# ---------------------------------------------------------------- section 8: the cursor
chk('a-v4a-cursor', 'second-chunk-starts-at-the-cursor', '8.9: the chunk takes the first occurrence at or after the cursor',
    ['--format=v4a'],
    '*** Begin Patch\n*** Update File: f\n@@ one\n-x\n+X\n@@ two\n-x\n+Y\n*** End Patch\n',
    {'f': 'one\nx\ntwo\nx\n'}, 0, 'M f\nok: 1 file\n', {'f': 'one\nX\ntwo\nY\n'})
chk('a-v4a-cursor', 'anchor-has-its-own-tier', '8.9: the anchor is found at the first tier where one is',
    ['--format=v4a'], '*** Begin Patch\n*** Update File: f\n@@ head\n-A\n+A2\n*** End Patch\n',
    {'f': 'head  \nA\nB\n'}, 0, 'M f\nok: 1 file\n', {'f': 'head  \nA2\nB\n'})
chk('a-v4a-cursor', 'end-of-file-chunk', '8.9: an end-of-file chunk takes only an occurrence ending at the last line',
    ['--format=v4a'], '*** Begin Patch\n*** Update File: f\n@@\n-a\n+A\n*** End of File\n*** End Patch\n',
    {'f': 'a\nb\na\n'}, 0, 'M f\nok: 1 file\n', {'f': 'a\nb\nA\n'})
chk('a-v4a-cursor', 'empty-o-inserts-at-the-cursor', '8.9: when O is empty, N is inserted at the cursor',
    ['--format=v4a'], '*** Begin Patch\n*** Update File: f\n@@\n+x\n*** End Patch\n', {'f': 'a\nb\n'},
    0, 'M f\nok: 1 file\n', {'f': 'x\na\nb\n'})
chk('a-v4a-cursor', 'empty-o-inserts-at-the-end', '8.9: at the end of the file for an end-of-file chunk',
    ['--format=v4a'], '*** Begin Patch\n*** Update File: f\n@@\n+x\n*** End of File\n*** End Patch\n',
    {'f': 'a\nb\n'}, 0, 'M f\nok: 1 file\n', {'f': 'a\nb\nx\n'})
chk('a-v4a-cursor', 'anchor-not-found', '8.9: a chunk with an anchor that is not there is no-match',
    ['--format=v4a'], '*** Begin Patch\n*** Update File: f\n@@ nowhere\n-x\n+X\n*** End Patch\n',
    {'f': 'x\n'}, 1, 'error: no-match f\nfailed: 1 of 1 edits\n')

# ---------------------------------------------------------------- section 9: JSON Lines versus array form
chk('a-json-form', 'jsonl-bad-line', '9.2: JSON Lines: parse <that line>', ['--format=json'],
    '{"command":"create","path":"a","file_text":"x"}\nnope\n', {}, 2, 'error: parse 2\n')
chk('a-json-form', 'array-fault-line', '9.2: array form: parse <the line where the array starts>',
    ['--format=json'],
    '\n[{"command":"create","path":"a","file_text":"x"},{"command":"nope","path":"b"}]\n', {}, 2,
    'error: parse 2\n')
chk('a-json-form', 'array-element-not-an-edit', '9.2: array form, an element that is not an edit object',
    ['--format=json'], '[\n 1\n]\n', {}, 2, 'error: parse 1\n')
chk('a-json-form', 'wrong-command', '9.7: a wrong command is a parse fault', ['--format=json'],
    '{"command":"nope","path":"a"}\n', {'a': 'x'}, 2, 'error: parse 1\n')
chk('a-json-form', 'str-replace-crlf', '9.5: in a CRLF file both strings are folded to LF and back to CRLF',
    ['--format=json'], '{"command":"str_replace","path":"f","old_str":"a\\nb","new_str":"A\\nB"}\n',
    {'f': 'a\r\nb\r\n'}, 0, 'M f\nok: 1 file\n', {'f': 'A\r\nB\r\n'})
chk('a-json-form', 'str-replace-overlapping', '9.5: positions may overlap, so aa in aaa is ambiguous',
    ['--format=json'], '{"command":"str_replace","path":"f","old_str":"aa","new_str":"b"}\n',
    {'f': 'aaa'}, 1, 'error: ambiguous f\nfailed: 1 of 1 edits\n')
chk('a-json-form', 'str-replace-no-new-str', '9.5: new_str absent is empty', ['--format=json'],
    '{"command":"str_replace","path":"a","old_str":"x"}\n', {'a': 'x\ny\n'}, 0, 'M a\nok: 1 file\n',
    {'a': '\ny\n'})

# ---------------------------------------------------------------- section 9: insert_line bounds
chk('a-insert', 'past-the-end', '9.6: insert_line from 0 to the number of lines, else no-match',
    ['--format=json'], '{"command":"insert","path":"f","insert_line":3,"new_str":"x"}\n', {'f': 'a\nb\n'},
    1, 'error: no-match f\nfailed: 1 of 1 edits\n')
chk('a-insert', 'negative', '9.6: insert_line from 0, else no-match', ['--format=json'],
    '{"command":"insert","path":"f","insert_line":-1,"new_str":"x"}\n', {'f': 'a\n'}, 1,
    'error: no-match f\nfailed: 1 of 1 edits\n')
chk('a-insert', 'at-the-end', '9.6: insert_line equal to the number of lines is allowed', ['--format=json'],
    '{"command":"insert","path":"f","insert_line":2,"new_str":"z"}\n', {'f': 'a\nb\n'}, 0,
    'M f\nok: 1 file\n', {'f': 'a\nb\nz\n'})
chk('a-insert', 'final-break-stays', '9.6: the file\'s final break stays as it was', ['--format=json'],
    '{"command":"insert","path":"f","insert_line":1,"new_str":"z"}\n', {'f': 'a\nb'}, 0,
    'M f\nok: 1 file\n', {'f': 'a\nz\nb'})
chk('a-insert', 'crlf-file', '9.6: a CRLF file keeps its break', ['--format=json'],
    '{"command":"insert","path":"f","insert_line":0,"new_str":"x\\r\\ny"}\n', {'f': 'a\r\nb\r\n'}, 0,
    'M f\nok: 1 file\n', {'f': 'x\r\ny\r\na\r\nb\r\n'})
chk('a-insert', 'mistyped', '9.7: a mistyped key is a parse fault', ['--format=json'],
    '{"command":"insert","path":"f","insert_line":"1","new_str":"x"}\n', {'f': 'a\n'}, 2,
    'error: parse 1\n')

# ---------------------------------------------------------------- section 7: the path, the markers
chk('a-sr-path', 'inherits-the-previous-path', '7.4: a block with no path line takes the previous block\'s',
    ['--format=search-replace'],
    'f\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n<<<<<<< SEARCH\nc\n=======\nd\n>>>>>>> REPLACE\n',
    {'f': 'a\nc\n'}, 0, 'M f\nok: 1 file\n', {'f': 'b\nd\n'})
chk('a-sr-path', 'first-block-with-none', '7.4: the first block with none is parse <its SEARCH line>',
    ['--format=search-replace'], '```\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n', {'f': 'a\n'},
    2, 'error: parse 2\n')
chk('a-sr-path', 'skips-a-blank-line', '7.3: the nearest line before SEARCH that is not blank',
    ['--format=search-replace'], 'f\n\n  \n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n',
    {'f': 'a\n'}, 0, 'M f\nok: 1 file\n', {'f': 'b\n'})
chk('a-sr-path', 'no-equals-line', '7.5: a SEARCH line with no ======= is parse <the SEARCH line>',
    ['--format=search-replace'], 'f\n<<<<<<< SEARCH\na\n>>>>>>> REPLACE\n', {'f': 'a\n'}, 2,
    'error: parse 2\n')
chk('a-marker', 'trailing-whitespace', '7.2: the three marker lines are matched with trailing whitespace removed',
    ['--format=search-replace'],
    'f\n<<<<<<< SEARCH   \na\n=======\t\nb\n>>>>>>> REPLACE  \n', {'f': 'a\n'}, 0, 'M f\nok: 1 file\n',
    {'f': 'b\n'})
chk('a-marker', 'trailing-form-feed', '7.2: trailing whitespace, not only spaces and tabs',
    ['--format=search-replace'],
    'f\n<<<<<<< SEARCH\x0c\na\n=======\x0c\nb\n>>>>>>> REPLACE\x0c\n', {'f': 'a\n'}, 0,
    'M f\nok: 1 file\n', {'f': 'b\n'})
chk('a-marker', 'empty-old-lines-create', '7.7: old lines empty creates the file (else exists)',
    ['--format=search-replace'], 'n.txt\n<<<<<<< SEARCH\n=======\n>>>>>>> REPLACE\n', {}, 0,
    'A n.txt\nok: 1 file\n', {'n.txt': ''})
chk('a-marker', 'ambiguous', '7.8: the old lines occur at their tier exactly once, else ambiguous',
    ['--format=search-replace'], 'f\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n', {'f': 'a\na\n'},
    1, 'error: ambiguous f\nfailed: 1 of 1 edits\n')

# ---------------------------------------------------------------- section 10: order and atomicity
chk('a-order-edits', 'later-edit-sees-the-earlier-one', '10.1: a file created earlier is seen by a later edit',
    ['--format=json'], '{"command":"create","path":"t","file_text":"z\\n"}\n', {}, 0, 'A t\nok: 1 file\n',
    {'t': 'z\n'})
chk('a-order-edits', 'failed-edit-changes-nothing', '10.3/10.4: a failure writes nothing at all',
    ['--format=json'], '{"command":"create","path":"n","file_text":"q"}\n'
    '{"command":"str_replace","path":"a","old_str":"zz"}\n', {'a': 'x\n'}, 1,
    'error: no-match a\nfailed: 1 of 2 edits\n')
chk('a-order-edits', 'every-failure-reported', '2: one line per failed edit, in the patch\'s order',
    ['--format=json'],
    '{"command":"str_replace","path":"a","old_str":"zz"}\n'
    '{"command":"str_replace","path":"b","old_str":"zz"}\n'
    '{"command":"create","path":"a","file_text":"q"}\n', {'a': 'x\n', 'b': 'y\n'}, 1,
    'error: no-match a\nerror: no-match b\nerror: exists a\nfailed: 3 of 3 edits\n')

out = {'checks': C}
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, 'audit.json'), 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=1, ensure_ascii=True)
    f.write('\n')
print('wrote %d checks' % len(C))
