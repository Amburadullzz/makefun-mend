"""tests/anomaly.py — look for main.ts crashing or misbehaving (exit outside 0-2, empty stdout, bad output shape)."""
import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import fuzz  # noqa: E402  (its generators)
import fuzz2  # noqa: E402


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    rng = random.Random(seed)
    cases = []
    for i in range(n):
        if rng.random() < 0.5:
            cases.append(fuzz.gen_case(rng, i))
        else:
            cases.append(fuzz2.gen_case(rng, i))
    tmp = tempfile.mkdtemp()
    cf = os.path.join(tmp, 'c.json')
    rf = os.path.join(tmp, 'r.json')
    json.dump(cases, open(cf, 'w'))
    subprocess.run(['node', os.path.join(HERE, 'diffrun.ts'), cf, rf], check=True, cwd=ROOT)
    got = json.load(open(rf))
    bad = 0
    for c, g in zip(cases, got):
        out = g['stdout']
        why = []
        if g['exit'] not in (0, 1, 2):
            why.append('exit %r' % g['exit'])
        if g['exit'] == 1 and out == '':
            why.append('exit 1 with no stdout (crash?)')
        if g['exit'] == 2 and out.count('\n') != 1:
            why.append('exit 2 with %d lines' % out.count('\n'))
        if g['exit'] == 0:
            if not out.endswith('\n'):
                why.append('exit 0 stdout does not end with a newline')
            last = out.rstrip('\n').split('\n')[-1]
            if not (last == 'ok: 0 files' or last.startswith('ok: ') and last.endswith((' file', ' files'))):
                why.append('bad ok line %r' % last)
            if not out.endswith('\n'):
                why.append('no final newline')
        if why:
            bad += 1
            if bad <= 10:
                print('ANOMALY', c['key'], c['argv'], why)
                print('  stdin', json.dumps(c['stdin'])[:400])
                print('  files', json.dumps(c['files'])[:300])
                print('  out', repr(out)[:300])
    print('%d anomalies of %d' % (bad, n))


main()
