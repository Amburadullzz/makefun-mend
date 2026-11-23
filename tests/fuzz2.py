"""tests/fuzz2.py — structural fuzz: random token soup patches, to compare parse errors and precedence.

usage: python3 tests/fuzz2.py [n] [seed]
"""
import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import ref  # noqa: E402

TOKENS = [
    '', '', '', ' ', '\t', 'prose', '```', '```diff', '=======', '<<<<<<< SEARCH', '>>>>>>> REPLACE',
    '*** Begin Patch', '*** End Patch', '*** Add File: a.txt', '*** Add File: ../x', '*** Delete File: a.txt',
    '*** Update File: a.txt', '*** Update File: nope.txt', '*** Move to: b.txt', '*** End of File',
    '@@', '@@ alpha', '@@ bogus', '@@ -1 +1 @@', '@@ -1,2 +1,2 @@', '@@ -0,0 +1,2 @@', '@@ -9,3 +9,3 @@',
    'diff --git a/a.txt b/a.txt', 'diff --git a/a.txt b/b.txt', 'diff --git bad', '--- a/a.txt', '+++ b/a.txt',
    '--- /dev/null', '+++ /dev/null', '--- a/../x', 'rename from a.txt', 'rename to b.txt', 'new file mode 100644',
    'deleted file mode 100644', 'index 111..222 100644', 'similarity index 90%', 'Index: a.txt',
    ' alpha', ' beta', '-alpha', '+beta', '\\ No newline at end of file', '+', '-', ' alpha  ', '\talpha',
    '{"command":"create","path":"a.txt","file_text":"x"}',
    '{"command":"create","path":"n.txt","file_text":"x"}',
    '{"command":"str_replace","path":"a.txt","old_str":"alpha","new_str":"Z"}',
    '{"command":"insert","path":"a.txt","insert_line":0,"new_str":"z"}',
    '{"command":"insert","path":"a.txt","insert_line":9,"new_str":"z"}',
    '{"command":"nope","path":"a.txt"}', '[]', '[', ']', '{}', 'nope',
]


def gen_case(rng, i):
    files = {}
    for name in ['a.txt', 'b.txt']:
        if rng.random() < 0.8:
            n = rng.randrange(0, 4)
            lines = [rng.choice(['alpha', 'beta', ' gamma', '\talpha', '']) for _ in range(n)]
            br = '\r\n' if rng.random() < 0.15 else '\n'
            files[name] = br.join(lines) + (br if lines and rng.random() < 0.7 else '')
    n = rng.randrange(1, 9)
    patch = '\n'.join(rng.choice(TOKENS) for _ in range(n)) + '\n'
    argv = []
    r = rng.random()
    if r < 0.55:
        argv.append('--format=' + rng.choice(['unified', 'search-replace', 'v4a', 'json']))
    if rng.random() < 0.1:
        argv.append('--strict')
    return {'key': 'w%d' % i, 'argv': argv, 'stdin': patch, 'files': files}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    rng = random.Random(seed)
    cases = [gen_case(rng, i) for i in range(n)]
    tmp = tempfile.mkdtemp()
    cf = os.path.join(tmp, 'cases.json')
    rf = os.path.join(tmp, 'res.json')
    json.dump(cases, open(cf, 'w'))
    subprocess.run(['node', os.path.join(HERE, 'diffrun.ts'), cf, rf], check=True, cwd=ROOT)
    got = json.load(open(rf))
    bad = 0
    for c, g in zip(cases, got):
        code, out, after = ref.run(c['argv'], c['stdin'], c['files'])
        why = []
        if g['exit'] != code:
            why.append('exit %r vs %r' % (g['exit'], code))
        if g['stdout'] != out:
            why.append('stdout %r vs %r' % (g['stdout'], out))
        for p in sorted(set(after) | set(g['files'])):
            if after.get(p) != g['files'].get(p):
                why.append('%s %r vs %r' % (p, g['files'].get(p), after.get(p)))
        if why:
            bad += 1
            if bad <= 10:
                print('DIFF', c['key'], c['argv'])
                print('  stdin', json.dumps(c['stdin']))
                print('  files', json.dumps(c['files']))
                for w in why:
                    print('   ', w[:400])
    print('%d of %d agree (%d differ)' % (n - bad, n, bad))


if __name__ == '__main__':
    main()
