// main.ts is mend, the CLI SPEC.md specifies (MIT). It reads a patch in one of four formats (unified diff,
// SEARCH/REPLACE blocks, V4A patches, JSON edit objects) and applies it to the tree all-or-nothing.
//
// In a trial the grader runs `node /bot/main.ts` with MAKEFUN_ENV_URL set, and then this file hands over to runner.ts
// (the last lines below): keep that hand-over and runner.ts as they are, and write the CLI.

/*
  Formats read (auto-detected, or named with --format=): unified diff, SEARCH/REPLACE blocks,
  V4A patches, JSON edit objects.
  Matching tiers, tried in order until one finds the block: 0 exact, 1 ignoring trailing spaces
  and tabs, 2 ignoring leading and trailing spaces and tabs. The first tier that matches wins;
  --strict allows tier 0 only.
*/

import { mkdirSync, readFileSync, rmSync, statSync, writeFileSync, writeSync } from 'node:fs';
import { dirname } from 'node:path';

const FORMATS = ['auto', 'unified', 'search-replace', 'v4a', 'json'];

class Exit2 {
  readonly line: string;
  constructor(line: string) {
    this.line = line;
  }
}
class Fail {
  readonly code: string;
  readonly path: string;
  constructor(code: string, path: string) {
    this.code = code;
    this.path = path;
  }
}

const blank = (s: string): boolean => s.trim() === '';
/** Trailing spaces and tabs removed (tier 1). */
const trimEndST = (s: string): string => s.replace(/[ \t]+$/, '');
/** Trailing whitespace removed (the SEARCH/REPLACE markers, section 7.2). */
const trimEndWS = (s: string): string => s.trimEnd();
/** Leading and trailing spaces and tabs removed (tier 2). */
const trimST = (s: string): string => s.replace(/^[ \t]+/, '').replace(/[ \t]+$/, '');
/** The leading spaces and tabs of a line. */
const leadST = (s: string): string => {
  const m = /^[ \t]*/.exec(s);
  return m === null ? '' : m[0];
};

/** Report a fault on standard error, which no check compares; a closed stderr must not throw either. */
function note(msg: string): void {
  try {
    process.stderr.write(msg);
  } catch {
    // Nothing left to report to.
  }
}

/** Write every byte to standard output: one writeSync may take fewer bytes than asked on a pipe. */
function writeAll(text: string): void {
  const buf = Buffer.from(text, 'utf8');
  let off = 0;
  while (off < buf.length) {
    const n = writeSync(1, buf, off, buf.length - off);
    if (n <= 0) return;
    off += n;
  }
}

/** A patch's lines: split at \n, one trailing \r removed, no empty piece after a final \n. */
function patchLines(text: string): string[] {
  if (text === '') return [];
  const lines = text.split('\n');
  if (text.endsWith('\n')) lines.pop();
  return lines.map((l) => (l.endsWith('\r') ? l.slice(0, -1) : l));
}

function isSafePath(p: string): boolean {
  // Section 3's four conditions, plus the two limits no file system can honour: a NUL byte (Node's fs refuses
  // it outright) and a path or segment longer than the kernel's PATH_MAX / NAME_MAX, which would throw where it
  // is written. Such a path cannot name a file, so it is not a safe path and the edit fails with `unsafe`.
  if (p === '' || p.startsWith('/') || p.includes('\\') || p.includes('\u0000')) return false;
  if (Buffer.byteLength(p, 'utf8') > MAX_PATH_BYTES) return false;
  return p
    .split('/')
    .every((s) => s !== '' && s !== '.' && s !== '..' && Buffer.byteLength(s, 'utf8') <= MAX_SEGMENT_BYTES);
}

/** PATH_MAX is 4096 bytes with the terminating NUL; a margin keeps the working directory's own prefix in it. */
const MAX_PATH_BYTES = 3800;
/** NAME_MAX is 255 bytes on every file system the checks can run on. */
const MAX_SEGMENT_BYTES = 255;

/* ---------------------------------------------------------------- files and lines (section 3) */

type Br = '\n' | '\r\n';
interface FT {
  lines: string[];
  final: boolean;
  br: Br;
}

function ftOf(text: string): FT {
  const nl = text.indexOf('\n');
  const br: Br = nl > 0 && text[nl - 1] === '\r' ? '\r\n' : '\n';
  if (text === '') return { lines: [], final: false, br };
  const parts = text.split(br);
  const final = text.endsWith(br);
  if (final) parts.pop();
  return { lines: parts, final, br };
}

function ftText(ft: FT): string {
  if (ft.lines.length === 0) return '';
  return ft.lines.join(ft.br) + (ft.final ? ft.br : '');
}

/** The FT a re-read of the file's bytes would give. Only two shapes differ, and both are the empty or the
 * CR-leading line: a file of no lines is written empty, and a first line ending in \r makes the break CRLF. */
