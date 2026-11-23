// tests/diffrun.ts — run main.ts on each case in a JSON file and write the results as JSON.
// usage: node tests/diffrun.ts <cases.json> <results.json>
import { readFileSync, writeFileSync } from 'node:fs';
import { runCheck } from '../runner.ts';

interface Case {
  key: string;
  argv: string[];
  stdin: string | null;
  files: Record<string, string>;
}

const [inFile, outFile] = process.argv.slice(2);
const cases = JSON.parse(readFileSync(inFile!, 'utf8')) as Case[];
const out = cases.map((c) => {
  const r = runCheck({ id: c.key, argv: c.argv, stdin: c.stdin, files: c.files });
  return { key: c.key, exit: r.exit, stdout: r.stdout, files: r.files };
});
writeFileSync(outFile!, JSON.stringify(out));
