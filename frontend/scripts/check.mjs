import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const steps = [
  { name: "eslint", args: ["node_modules/eslint/bin/eslint.js", "."] },
  { name: "prettier", args: ["node_modules/prettier/bin/prettier.cjs", "--check", "."] },
];

let failed = false;
for (const step of steps) {
  const result = spawnSync(process.execPath, step.args, { cwd: frontendDir, stdio: "inherit" });
  if (result.status !== 0) {
    console.error(`[check] ${step.name} 失败`);
    failed = true;
  }
}
process.exitCode = failed ? 1 : 0;
