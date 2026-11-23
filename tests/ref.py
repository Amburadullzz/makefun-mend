"""tests/ref.py — an independent implementation of SPEC.md (version 1), written from the spec text.

Used only as an oracle for differential testing against main.ts. Where SPEC.md admits two readings this
picks the plain one and the choice is recorded in AUDIT.md.
"""
import json
import os
import re

FORMATS = ['auto', 'unified', 'search-replace', 'v4a', 'json']
SR_SEARCH = '<<<<<<< SEARCH'
SR_EQ = '======='
SR_END = '>>>>>>> REPLACE'


class Usage(Exception):
    pass


class NoPatch(Exception):
    pass


class NoFormat(Exception):
    pass


class Parse(Exception):
    def __init__(self, n):
        self.n = n


class Fail(Exception):
    def __init__(self, code, path):
        self.code = code
        self.path = path


def blank(s):
    return s.strip() == ''


def trim_end_st(s):
    return re.sub(r'[ \t]+$', '', s)


def trim_end_ws(s):
    """7.2: the SEARCH/REPLACE markers are matched with their trailing whitespace removed."""
    return s.rstrip()


def trim_st(s):
    return re.sub(r'^[ \t]+', '', re.sub(r'[ \t]+$', '', s))


def lead_st(s):
    m = re.match(r'[ \t]*', s)
    return m.group(0)


def patch_lines(text):
    """3.9: split at \n, one trailing \r removed, no empty piece after a final \n."""
    if text == '':
        return []
    lines = text.split('\n')
    if text.endswith('\n'):
        lines.pop()
    return [l[:-1] if l.endswith('\r') else l for l in lines]


def is_safe(p):
    if p is None or p == '' or p.startswith('/') or '\\' in p:
        return False
    return all(s != '' and s != '.' and s != '..' for s in p.split('/'))


def ft_of(text):
    """3.4-3.6: (lines, final, br)."""
    nl = text.find('\n')
    br = '\r\n' if (nl > 0 and text[nl - 1] == '\r') else '\n'
    if text == '':
        return ([], False, br)
    parts = text.split(br)
    final = text.endswith(br)
    if final:
        parts.pop()
    return (parts, final, br)


def ft_text(ft):
    lines, final, br = ft
    if not lines:
        return ''
    return br.join(lines) + (br if final else '')


def line_eq(a, b, tier):
    if a == b:
        return True
    if tier == 0:
        return False
    if tier == 1:
        return trim_end_st(a) == trim_end_st(b)
    return trim_st(a) == trim_st(b)


def occurrences(f, old, tier):
    out = []
    m = len(old)
    for i in range(0, len(f) - m + 1):
        if all(line_eq(f[i + j], old[j], tier) for j in range(m)):
            out.append(i)
    return out


def find_tier(f, old, strict):
    for t in range(0, 1 if strict else 3):
        pos = occurrences(f, old, t)
        if pos:
            return (t, pos)
    return None


def nearest(pos, target):
    best = pos[0]
    bd = abs(best - target)
    for p in pos:
        d = abs(p - target)
        if d < bd:
            best, bd = p, d
    return best


class Tree:
    def __init__(self, files):
        self.orig = {}
        self.now = {}
        self.files = files

    def original(self, p):
        if p not in self.orig:
            self.orig[p] = self.files.get(p)
        return self.orig[p]

    def get(self, p):
        if p in self.now:
            return self.now[p]
        return self.original(p)


class Ctx:
    def __init__(self):
        self.moves = []


# ---------------------------------------------------------------- section 5


def body_parts(body):
    """body: list of (kind, text). Returns O and N items [(is_ctx, oi, text)]."""
    O, items = [], []
    for kind, text in body:
        if kind == 'ctx':
            items.append((True, len(O), text))
            O.append(text)
        elif kind == 'old':
            O.append(text)
        elif kind == 'new':
            items.append((False, -1, text))
    return O, items


def no_newline_flag(body):
    last = -1
    for i, (kind, _) in enumerate(body):
        if kind in ('ctx', 'new'):
            last = i
    return last >= 0 and last + 1 < len(body) and body[last + 1][0] == 'note'


def render_n(items, f, start, tier, old):
    F = lead_st(f[start]) if start < len(f) else ''
    P = lead_st(old[0]) if old else ''
    out = []
    for is_ctx, oi, text in items:
        if is_ctx:
            out.append(f[start + oi])
            continue
        s = text
        if tier == 2 and s != '' and trim_st(s) != '' and s.startswith(P):
            s = F + s[len(P):]
        out.append(s)
    return out


