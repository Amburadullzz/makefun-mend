"""tests/fuzz.py — differential fuzz: main.ts against tests/ref.py.

usage: python3 tests/fuzz.py [n] [seed]
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

WORDS = ['alpha', 'beta', 'gamma', 'delta', 'keep', 'old', 'new', 'x', 'y', 'z']
INDENTS = ['', '', '  ', '    ', '\t']
NAMES = ['a.txt', 'b.txt', 'c.md', 'dir/d.txt', 'dir/sub/e.py', 'f.js']


def rline(rng):
    ind = rng.choice(INDENTS)
    if rng.random() < 0.18:
        return ''
    return ind + rng.choice(WORDS) + ('' if rng.random() < 0.6 else ' ' + rng.choice(WORDS))


def rfile(rng):
    n = rng.randrange(0, 7)
    lines = [rline(rng) for _ in range(n)]
    br = '\r\n' if rng.random() < 0.18 else '\n'
    final = rng.random() < 0.78
    if not lines:
        return ''
    return br.join(lines) + (br if final else '')


def rtree(rng):
    files = {}
    for name in rng.sample(NAMES, rng.randrange(0, 3)):
        files[name] = rfile(rng)
    return files


def split_ft(text):
    return ref.ft_of(text)


def unify(rng, files):
    """A unified diff, usually plausible."""
    names = list(files) or ['a.txt']
    out = []
    for _ in range(rng.randrange(1, 3)):
        name = rng.choice(names)
        ft = split_ft(files.get(name, ''))
        lines = ft[0]
        head = rng.random()
        if head < 0.12:
            out.append('--- /dev/null')
            out.append('+++ b/%s' % name)
            body = ['+' + rline(rng) for _ in range(rng.randrange(0, 4))]
            out.append('@@ -0,0 +1,%d @@' % len(body))
            out.extend(body)
            continue
        if head < 0.2:
            out.append('diff --git a/%s b/%s' % (name, name))
            out.append('new file mode 100644')
            out.append('--- /dev/null')
            out.append('+++ b/%s' % name)
            out.extend(['@@ -0,0 +1,1 @@', '+' + rline(rng)])
            continue
        if head < 0.3:
            out.append('--- a/%s' % name)
            out.append('+++ /dev/null')
            out.append('@@ -1,1 +0,0 @@')
            out.append('-' + rline(rng))
            continue
        other = rng.choice(NAMES + ['g.txt'])
        if head < 0.4 and other != name:
            out.append('diff --git a/%s b/%s' % (name, other))
            out.append('rename from %s' % name)
            out.append('rename to %s' % other)
            out.append('--- a/%s' % name)
            out.append('+++ b/%s' % other)
        else:
            if rng.random() < 0.5:
                out.append('diff --git a/%s b/%s' % (name, name))
                out.append('index 1111111..2222222 100644')
            out.append('--- a/%s' % name)
            out.append('+++ b/%s' % name)
        n = len(lines)
        for _h in range(rng.randrange(1, 3)):
            if n == 0:
                a = 1
                start, ln = 0, 0
            else:
                start = rng.randrange(0, n)
                ln = rng.randrange(1, min(4, n - start) + 1)
            body = []
            for i in range(start, start + ln):
                r = rng.random()
                if r < 0.45:
                    body.append(' ' + lines[i])
                elif r < 0.75:
                    body.append('-' + lines[i])
                    if rng.random() < 0.6:
                        body.append('+' + rline(rng))
                else:
                    body.append('+' + rline(rng))
            if ln == 0:
                body = ['+' + rline(rng) for _ in range(rng.randrange(1, 3))]
            if rng.random() < 0.18 and body and body[-1][:1] in (' ', '+'):
                body.append('\\ No newline at end of file')
            a = start + 1 if rng.random() < 0.75 else max(1, start + 1 + rng.randrange(-2, 3))
            oldn = sum(1 for b in body if b[:1] in (' ', '-') or b == '')
            newn = sum(1 for b in body if b[:1] in (' ', '+') or b == '')
            out.append('@@ -%d,%d +%d,%d @@' % (a, oldn, a, newn))
            out.extend(body)
    return '\n'.join(out) + '\n'


def searchrep(rng, files):
    names = list(files) or ['a.txt']
    out = []
    for _ in range(rng.randrange(1, 3)):
        name = rng.choice(names)
        ft = split_ft(files.get(name, ''))
        lines = ft[0]
        if rng.random() < 0.2:
            out.append(name)
            out.append('<<<<<<< SEARCH')
            out.append('=======')
            out.extend(rline(rng) for _ in range(rng.randrange(0, 3)))
            out.append('>>>>>>> REPLACE')
            continue
        if rng.random() < 0.75:
            out.append(name)
        if lines:
            start = rng.randrange(0, len(lines))
            ln = rng.randrange(1, min(3, len(lines) - start) + 1)
            old = lines[start:start + ln]
        else:
            old = []
        out.append('<<<<<<< SEARCH')
        out.extend(old)
        out.append('=======')
        out.extend(rline(rng) for _ in range(rng.randrange(0, 3)))
        out.append('>>>>>>> REPLACE')
    return '\n'.join(out) + '\n'


def v4a(rng, files):
    names = list(files) or ['a.txt']
    out = ['*** Begin Patch']
    for _ in range(rng.randrange(1, 3)):
        name = rng.choice(names)
        ft = split_ft(files.get(name, ''))
        lines = ft[0]
        r = rng.random()
        if r < 0.25:
            out.append('*** Add File: %s' % name)
            out.extend('+' + rline(rng) for _ in range(rng.randrange(0, 4)))
            continue
        if r < 0.4:
            out.append('*** Delete File: %s' % name)
            continue
        out.append('*** Update File: %s' % name)
        if rng.random() < 0.25:
            out.append('*** Move to: %s' % rng.choice(NAMES + ['h.txt']))
        for _c in range(rng.randrange(1, 3)):
            if rng.random() < 0.5 and lines:
                anchor = lines[rng.randrange(0, len(lines))]
                out.append('@@ ' + anchor if anchor.strip() else '@@')
                cur = [i for i, l in enumerate(lines) if l == anchor]
                start = (cur[0] + 1) if cur else 0
            else:
                out.append('@@')
                start = rng.randrange(0, max(1, len(lines)))
            if start < len(lines):
                ln = rng.randrange(1, min(3, len(lines) - start) + 1)
            else:
                ln = 0
            body = []
            for i in range(start, start + ln):
                q = rng.random()
                if q < 0.5:
                    body.append(' ' + lines[i])
                elif q < 0.8:
                    body.append('-' + lines[i])
                    if rng.random() < 0.6:
                        body.append('+' + rline(rng))
                else:
                    body.append('+' + rline(rng))
            if ln == 0 and rng.random() < 0.5:
                body = ['+' + rline(rng)]
            out.extend(body)
            if rng.random() < 0.15:
                out.append('*** End of File')
    out.append('*** End Patch')
    return '\n'.join(out) + '\n'


def jsonpatch(rng, files):
    names = list(files) or ['a.txt']
    edits = []
    for _ in range(rng.randrange(1, 3)):
        name = rng.choice(names)
        text = files.get(name, '')
        r = rng.random()
        if r < 0.3:
            edits.append({'command': 'create', 'path': name, 'file_text': rline(rng)})
        elif r < 0.65:
            lines = split_ft(text)[0]
            old = rng.choice(lines) if lines and rng.random() < 0.7 else rline(rng)
            edits.append({'command': 'str_replace', 'path': name, 'old_str': old,
                          'new_str': rline(rng)})
        else:
            edits.append({'command': 'insert', 'path': name,
                          'insert_line': rng.randrange(-1, len(split_ft(text)[0]) + 2),
                          'new_str': '\n'.join(rline(rng) for _ in range(rng.randrange(0, 3)))})
    if rng.random() < 0.5:
        return json.dumps(edits) + '\n'
    return ''.join(json.dumps(e) + '\n' for e in edits)


MUTATORS = []


def mutate(rng, text):
    lines = text.split('\n')
    for _ in range(rng.randrange(1, 3)):
        if not lines:
            break
        k = rng.randrange(len(lines))
        r = rng.random()
        if r < 0.2:
            lines.pop(k)
        elif r < 0.35:
            lines.insert(k, lines[k])
        elif r < 0.5:
            lines.insert(k, rng.choice(['', 'prose', '```', 'diff --git a/x b/y', '@@ bogus',
                                        '--- a/zz', '+++ b/zz', '*** End Patch', '*** Add File: q',
                                        '\\ No newline at end of file', '=======', '>>>>>>> REPLACE',
                                        '<<<<<<< SEARCH']))
        elif r < 0.65 and lines[k]:
            lines[k] = rng.choice([' ', '\t', '-', '+', '\\']) + lines[k]
        elif r < 0.8 and lines[k]:
            lines[k] = lines[k][1:]
        elif r < 0.9:
            lines[k] = lines[k] + rng.choice(['  ', '\t', ' '])
        else:
            lines[k] = lines[k].replace('@@', '@', 1)
    out = '\n'.join(lines)
    if rng.random() < 0.15:
        out = out.replace('\n', '\r\n')
    return out


def gen_case(rng, i):
    files = rtree(rng)
    kind = rng.random()
    if kind < 0.4:
        patch = unify(rng, files)
    elif kind < 0.6:
        patch = searchrep(rng, files)
    elif kind < 0.8:
        patch = v4a(rng, files)
    else:
        patch = jsonpatch(rng, files)
    if rng.random() < 0.45:
        patch = mutate(rng, patch)
    argv = []
    r = rng.random()
    if r < 0.2:
        argv.append('--format=' + rng.choice(['unified', 'search-replace', 'v4a', 'json', 'auto']))
    if rng.random() < 0.2:
        argv.append('--check')
    if rng.random() < 0.2:
        argv.append('--strict')
    use_file = rng.random() < 0.4
    if use_file:
        pname = rng.choice(['p.patch', 'patch.diff'])
        files[pname] = patch
        argv.append(pname)
        stdin = None
    else:
        stdin = patch
    return {'key': 'f%d' % i, 'argv': argv, 'stdin': stdin, 'files': files}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    rng = random.Random(seed)
    cases = [gen_case(rng, i) for i in range(n)]
    tmp = tempfile.mkdtemp()
    cf = os.path.join(tmp, 'cases.json')
    rf = os.path.join(tmp, 'res.json')
    with open(cf, 'w') as f:
        json.dump(cases, f)
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
            if bad <= 8:
                print('DIFF', c['key'], c['argv'])
                print('  stdin', json.dumps(c['stdin'])[:600])
                print('  files', json.dumps(c['files'])[:600])
                for w in why:
                    print('   ', w[:500])
    print('%d of %d agree (%d differ)' % (n - bad, n, bad))


if __name__ == '__main__':
    main()