function normFT(ft: FT): FT {
  if (ft.lines.length === 0) return { lines: [], final: false, br: '\n' };
  if (ft.br === '\n' && ft.lines[0]!.endsWith('\r') && (ft.lines.length > 1 || ft.final)) return ftOf(ftText(ft));
  return ft;
}

/** The tree as the edits leave it: changed paths in `now` (null: deleted), the rest read from disk. */
class Tree {
  private readonly orig = new Map<string, FT | null>();
  private readonly there = new Map<string, boolean>();
  readonly now = new Map<string, FT | null>();
  /** The writes the edit in flight made, so a failure undoes just those and not a copy of the whole map. */
  private journal: { p: string; had: boolean; prev: FT | null }[] | null = null;
  original(p: string): FT | null {
    if (!this.orig.has(p)) {
      let v: FT | null = null;
      try {
        if (statSync(p).isFile()) v = ftOf(readFileSync(p, 'utf8'));
      } catch {
        v = null;
      }
      this.orig.set(p, v);
    }
    return this.orig.get(p)!;
  }
  get(p: string): FT | null {
    return this.now.has(p) ? this.now.get(p)! : this.original(p);
  }
  /** Whether anything at all is at `p`: a file, a directory, a link. A create there is `exists`. */
  exists(p: string): boolean {
    if (this.now.has(p)) return this.now.get(p) !== null;
    if (!this.there.has(p)) {
      let yes = false;
      try {
        statSync(p);
        yes = true;
      } catch {
        yes = false;
      }
      this.there.set(p, yes);
    }
    return this.there.get(p)!;
  }
  /** Start recording writes: one edit's worth. */
  begin(): void {
    this.journal = [];
  }
  set(p: string, v: FT | null): void {
    if (this.journal !== null) {
      this.journal.push({ p, had: this.now.has(p), prev: this.now.has(p) ? this.now.get(p)! : null });
    }
    this.now.set(p, v === null ? null : normFT(v));
  }
  /** Undo the writes since begin(). */
  rollback(): void {
    if (this.journal !== null) {
      for (let i = this.journal.length - 1; i >= 0; i -= 1) {
        const j = this.journal[i]!;
        if (j.had) this.now.set(j.p, j.prev);
        else this.now.delete(j.p);
      }
    }
    this.journal = null;
  }
  commit(): void {
    this.journal = null;
  }
}

interface Ctx {
  moves: { from: string; to: string }[];
}
type Edit = (tree: Tree, ctx: Ctx) => void;

/* ---------------------------------------------------------------- matching lines (section 5) */

function lineEq(a: string, b: string, tier: number): boolean {
  if (a === b) return true;
  if (tier === 0) return false;
  if (tier === 1) return trimEndST(a) === trimEndST(b);
  return trimST(a) === trimST(b);
}

function occurrences(file: string[], old: string[], tier: number): number[] {
  const out: number[] = [];
  const n = file.length;
  const m = old.length;
  for (let i = 0; i + m <= n; i += 1) {
    let ok = true;
    for (let j = 0; j < m; j += 1) {
      if (!lineEq(file[i + j]!, old[j]!, tier)) {
        ok = false;
        break;
      }
    }
    if (ok) out.push(i);
  }
  return out;
}

/** The first tier at which `old` occurs, and its occurrences there. */
function findTier(file: string[], old: string[], strict: boolean): { tier: number; pos: number[] } | null {
  const maxTier = strict ? 0 : 2;
  for (let t = 0; t <= maxTier; t += 1) {
    const pos = occurrences(file, old, t);
    if (pos.length > 0) return { tier: t, pos };
  }
  return null;
}

function nearest(pos: number[], target: number): number {
  let best = pos[0]!;
  let bestD = Math.abs(best - target);
  for (const p of pos) {
    const dd = Math.abs(p - target);
    if (dd < bestD) {
      best = p;
      bestD = dd;
    }
  }
  return best;
}

/** One line of a hunk or chunk body: a context line, an old line, a new line, or a `\` note. */
interface BodyLine {
  kind: 'ctx' | 'old' | 'new' | 'note';
  text: string;
  raw: string;
}

interface NItem {
  ctx: boolean;
  oi: number;
  text: string;
}

function bodyParts(body: BodyLine[]): { O: string[]; items: NItem[] } {
  const O: string[] = [];
  const items: NItem[] = [];
  for (const b of body) {
    if (b.kind === 'ctx') {
      items.push({ ctx: true, oi: O.length, text: b.text });
      O.push(b.text);
    } else if (b.kind === 'old') {
      O.push(b.text);
    } else if (b.kind === 'new') {
      items.push({ ctx: false, oi: -1, text: b.text });
    }
  }
  return { O, items };
}

/** Whether a `\` line follows the body's last context or `+` line. */
function noNewlineFlag(body: BodyLine[]): boolean {
  let last = -1;
  for (let i = 0; i < body.length; i += 1) if (body[i]!.kind === 'ctx' || body[i]!.kind === 'new') last = i;
  return last >= 0 && last + 1 < body.length && body[last + 1]!.kind === 'note';
}

