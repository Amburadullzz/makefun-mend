// tests/probe.ts — exploratory probe: run main.ts on each case in a JSON file and print what it did.
// usage: node tests/probe.ts <cases.json>
import { readFileSync } from 'node:fs';
import { runCheck } from '../runner.ts';

interface Case {
  key: string;
  why?: string;
  argv: string[];
  stdin?: string | null;
  files: Record<string, string>;
}

const [file] = process.argv.slice(2);
const cases = JSON.parse(readFileSync(file!, 'utf8')) as Case[];
for (const c of cases) {
  const got = runCheck({ id: c.key, argv: c.argv, stdin: c.stdin ?? null, files: c.files });
  console.log(`=== ${c.key}${c.why ? `  (${c.why})` : ''}`);
  console.log(`  exit ${got.exit}`);
  console.log(`  stdout ${JSON.stringify(got.stdout)}`);
  const before = c.files;
  const after = got.files;
  for (const p of new Set([...Object.keys(before), ...Object.keys(after)])) {
    if (!(p in after)) console.log(`  -${p} (deleted)`);
    else if (!(p in before)) console.log(`  +${p} = ${JSON.stringify(after[p])}`);
    else if (before[p] !== after[p]) console.log(`  ~${p} = ${JSON.stringify(after[p])}`);
  }
}
