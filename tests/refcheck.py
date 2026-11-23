"""tests/refcheck.py — score tests/ref.py against practice.json (an oracle sanity check)."""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ref  # noqa: E402

path = sys.argv[1] if len(sys.argv) > 1 else 'practice.json'
checks = json.load(open(path))['checks']
bad = 0
for c in checks:
    want = dict(c['files'])
    for p, v in c['expect']['changes'].items():
        if v is None:
            want.pop(p, None)
        else:
            want[p] = v
    try:
        code, out, after = ref.run(c['argv'], c['stdin'], c['files'])
    except Exception as e:  # noqa: BLE001
        code, out, after = -1, 'EXC %r' % (e,), {}
    why = []
    if code != c['expect']['exit']:
        why.append('exit %r != %r' % (code, c['expect']['exit']))
    if out != c['expect']['stdout']:
        why.append('stdout %r != %r' % (out, c['expect']['stdout']))
    for p in sorted(set(want) | set(after)):
        if want.get(p) != after.get(p):
            why.append('%s %r != %r' % (p, after.get(p), want.get(p)))
    if why:
        bad += 1
        print('FAIL', c['family'], c['key'][:10], c['argv'])
        for w in why:
            print('   ', w)
print('%d of %d agree' % (len(checks) - bad, len(checks)))