/** N as written: context lines are the file's own matched lines, other lines re-indented at tier 2. */
function renderN(items: NItem[], file: string[], start: number, tier: number, old: string[]): string[] {
  const F = start < file.length ? leadST(file[start]!) : '';
  const P = old.length > 0 ? leadST(old[0]!) : '';
  const out: string[] = [];
  for (const it of items) {
    if (it.ctx) {
      out.push(file[start + it.oi]!);
      continue;
    }
    let s = it.text;
    if (tier === 2 && s !== '' && trimST(s) !== '' && s.startsWith(P)) s = F + s.slice(P.length);
    out.push(s);
  }
  return out;
}

/* ---------------------------------------------------------------- unified diffs (section 6) */

interface Hunk {
  a: number;
  body: BodyLine[];
}

interface Sec {
  hasDiff: boolean;
  diffA: string | null;
  diffB: string | null;
  hasOld: boolean;
  pOld: string | null;
  hasNew: boolean;
  pNew: string | null;
  hasROld: boolean;
  rOld: string | null;
  hasRNew: boolean;
  rNew: string | null;
  create: boolean;
  del: boolean;
  hunks: Hunk[];
}

function newSec(): Sec {
  return {
    hasDiff: false,
    diffA: null,
    diffB: null,
    hasOld: false,
    pOld: null,
    hasNew: false,
    pNew: null,
    hasROld: false,
    rOld: null,
    hasRNew: false,
    rNew: null,
    create: false,
    del: false,
    hunks: [],
  };
}