# ---------------------------------------------------------------- section 6

DIFF_RE = re.compile(r'^diff --git a/(.+) b/(.+)$')
HUNK_RE = re.compile(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')


def path_of(s):
    p = s.split('\t')[0].strip(' ')
    if p == '/dev/null':
        return None
    if p.startswith('a/') or p.startswith('b/'):
        p = p[2:]
    return p


def body_of(raw):
    if raw == '':
        return ('ctx', '')
    c = raw[0]
    if c == ' ':
        return ('ctx', raw[1:])
    if c == '-':
        return ('old', raw[1:])
    if c == '+':
        return ('new', raw[1:])
    return ('note', raw)


def is_body_line(l):
    return l == '' or l[0] in ' -+\\'


def parse_unified(lines):
    """Returns a list of sections (dicts) or raises Parse."""
    secs = []
    cur = None
    phase = 'header'
    hunk = None
    in_body = False

    def close_body():
        nonlocal hunk, in_body
        if hunk is not None:
            while hunk['body'] and hunk['body'][-1][2] == '':
                hunk['body'].pop()
        hunk = None
        in_body = False

    i = 0
    while i < len(lines):
        L = lines[i]
        NX = lines[i + 1] if i + 1 < len(lines) else ''
        is_hunk = L.startswith('@@')
        if L.startswith('diff --git '):
            m = DIFF_RE.match(L)
            if m is None:
                raise Parse(i + 1)
            close_body()
            cur = {'hasDiff': True, 'diffA': m.group(1), 'diffB': m.group(2),
                   'hasOld': False, 'pOld': None, 'hasNew': False, 'pNew': None,
                   'hasROld': False, 'rOld': None, 'hasRNew': False, 'rNew': None,
                   'create': False, 'del': False, 'hunks': []}
            secs.append(cur)
            phase = 'header'
            i += 1
            continue
        if L.startswith('--- ') and NX.startswith('+++ ') and not (cur is not None and phase == 'header'):
            close_body()
            cur = {'hasDiff': False, 'diffA': None, 'diffB': None,
                   'hasOld': False, 'pOld': None, 'hasNew': False, 'pNew': None,
                   'hasROld': False, 'rOld': None, 'hasRNew': False, 'rNew': None,
                   'create': False, 'del': False, 'hunks': []}
            secs.append(cur)
            phase = 'header'
        if cur is None:
            if is_hunk:
                raise Parse(i + 1)
            i += 1
            continue
        if phase == 'header':
            if is_hunk:
                m = HUNK_RE.match(L)
                if m is None:
                    raise Parse(i + 1)
                close_body()
                hunk = {'a': int(m.group(1)), 'body': []}
                cur['hunks'].append(hunk)
                phase = 'body'
                in_body = True
                i += 1
                continue
            if L.startswith('--- '):
                if not NX.startswith('+++ '):
                    raise Parse(i + 2)
                cur['hasOld'] = True
                cur['pOld'] = path_of(L[4:])
                cur['hasNew'] = True
                cur['pNew'] = path_of(NX[4:])
                i += 2
                continue
            if L.startswith('+++ '):
                cur['hasNew'] = True
                cur['pNew'] = path_of(L[4:])
                i += 1
                continue
            if L.startswith('rename from '):
                cur['hasROld'] = True
                cur['rOld'] = L[12:].strip()
                i += 1
                continue
            if L.startswith('rename to '):
                cur['hasRNew'] = True
                cur['rNew'] = L[10:].strip()
                i += 1
                continue
            if L.startswith('new file mode'):
                cur['create'] = True
                i += 1
                continue
            if L.startswith('deleted file mode'):
                cur['del'] = True
                i += 1
                continue
            i += 1
            continue
        if is_hunk:
            m = HUNK_RE.match(L)
            if m is None:
                raise Parse(i + 1)
            close_body()
            hunk = {'a': int(m.group(1)), 'body': []}
            cur['hunks'].append(hunk)
            in_body = True
            i += 1
            continue
        if in_body and is_body_line(L):
            hunk['body'].append((body_of(L)[0], body_of(L)[1], L))
            i += 1
            continue
        if in_body:
            close_body()
        i += 1
    close_body()
    return secs


def apply_hunks(ft, hunks, strict, path):
    lines, final, br = ft
    lines = list(lines)
    d = 0
    for h in hunks:
        body = [(k, t) for (k, t, _) in h['body']]
        O, items = body_parts(body)
        nonl = no_newline_flag(body)
        oldlen = len(lines)
        if not O:
            pos = min(max(h['a'] + d, 0), oldlen)
            N = render_n(items, lines, pos, 0, O)
            lines = lines[:pos] + N + lines[pos:]
            d += len(N)
            if pos == oldlen:
                final = not nonl
            continue
        target = h['a'] - 1 + d
        tier = None
        pos = None
        for t in range(0, 1 if strict else 3):
            ps = occurrences(lines, O, t)
            if ps:
                tier = t
                pos = nearest(ps, target)
                break
        if tier is None:
            raise Fail('no-match', path)
        N = render_n(items, lines, pos, tier, O)
        at_end = pos + len(O) == oldlen
        lines = lines[:pos] + N + lines[pos + len(O):]
        d += len(N) - len(O)
        if at_end:
            final = not nonl
    return (lines, final, br)


def created_ft(hunks):
    lines = []
    nonl = False
    for h in hunks:
        body = h['body']
        for i, (kind, text, _raw) in enumerate(body):
            if kind == 'new':
                lines.append(text)
                nonl = i + 1 < len(body) and body[i + 1][0] == 'note'
    return (lines, not nonl, '\n')


def section_edit(s, strict):
    def edit(tree, ctx):
        old = s['pOld'] if s['hasOld'] else (s['rOld'] if s['hasROld'] else (s['diffA'] if s['hasDiff'] else None))
        new = s['pNew'] if s['hasNew'] else (s['rNew'] if s['hasRNew'] else (s['diffB'] if s['hasDiff'] else None))
        is_create = s['create'] or old is None
        is_del = s['del'] or new is None
        essential = new if is_create else old
        if essential is None:
            raise Fail('unsafe', '')
        for p in (old, new):
            if p is not None and not is_safe(p):
                raise Fail('unsafe', p)
        if is_create:
            if tree.get(new) is not None:
                raise Fail('exists', new)
            tree.now[new] = ft_text(created_ft(s['hunks']))
            return
        if is_del:
            if tree.get(old) is None:
                raise Fail('missing', old)
            tree.now[old] = None
            return
        if old != new:
            before = tree.get(old)
            if before is None:
                raise Fail('missing', old)
            if tree.get(new) is not None:
                raise Fail('exists', new)
            out = apply_hunks(ft_of(before), s['hunks'], strict, old)
            tree.now[new] = ft_text(out)
            tree.now[old] = None
            ctx.moves.append((old, new))
            return
        before = tree.get(old)
        if before is None:
            raise Fail('missing', old)
        tree.now[old] = ft_text(apply_hunks(ft_of(before), s['hunks'], strict, old))
    return edit


# ---------------------------------------------------------------- section 7


def search_replace_edits(lines, strict):
    blocks = []
    prev_end = 0
    prev_path = None
    i = 0
    while i < len(lines):
        if trim_end_ws(lines[i]) != SR_SEARCH:
            i += 1
            continue
        search_line = i
        eq = -1
        for j in range(i + 1, len(lines)):
            if trim_end_ws(lines[j]) == SR_EQ:
                eq = j
                break
        end = -1
        if eq >= 0:
            for j in range(eq + 1, len(lines)):
                if trim_end_ws(lines[j]) == SR_END:
                    end = j
                    break
        if eq < 0 or end < 0:
            raise Parse(search_line + 1)
        path = None
        for j in range(search_line - 1, prev_end - 1, -1):
            t = lines[j]
            if blank(t):
                continue
            if t.strip().startswith('```'):
                continue
            path = t.strip()
            break
        if path is None:
            path = prev_path
        if path is None:
            raise Parse(search_line + 1)
        blocks.append((path, lines[search_line + 1:eq], lines[eq + 1:end]))
        prev_path = path
        prev_end = end + 1
        i = end + 1

    def mk(b):
        path, old, nw = b

        def edit(tree, ctx):
            if not is_safe(path):
                raise Fail('unsafe', path)
            if not old:
                if tree.get(path) is not None:
                    raise Fail('exists', path)
                tree.now[path] = '' if not nw else '\n'.join(nw) + '\n'
                return
            before = tree.get(path)
            if before is None:
                raise Fail('missing', path)
            ft = ft_of(before)
            found = find_tier(ft[0], old, strict)
            if found is None:
                raise Fail('no-match', path)
            tier, pos = found
            if len(pos) > 1:
                raise Fail('ambiguous', path)
            items = [(False, -1, t) for t in nw]
            N = render_n(items, ft[0], pos[0], tier, old)
            out = ft[0][:pos[0]] + N + ft[0][pos[0] + len(old):]
            tree.now[path] = ft_text((out, ft[1], ft[2]))
        return edit

    return [mk(b) for b in blocks]


# ---------------------------------------------------------------- section 8


def v4a_body_line(l):
    return l == '' or l[0] in ' -+'


def v4a_edits(lines, strict):
    nb = [(l, i) for i, l in enumerate(lines) if not blank(l)]
    if not nb:
        raise Parse(1)
    fi, fidx = nb[0]
    if fi.strip() != '*** Begin Patch':
        raise Parse(fidx + 1)
    lv, lidx = nb[-1]
    if lv.strip() != '*** End Patch':
        raise Parse(len(lines) + 1)
    secs = []
    cur = None
    for i in range(fidx + 1, lidx):
        L = lines[i]
        if blank(L) and cur is None:
            continue
        if L.startswith('*** Add File: '):
            cur = {'kind': 'add', 'path': L[14:].strip(), 'line': i + 1, 'body': [], 'moveTo': None, 'chunks': []}
            secs.append(cur)
            continue
        if L.startswith('*** Delete File: '):
            cur = {'kind': 'delete', 'path': L[17:].strip(), 'line': i + 1, 'body': [], 'moveTo': None, 'chunks': []}
            secs.append(cur)
            continue
        if L.startswith('*** Update File: '):
            cur = {'kind': 'update', 'path': L[17:].strip(), 'line': i + 1, 'body': [], 'moveTo': None, 'chunks': []}
            secs.append(cur)
            continue
        if cur is None:
            raise Parse(i + 1)
        cur['body'].append((L, i + 1))
    for s in secs:
        while s['body'] and blank(s['body'][-1][0]):
            s['body'].pop()
    edits = []
    for s in secs:
        if s['kind'] == 'add':
            content = []
            for raw, no in s['body']:
                if raw == '':
                    content.append('')
                elif raw.startswith('+'):
                    content.append(raw[1:])
                else:
                    raise Parse(no)
            path = s['path']

            def add_edit(tree, ctx, path=path, content=content):
                if not is_safe(path):
                    raise Fail('unsafe', path)
                if tree.get(path) is not None:
                    raise Fail('exists', path)
                tree.now[path] = '' if not content else '\n'.join(content) + '\n'
            edits.append(add_edit)
            continue
        if s['kind'] == 'delete':
            for raw, no in s['body']:
                raise Parse(no)
            path = s['path']

            def del_edit(tree, ctx, path=path):
                if not is_safe(path):
                    raise Fail('unsafe', path)
                if tree.get(path) is None:
                    raise Fail('missing', path)
                tree.now[path] = None
            edits.append(del_edit)
            continue
        k = 0
        if s['body'] and s['body'][0][0].startswith('*** Move to: '):
            s['moveTo'] = s['body'][0][0][13:].strip()
            k = 1
        chunk = None
        while k < len(s['body']):
            raw, no = s['body'][k]
            if raw.startswith('@@'):
                txt = raw[2:].strip()
                chunk = {'anchor': None if txt == '' else txt, 'eof': False, 'body': []}
                s['chunks'].append(chunk)
                k += 1
                continue
            if raw.strip() == '*** End of File':
                if chunk is None:
                    raise Parse(no)
                chunk['eof'] = True
                chunk = None
                k += 1
                continue
            if v4a_body_line(raw):
                if chunk is None:
                    if s['chunks']:
                        raise Parse(no)
                    chunk = {'anchor': None, 'eof': False, 'body': []}
                    s['chunks'].append(chunk)
                chunk['body'].append((body_of(raw)[0], body_of(raw)[1], raw))
                k += 1
                continue
            raise Parse(no)
        if not s['chunks'] and s['moveTo'] is None:
            raise Parse(s['line'])
        path = s['path']
        moveTo = s['moveTo']
        chunks = s['chunks']

        def upd_edit(tree, ctx, path=path, moveTo=moveTo, chunks=chunks):
            if not is_safe(path):
                raise Fail('unsafe', path)
            if moveTo is not None and not is_safe(moveTo):
                raise Fail('unsafe', moveTo)
            before = tree.get(path)
            if before is None:
                raise Fail('missing', path)
            if moveTo is not None and tree.get(moveTo) is not None:
                raise Fail('exists', moveTo)
            ft = ft_of(before)
            lines = list(ft[0])
            cursor = 0
            for ch in chunks:
                if ch['anchor'] is not None:
                    at = -1
                    for t in range(0, 1 if strict else 3):
                        for j in range(cursor, len(lines)):
                            if line_eq(lines[j], ch['anchor'], t):
                                at = j
                                break
                        if at >= 0:
                            break
                    if at < 0:
                        raise Fail('no-match', path)
                    cursor = at + 1
                body = [(kk, tt) for (kk, tt, _r) in ch['body']]
                O, items = body_parts(body)
                if not O:
                    pos = len(lines) if ch['eof'] else cursor
                    N = render_n(items, lines, pos, 0, O)
                    lines = lines[:pos] + N + lines[pos:]
                    cursor = pos + len(N)
                    continue
                tier = None
                pos = None
                for t in range(0, 1 if strict else 3):
                    for j in range(cursor, len(lines) - len(O) + 1):
                        if ch['eof'] and j + len(O) != len(lines):
                            continue
                        if all(line_eq(lines[j + q], O[q], t) for q in range(len(O))):
                            tier, pos = t, j
                            break
                    if pos is not None:
                        break
                if pos is None:
                    raise Fail('no-match', path)
                N = render_n(items, lines, pos, tier, O)
                lines = lines[:pos] + N + lines[pos + len(O):]
                cursor = pos + len(N)
            out = ft_text((lines, ft[1], ft[2]))
            if moveTo is None:
                tree.now[path] = out
            else:
                tree.now[moveTo] = out
                tree.now[path] = None
                ctx.moves.append((path, moveTo))
        edits.append(upd_edit)
    return edits


# ---------------------------------------------------------------- section 9


def json_edit(v):
    if not isinstance(v, dict):
        return None
    if not isinstance(v.get('path'), str):
        return None
    p = v['path']
    cmd = v.get('command')

    def need(tree):
        if not is_safe(p):
            raise Fail('unsafe', p)
        t = tree.get(p)
        if t is None:
            raise Fail('missing', p)
        return t

    if cmd == 'create' and isinstance(v.get('file_text'), str):
        text = v['file_text']

        def create(tree, ctx):
            if not is_safe(p):
                raise Fail('unsafe', p)
            if tree.get(p) is not None:
                raise Fail('exists', p)
            tree.now[p] = text
        return create
    if cmd == 'str_replace' and isinstance(v.get('old_str'), str) and v['old_str'] != '' and \
            ('new_str' not in v or isinstance(v.get('new_str'), str)):
        old_str = v['old_str']
        new_str = v.get('new_str', '')

        def sr(tree, ctx):
            before = need(tree)
            ft = ft_of(before)
            crlf = ft[2] == '\r\n'

            def fold(s):
                return s.replace('\r\n', '\n') if crlf else s
            t = fold(before)
            o = fold(old_str)
            cnt = 0
            k = t.find(o)
            while k >= 0:
                cnt += 1
                k = t.find(o, k + 1)
            if cnt == 0:
                raise Fail('no-match', p)
            if cnt > 1:
                raise Fail('ambiguous', p)
            at = t.find(o)
            out = t[:at] + fold(new_str) + t[at + len(o):]
            tree.now[p] = out.replace('\n', '\r\n') if crlf else out
        return sr
    if cmd == 'insert' and isinstance(v.get('insert_line'), int) and not isinstance(v.get('insert_line'), bool) \
            and isinstance(v.get('new_str'), str):
        line = v['insert_line']
        add = patch_lines(v['new_str'])

        def ins(tree, ctx):
            before = need(tree)
            ft = ft_of(before)
            lines = list(ft[0])
            if line < 0 or line > len(lines):
                raise Fail('no-match', p)
            lines[line:line] = add
            tree.now[p] = ft_text((lines, ft[1], ft[2]))
        return ins
    return None


def json_edits(text, lines):
    first = -1
    for i, l in enumerate(lines):
        if not blank(l):
            first = i
            break
    if first < 0:
        raise Parse(1)
    if lines[first].lstrip().startswith('['):
        try:
            v = json.loads(text)
        except Exception:
            raise Parse(first + 1)
        if not isinstance(v, list):
            raise Parse(first + 1)
        out = []
        for x in v:
            e = json_edit(x)
            if e is None:
                raise Parse(first + 1)
            out.append(e)
        return out
    out = []
    for i, l in enumerate(lines):
        if blank(l):
            continue
        try:
            e = json_edit(json.loads(l))
        except Exception:
            e = None
        if e is None:
            raise Parse(i + 1)
        out.append(e)
    return out


# ---------------------------------------------------------------- section 4


def detect(lines):
    first = -1
    for i, l in enumerate(lines):
        if not blank(l):
            first = i
            break
    if first < 0:
        return None
    if lines[first].strip() == '*** Begin Patch':
        return 'v4a'
    c = lines[first].lstrip()[:1]
    if c in ('[', '{'):
        return 'json'
    if any(trim_end_st(l) == SR_SEARCH for l in lines):
        return 'search-replace'
    for i, l in enumerate(lines):
        if l.startswith('diff --git '):
            return 'unified'
        if l.startswith('--- ') and i + 1 < len(lines) and lines[i + 1].startswith('+++ '):
            return 'unified'
    return None


# ---------------------------------------------------------------- the command line


def run(argv, stdin_text, files):
    """Returns (exit, stdout, files_after)."""
    fmt = None
    check = False
    strict = False
    pos = []
    ended = False
    for a in argv:
        if not ended and a == '--':
            ended = True
        elif not ended and a.startswith('-') and a != '-':
            if a.startswith('--format=') and fmt is None and a[9:] in FORMATS:
                fmt = a[9:]
            elif a == '--check' and not check:
                check = True
            elif a == '--strict' and not strict:
                strict = True
            else:
                return (2, 'error: usage\n', dict(files))
        else:
            pos.append(a)
    if len(pos) > 1:
        return (2, 'error: usage\n', dict(files))
    if not pos or pos[0] == '-':
        text = stdin_text if stdin_text is not None else ''
    else:
        if pos[0] not in files:
            return (2, 'error: no-patch\n', dict(files))
        text = files[pos[0]]
    lines = patch_lines(text)
    kind = detect(lines) if fmt is None or fmt == 'auto' else fmt
    if kind is None:
        return (2, 'error: format\n', dict(files))
    try:
        if kind == 'json':
            edits = json_edits(text, lines)
        elif kind == 'unified':
            edits = [section_edit(s, strict) for s in parse_unified(lines)]
        elif kind == 'search-replace':
            edits = search_replace_edits(lines, strict)
        else:
            edits = v4a_edits(lines, strict)
        if not edits:
            raise Parse(1)
    except Parse as e:
        return (2, 'error: parse %d\n' % e.n, dict(files))
    tree = Tree(files)
    ctx = Ctx()
    fails = []
    for e in edits:
        before = dict(tree.now)
        nm = len(ctx.moves)
        try:
            e(tree, ctx)
        except Fail as f:
            tree.now = dict(before)
            del ctx.moves[nm:]
            fails.append(f)
    if fails:
        out = ''.join('error: %s %s\n' % (f.code, f.path) for f in fails)
        out += 'failed: %d of %d edits\n' % (len(fails), len(edits))
        return (1, out, dict(files))
    changed = {}
    for p, now in tree.now.items():
        was = tree.original(p)
        if was == now:
            continue
        changed[p] = 'A' if was is None else ('D' if now is None else 'M')
    rows = []
    taken = set()
    for (fr, to) in ctx.moves:
        if fr in taken or to in taken:
            continue
        if changed.get(fr) != 'D' or changed.get(to) != 'A':
            continue
        if tree.original(fr) is None or tree.original(to) is not None:
            continue
        if tree.now.get(fr) is not None or tree.now.get(to) is None:
            continue
        rows.append((to, 'R %s -> %s' % (fr, to)))
        taken.add(fr)
        taken.add(to)
    for p, st in changed.items():
        if p in taken:
            continue
        rows.append((p, '%s %s' % (st, p)))
    rows.sort(key=lambda r: r[0].encode('utf-8'))
    after = dict(files)
    for p, st in changed.items():
        if st == 'D':
            after.pop(p, None)
        else:
            after[p] = tree.now[p]
    out = ''.join(t + '\n' for _, t in rows)
    out += 'ok: %d %s\n' % (len(rows), 'file' if len(rows) == 1 else 'files')
    return (0, out, dict(files) if check else after)