const DIFF_RE = /^diff --git a\/(.+) b\/(.+)$/;
const HUNK_RE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/** A `--- `/`+++ ` path: after the four characters, up to a tab, spaces removed, a/ or b/ dropped. */
function pathOf(s: string): string | null {
  const p = s.split('\t')[0]!.trim();
  if (p === '/dev/null') return null;
  return p.replace(/^[ab]\//, '');
}

function isBodyLine(l: string): boolean {
  if (l === '') return true;
  const c = l[0]!;
  return c === ' ' || c === '-' || c === '+' || c === '\\';
}

function bodyOf(raw: string): BodyLine {
  if (raw === '') return { kind: 'ctx', text: '', raw };
  const c = raw[0]!;
  if (c === ' ') return { kind: 'ctx', text: raw.slice(1), raw };
  if (c === '-') return { kind: 'old', text: raw.slice(1), raw };
  if (c === '+') return { kind: 'new', text: raw.slice(1), raw };
  return { kind: 'note', text: raw, raw };
}

function parseUnified(lines: string[]): Sec[] {
  const secs: Sec[] = [];
  let cur: Sec | null = null;
  let phase: 'header' | 'body' = 'header';
  let hunk: Hunk | null = null;
  let inBody = false;
  const closeBody = (): void => {
    if (hunk !== null) {
      while (hunk.body.length > 0 && hunk.body[hunk.body.length - 1]!.raw === '') hunk.body.pop();
    }
    hunk = null;
    inBody = false;
  };
  let i = 0;
  while (i < lines.length) {
    const L = lines[i]!;
    const NX = lines[i + 1] ?? '';
    const isHunkHeader = L.startsWith('@@');
    if (L.startsWith('diff --git ')) {
      const m = DIFF_RE.exec(L);
      if (m === null) throw new Exit2(`error: parse ${i + 1}`);
      closeBody();
      cur = newSec();
      cur.hasDiff = true;
      cur.diffA = m[1]!;
      cur.diffB = m[2]!;
      secs.push(cur);
      phase = 'header';
      i += 1;
      continue;
    }
    if (L.startsWith('--- ') && NX.startsWith('+++ ') && !(cur !== null && phase === 'header')) {
      closeBody();
      cur = newSec();
      secs.push(cur);
      phase = 'header';
    }
    if (cur === null) {
      if (isHunkHeader) throw new Exit2(`error: parse ${i + 1}`);
      i += 1;
      continue;
    }
    if (phase === 'header') {
      if (isHunkHeader) {
        const m = HUNK_RE.exec(L);
        if (m === null) throw new Exit2(`error: parse ${i + 1}`);
        closeBody();
        hunk = { a: Number(m[1]), body: [] };
        cur.hunks.push(hunk);
        phase = 'body';
        inBody = true;
        i += 1;
        continue;
      }
      if (L.startsWith('--- ')) {
        if (!NX.startsWith('+++ ')) throw new Exit2(`error: parse ${i + 2}`);
        cur.hasOld = true;
        cur.pOld = pathOf(L.slice(4));
        cur.hasNew = true;
        cur.pNew = pathOf(NX.slice(4));
        i += 2;
        continue;
      }
      if (L.startsWith('+++ ')) {
        cur.hasNew = true;
        cur.pNew = pathOf(L.slice(4));
        i += 1;
        continue;
      }
      if (L.startsWith('rename from ')) {
        cur.hasROld = true;
        cur.rOld = L.slice(12).trim();
        i += 1;
        continue;
      }
      if (L.startsWith('rename to ')) {
        cur.hasRNew = true;
        cur.rNew = L.slice(10).trim();
        i += 1;
        continue;
      }
      if (L.startsWith('new file mode')) {
        cur.create = true;
        i += 1;
        continue;
      }
      if (L.startsWith('deleted file mode')) {
        cur.del = true;
        i += 1;
        continue;
      }
      i += 1;
      continue;
    }
    if (isHunkHeader) {
      const m = HUNK_RE.exec(L);
      if (m === null) throw new Exit2(`error: parse ${i + 1}`);
      closeBody();
      hunk = { a: Number(m[1]), body: [] };
      cur.hunks.push(hunk);
      inBody = true;
      i += 1;
      continue;
    }
    if (inBody && isBodyLine(L)) {
      hunk!.body.push(bodyOf(L));
      i += 1;
      continue;
    }
    if (inBody) closeBody();
    i += 1;
  }
  closeBody();
  return secs;
}

function applyHunks(ft: FT, hunks: Hunk[], strict: boolean, path: string): FT {
  let lines = ft.lines.slice();
  let final = ft.final;
  let d = 0;
  const maxTier = strict ? 0 : 2;
  for (const h of hunks) {
    const { O, items } = bodyParts(h.body);
    const noNL = noNewlineFlag(h.body);
    const oldLen = lines.length;
    if (O.length === 0) {
      const pos = Math.min(Math.max(h.a + d, 0), oldLen);
      const N = renderN(items, lines, pos, 0, O);
      lines = lines.slice(0, pos).concat(N, lines.slice(pos));
      d += N.length;
      if (pos === oldLen) final = !noNL;
      continue;
    }
    const target = h.a - 1 + d;
    let tier = -1;
    let pos = -1;
    for (let t = 0; t <= maxTier; t += 1) {
      const ps = occurrences(lines, O, t);
      if (ps.length > 0) {
        tier = t;
        pos = nearest(ps, target);
        break;
      }
    }
    if (tier < 0) throw new Fail('no-match', path);
    const N = renderN(items, lines, pos, tier, O);
    const atEnd = pos + O.length === oldLen;
    lines = lines.slice(0, pos).concat(N, lines.slice(pos + O.length));
    d += N.length - O.length;
    if (atEnd) final = !noNL;
  }
  return { lines, final, br: ft.br };
}

/** A creation's content: the `+` lines of every hunk, in order. */
function createdFT(hunks: Hunk[]): FT {
  const lines: string[] = [];
  let noNL = false;
  for (const h of hunks) {
    for (let i = 0; i < h.body.length; i += 1) {
      const b = h.body[i]!;
      if (b.kind === 'new') {
        lines.push(b.text);
        noNL = i + 1 < h.body.length && h.body[i + 1]!.kind === 'note';
      }
    }
  }
  return { lines, final: !noNL, br: '\n' };
}

function sectionEdit(s: Sec, strict: boolean): Edit {
  return (tree, ctx) => {
    const oldPath = s.hasOld ? s.pOld : s.hasROld ? s.rOld : s.hasDiff ? s.diffA : null;
    const newPath = s.hasNew ? s.pNew : s.hasRNew ? s.rNew : s.hasDiff ? s.diffB : null;
    const isCreate = s.create || oldPath === null;
    const isDelete = s.del || newPath === null;
    // Section 10: every path the edit names is checked for safety before anything else about it, in the
    // order the patch names them. A creation or a deletion still needs the one path it works on.
    const essential = isCreate ? newPath : oldPath;
    if (essential === null) throw new Fail('unsafe', '');
    for (const p of [oldPath, newPath]) if (p !== null && !isSafePath(p)) throw new Fail('unsafe', p);
    if (isCreate) {
      const p = newPath!;
      if (tree.exists(p)) throw new Fail('exists', p);
      tree.set(p, createdFT(s.hunks));
      return;
    }
    if (isDelete) {
      const p = oldPath!;
      if (tree.get(p) === null) throw new Fail('missing', p);
      tree.set(p, null);
      return;
    }
    if (oldPath !== newPath) {
      const from = oldPath!;
      const to = newPath!;
      const before = tree.get(from);
      if (before === null) throw new Fail('missing', from);
      if (tree.exists(to)) throw new Fail('exists', to);
      const out = applyHunks(before, s.hunks, strict, from);
      tree.set(to, out);
      tree.set(from, null);
      ctx.moves.push({ from, to });
      return;
    }
    const p = oldPath!;
    const before = tree.get(p);
    if (before === null) throw new Fail('missing', p);
    tree.set(p, applyHunks(before, s.hunks, strict, p));
  };
}

function unifiedEdits(lines: string[], strict: boolean): Edit[] {
  return parseUnified(lines).map((s) => sectionEdit(s, strict));
}

/* ---------------------------------------------------------------- SEARCH/REPLACE blocks (section 7) */

const SR_SEARCH = '<<<<<<< SEARCH';
const SR_EQ = '=======';
const SR_END = '>>>>>>> REPLACE';

interface Block {
  path: string;
  old: string[];
  nw: string[];
}

function searchReplaceEdits(lines: string[], strict: boolean): Edit[] {
  const blocks: Block[] = [];
  let prevEnd = 0;
  let prevPath: string | null = null;
  let i = 0;
  while (i < lines.length) {
    if (trimEndWS(lines[i]!) !== SR_SEARCH) {
      i += 1;
      continue;
    }
    const searchLine = i;
    let eq = -1;
    for (let j = i + 1; j < lines.length; j += 1) {
      if (trimEndWS(lines[j]!) === SR_EQ) {
        eq = j;
        break;
      }
    }
    let end = -1;
    if (eq >= 0) {
      for (let j = eq + 1; j < lines.length; j += 1) {
        if (trimEndWS(lines[j]!) === SR_END) {
          end = j;
          break;
        }
      }
    }
    if (eq < 0 || end < 0) throw new Exit2(`error: parse ${searchLine + 1}`);
    let path: string | null = null;
    for (let j = searchLine - 1; j >= prevEnd; j -= 1) {
      const t = lines[j]!;
      if (blank(t)) continue;
      if (t.trim().startsWith('```')) continue;
      path = t.trim();
      break;
    }
    if (path === null) path = prevPath;
    if (path === null) throw new Exit2(`error: parse ${searchLine + 1}`);
    blocks.push({ path, old: lines.slice(searchLine + 1, eq), nw: lines.slice(eq + 1, end) });
    prevPath = path;
    prevEnd = end + 1;
    i = end + 1;
  }
  return blocks.map((b) => (tree) => {
    const p = b.path;
    if (!isSafePath(p)) throw new Fail('unsafe', p);
    if (b.old.length === 0) {
      if (tree.exists(p)) throw new Fail('exists', p);
      tree.set(p, ftOf(b.nw.length === 0 ? '' : `${b.nw.join('\n')}\n`));
      return;
    }
    const before = tree.get(p);
    if (before === null) throw new Fail('missing', p);
    const ft = before;
    const found = findTier(ft.lines, b.old, strict);
    if (found === null) throw new Fail('no-match', p);
    if (found.pos.length > 1) throw new Fail('ambiguous', p);
    const pos = found.pos[0]!;
    const items: NItem[] = b.nw.map((t) => ({ ctx: false, oi: -1, text: t }));
    const N = renderN(items, ft.lines, pos, found.tier, b.old);
    const out = ft.lines.slice(0, pos).concat(N, ft.lines.slice(pos + b.old.length));
    tree.set(p, { lines: out, final: ft.final, br: ft.br });
  });
}

/* ---------------------------------------------------------------- V4A patches (section 8) */

interface VChunk {
  anchor: string | null;
  eof: boolean;
  body: BodyLine[];
}

interface VSec {
  kind: 'add' | 'delete' | 'update';
  path: string;
  line: number;
  body: { raw: string; no: number }[];
  moveTo: string | null;
  chunks: VChunk[];
}

function v4aBodyLine(l: string): boolean {
  if (l === '') return true;
  const c = l[0]!;
  return c === ' ' || c === '-' || c === '+';
}

function v4aEdits(lines: string[], strict: boolean): Edit[] {
  const nb = lines.map((l, i) => ({ l, i })).filter((x) => !blank(x.l));
  if (nb.length === 0) throw new Exit2('error: parse 1');
  const first = nb[0]!;
  if (first.l.trim() !== '*** Begin Patch') throw new Exit2(`error: parse ${first.i + 1}`);
  const last = nb[nb.length - 1]!;
  if (last.l.trim() !== '*** End Patch') throw new Exit2(`error: parse ${lines.length + 1}`);
  const secs: VSec[] = [];
  let cur: VSec | null = null;
  for (let i = first.i + 1; i < last.i; i += 1) {
    const L = lines[i]!;
    if (blank(L) && cur === null) continue;
    if (L.startsWith('*** Add File: ')) {
      cur = { kind: 'add', path: L.slice(14).trim(), line: i + 1, body: [], moveTo: null, chunks: [] };
      secs.push(cur);
      continue;
    }
    if (L.startsWith('*** Delete File: ')) {
      cur = { kind: 'delete', path: L.slice(17).trim(), line: i + 1, body: [], moveTo: null, chunks: [] };
      secs.push(cur);
      continue;
    }
    if (L.startsWith('*** Update File: ')) {
      cur = { kind: 'update', path: L.slice(17).trim(), line: i + 1, body: [], moveTo: null, chunks: [] };
      secs.push(cur);
      continue;
    }
    if (cur === null) throw new Exit2(`error: parse ${i + 1}`);
    cur.body.push({ raw: L, no: i + 1 });
  }
  for (const s of secs) while (s.body.length > 0 && blank(s.body[s.body.length - 1]!.raw)) s.body.pop();
  const edits: Edit[] = [];
  for (const s of secs) {
    if (s.kind === 'add') {
      const content: string[] = [];
      for (const b of s.body) {
        if (b.raw === '') content.push('');
        else if (b.raw.startsWith('+')) content.push(b.raw.slice(1));
        else throw new Exit2(`error: parse ${b.no}`);
      }
      edits.push((tree) => {
        if (!isSafePath(s.path)) throw new Fail('unsafe', s.path);
        if (tree.exists(s.path)) throw new Fail('exists', s.path);
        tree.set(s.path, ftOf(content.length === 0 ? '' : `${content.join('\n')}\n`));
      });
      continue;
    }
    if (s.kind === 'delete') {
      for (const b of s.body) throw new Exit2(`error: parse ${b.no}`);
      edits.push((tree) => {
        if (!isSafePath(s.path)) throw new Fail('unsafe', s.path);
        if (tree.get(s.path) === null) throw new Fail('missing', s.path);
        tree.set(s.path, null);
      });
      continue;
    }
    // update
    let k = 0;
    if (s.body.length > 0 && s.body[0]!.raw.startsWith('*** Move to: ')) {
      s.moveTo = s.body[0]!.raw.slice(13).trim();
      k = 1;
    }
    let chunk: VChunk | null = null;
    for (; k < s.body.length; k += 1) {
      const b = s.body[k]!;
      if (b.raw.startsWith('@@')) {
        chunk = { anchor: b.raw.slice(2).trim() === '' ? null : b.raw.slice(2).trim(), eof: false, body: [] };
        s.chunks.push(chunk);
        continue;
      }
      if (b.raw.trim() === '*** End of File') {
        if (chunk === null) throw new Exit2(`error: parse ${b.no}`);
        chunk.eof = true;
        chunk = null;
        continue;
      }
      if (v4aBodyLine(b.raw)) {
        if (chunk === null) {
          if (s.chunks.length > 0) throw new Exit2(`error: parse ${b.no}`);
          chunk = { anchor: null, eof: false, body: [] };
          s.chunks.push(chunk);
        }
        chunk.body.push(bodyOf(b.raw));
        continue;
      }
      throw new Exit2(`error: parse ${b.no}`);
    }
    if (s.chunks.length === 0 && s.moveTo === null) throw new Exit2(`error: parse ${s.line}`);
    const moveTo = s.moveTo;
    const path = s.path;
    const chunks = s.chunks;
    edits.push((tree, ctx) => {
      if (!isSafePath(path)) throw new Fail('unsafe', path);
      if (moveTo !== null && !isSafePath(moveTo)) throw new Fail('unsafe', moveTo);
      const before = tree.get(path);
      if (before === null) throw new Fail('missing', path);
      if (moveTo !== null && tree.exists(moveTo)) throw new Fail('exists', moveTo);
      const ft = before;
      let lines = ft.lines.slice();
      let cursor = 0;
      const maxTier = strict ? 0 : 2;
      for (const ch of chunks) {
        if (ch.anchor !== null) {
          let at = -1;
          for (let t = 0; t <= maxTier && at < 0; t += 1) {
            for (let j = cursor; j < lines.length; j += 1) {
              if (lineEq(lines[j]!, ch.anchor, t)) {
                at = j;
                break;
              }
            }
          }
          if (at < 0) throw new Fail('no-match', path);
          cursor = at + 1;
        }
        const { O, items } = bodyParts(ch.body);
        if (O.length === 0) {
          const pos = ch.eof ? lines.length : cursor;
          const N = renderN(items, lines, pos, 0, O);
          lines = lines.slice(0, pos).concat(N, lines.slice(pos));
          cursor = pos + N.length;
          continue;
        }
        let tier = -1;
        let pos = -1;
        for (let t = 0; t <= maxTier && pos < 0; t += 1) {
          for (let j = cursor; j + O.length <= lines.length; j += 1) {
            if (ch.eof && j + O.length !== lines.length) continue;
            let ok = true;
            for (let q = 0; q < O.length; q += 1) {
              if (!lineEq(lines[j + q]!, O[q]!, t)) {
                ok = false;
                break;
              }
            }
            if (ok) {
              pos = j;
              tier = t;
              break;
            }
          }
        }
        if (pos < 0) throw new Fail('no-match', path);
        const N = renderN(items, lines, pos, tier, O);
        lines = lines.slice(0, pos).concat(N, lines.slice(pos + O.length));
        cursor = pos + N.length;
      }
      const out: FT = { lines, final: ft.final, br: ft.br };
      if (moveTo === null) tree.set(path, out);
      else {
        tree.set(moveTo, out);
        tree.set(path, null);
        ctx.moves.push({ from: path, to: moveTo });
      }
    });
  }
  return edits;
}

/* ---------------------------------------------------------------- JSON edits (section 9) */

function jsonEdit(v: unknown): Edit | null {
  if (v === null || typeof v !== 'object' || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const p = o.path;
  if (typeof p !== 'string') return null;
  const exists = (tree: Tree): FT => {
    if (!isSafePath(p)) throw new Fail('unsafe', p);
    const t = tree.get(p);
    if (t === null) throw new Fail('missing', p);
    return t;
  };
  if (o.command === 'create' && typeof o.file_text === 'string') {
    const text = o.file_text;
    return (tree) => {
      if (!isSafePath(p)) throw new Fail('unsafe', p);
      if (tree.exists(p)) throw new Fail('exists', p);
      tree.set(p, ftOf(text));
    };
  }
  if (o.command === 'str_replace' && typeof o.old_str === 'string' && o.old_str !== '' && (o.new_str === undefined || typeof o.new_str === 'string')) {
    const oldStr = o.old_str;
    const newStr = (o.new_str as string | undefined) ?? '';
    return (tree) => {
      const before = exists(tree);
      const ft = before;
      const crlf = ft.br === '\r\n';
      const fold = (s: string): string => (crlf ? s.replaceAll('\r\n', '\n') : s);
      const t = fold(ftText(before));
      const old = fold(oldStr);
      let count = 0;
      for (let k = t.indexOf(old); k >= 0; k = t.indexOf(old, k + 1)) count += 1;
      if (count === 0) throw new Fail('no-match', p);
      if (count > 1) throw new Fail('ambiguous', p);
      const at = t.indexOf(old);
      const out = t.slice(0, at) + fold(newStr) + t.slice(at + old.length);
      tree.set(p, ftOf(crlf ? out.replaceAll('\n', '\r\n') : out));
    };
  }
  if (o.command === 'insert' && typeof o.insert_line === 'number' && Number.isInteger(o.insert_line) && typeof o.new_str === 'string') {
    const line = o.insert_line;
    const add = patchLines(o.new_str);
    return (tree) => {
      const before = exists(tree);
      const ft = before;
      const lines = ft.lines.slice();
      if (line < 0 || line > lines.length) throw new Fail('no-match', p);
      lines.splice(line, 0, ...add);
      tree.set(p, { lines, final: ft.final, br: ft.br });
    };
  }
  return null;
}

function jsonEdits(text: string, lines: string[]): Edit[] {
  const first = lines.findIndex((l) => !blank(l));
  if (first < 0) throw new Exit2('error: parse 1');
  const edits: Edit[] = [];
  if (lines[first]!.trimStart().startsWith('[')) {
    let v: unknown;
    try {
      v = JSON.parse(text);
    } catch {
      throw new Exit2(`error: parse ${first + 1}`);
    }
    if (!Array.isArray(v)) throw new Exit2(`error: parse ${first + 1}`);
    for (const x of v) {
      const e = jsonEdit(x);
      if (e === null) throw new Exit2(`error: parse ${first + 1}`);
      edits.push(e);
    }
    return edits;
  }
  lines.forEach((l, i) => {
    if (blank(l)) return;
    let e: Edit | null = null;
    try {
      e = jsonEdit(JSON.parse(l));
    } catch {
      e = null;
    }
    if (e === null) throw new Exit2(`error: parse ${i + 1}`);
    edits.push(e);
  });
  return edits;
}

/* ---------------------------------------------------------------- detecting the format (section 4) */

function detect(lines: string[]): string | null {
  const first = lines.findIndex((l) => !blank(l));
  if (first < 0) return null;
  if (lines[first]!.trim() === '*** Begin Patch') return 'v4a';
  const c = lines[first]!.trimStart()[0];
  if (c === '[' || c === '{') return 'json';
  if (lines.some((l) => l.trimEnd() === SR_SEARCH)) return 'search-replace';
  if (lines.some((l, i) => l.startsWith('diff --git ') || (l.startsWith('--- ') && (lines[i + 1] ?? '').startsWith('+++ ')))) return 'unified';
  return null;
}

/* ---------------------------------------------------------------- the command line (sections 1, 2, 10) */

function run(args: string[]): { code: number; out: string[] } {
  let format: string | null = null;
  let check = false;
  let strict = false;
  const pos: string[] = [];
  let ended = false;
  for (const a of args) {
    if (!ended && a === '--') ended = true;
    else if (!ended && a.startsWith('-') && a !== '-') {
      if (a.startsWith('--format=') && format === null && FORMATS.includes(a.slice(9))) format = a.slice(9);
      else if (a === '--check' && !check) check = true;
      else if (a === '--strict' && !strict) strict = true;
      else return { code: 2, out: ['error: usage'] };
    } else pos.push(a);
  }
  if (pos.length > 1) return { code: 2, out: ['error: usage'] };
  const file = pos.length === 0 || pos[0] === '-' ? null : pos[0]!;
  let text = '';
  try {
    text = readFileSync(file ?? 0, 'utf8');
  } catch {
    if (file !== null) return { code: 2, out: ['error: no-patch'] };
  }
  const lines = patchLines(text);
  const kind = format === null || format === 'auto' ? detect(lines) : format;
  if (kind === null) return { code: 2, out: ['error: format'] };
  let edits: Edit[];
  try {
    if (kind === 'json') edits = jsonEdits(text, lines);
    else if (kind === 'unified') edits = unifiedEdits(lines, strict);
    else if (kind === 'search-replace') edits = searchReplaceEdits(lines, strict);
    else edits = v4aEdits(lines, strict);
    if (edits.length === 0) throw new Exit2('error: parse 1');
  } catch (e) {
    if (e instanceof Exit2) return { code: 2, out: [e.line] };
    throw e;
  }
  const tree = new Tree();
  const ctx: Ctx = { moves: [] };
  const fails: Fail[] = [];
  for (const edit of edits) {
    const movesBefore = ctx.moves.length;
    tree.begin();
    try {
      edit(tree, ctx);
      tree.commit();
    } catch (e) {
      tree.rollback();
      ctx.moves.length = movesBefore;
      if (!(e instanceof Fail)) throw e;
      fails.push(e);
    }
  }
  if (fails.length > 0) return { code: 1, out: [...fails.map((f) => `error: ${f.code} ${f.path}`), `failed: ${fails.length} of ${edits.length} edits`] };
  const changed = new Map<string, 'A' | 'D' | 'M'>();
  for (const p of tree.now.keys()) {
    const was = tree.original(p);
    const now = tree.now.get(p)!;
    if (was === null ? now === null : now !== null && ftText(was) === ftText(now)) continue;
    changed.set(p, was === null ? 'A' : now === null ? 'D' : 'M');
  }
  const rows: { key: string; text: string }[] = [];
  const taken = new Set<string>();
  for (const m of ctx.moves) {
    if (taken.has(m.from) || taken.has(m.to)) continue;
    if (changed.get(m.from) !== 'D' || changed.get(m.to) !== 'A') continue;
    if (tree.original(m.from) === null || tree.original(m.to) !== null) continue;
    if (tree.now.get(m.from) !== null || tree.now.get(m.to) === null) continue;
    rows.push({ key: m.to, text: `R ${m.from} -> ${m.to}` });
    taken.add(m.from);
    taken.add(m.to);
  }
  for (const [p, st] of changed) {
    if (taken.has(p)) continue;
    rows.push({ key: p, text: `${st} ${p}` });
  }
  rows.sort((a, b) => Buffer.compare(Buffer.from(a.key, 'utf8'), Buffer.from(b.key, 'utf8')));
  if (!check) {
    let at = '';
    try {
      for (const [p, st] of changed) {
        at = p;
        if (st === 'D') rmSync(p, { force: true });
        else {
          mkdirSync(dirname(p), { recursive: true });
          writeFileSync(p, ftText(tree.now.get(p)!));
        }
      }
    } catch (e) {
      // The file system refused a path isSafePath let through (a parent that is a file, a read-only tree):
      // that path cannot name a file either, so the answer is still one SPEC.md shape, never a throw.
      note(`mend: write ${at}: ${e instanceof Error ? e.message : String(e)}\n`);
      return { code: 1, out: [`error: unsafe ${at}`, `failed: 1 of ${edits.length} edits`] };
    }
  }
  return { code: 0, out: [...rows.map((r) => r.text), `ok: ${rows.length} ${rows.length === 1 ? 'file' : 'files'}`] };
}

const trialEnv = process.env.MAKEFUN_ENV_URL;
if (trialEnv !== undefined && trialEnv !== '') {
  // A trial: the kit's runner reads the checks from the env and runs this file on each one, without this variable.
  const { runTrial } = await import('./runner.ts');
  await runTrial(trialEnv);
} else {
  let r: { code: number; out: string[] };
  try {
    r = run(process.argv.slice(2));
  } catch (e) {
    // Nothing reaches the process as an uncaught throw: the contract is one spec-shaped answer and an exit code
    // in 0, 1 or 2. The real fault goes to standard error, which no check compares, so a bug is still visible.
    note(`mend: internal: ${e instanceof Error ? (e.stack ?? e.message) : String(e)}\n`);
    r = { code: 2, out: ['error: parse 1'] };
  }
  try {
    writeAll(r.out.map((l) => `${l}\n`).join(''));
  } catch {
    // Standard output is gone (a closed pipe): there is nowhere left to answer.
  }
  process.exitCode = r.code;
}
