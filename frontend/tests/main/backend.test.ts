import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { launchBackend, type BackendProcess } from "../../electron/main/backend/launcher";
import { BackendSupervisor } from "../../electron/main/backend/supervisor";

const BACKEND_DIR = fileURLToPath(new URL("../../../backend", import.meta.url));
const REAL_PYTHON = path.join(
  BACKEND_DIR,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);

const created: BackendProcess[] = [];
const workDirs: string[] = [];

afterEach(() => {
  for (const backend of created.splice(0)) {
    if (backend.child.exitCode === null) backend.child.kill();
  }
});

function workDir(): string {
  const dir = mkdtempSync(path.join(tmpdir(), "pet-launcher-"));
  workDirs.push(dir);
  return dir;
}

function fakeBackend(source: string): string {
  const file = path.join(workDir(), "fake-backend.js");
  writeFileSync(file, source, "utf-8");
  return file;
}

const WELL_BEHAVED = `
  process.stdout.write("PET_PORT=4321\\n");
  process.stdin.on("data", (chunk) => {
    if (String(chunk).includes("shutdown")) process.exit(0);
  });
  process.stdin.on("end", () => process.exit(0));
`;

async function launchFake(script: string, startupTimeoutMs = 5000): Promise<BackendProcess> {
  const backend = await launchBackend({
    command: process.execPath,
    args: [fakeBackend(script)],
    cwd: workDir(),
    startupTimeoutMs,
  });
  created.push(backend);
  return backend;
}

describe("launchBackend", () => {
  it("reads the port from the first PET_PORT line and generates a token", async () => {
    const backend = await launchFake(WELL_BEHAVED);
    expect(backend.port).toBe(4321);
    expect(backend.token.length).toBeGreaterThanOrEqual(32);
    expect(backend.token).not.toContain("=");
  });

  it("generates a different token for every launch", async () => {
    const first = await launchFake(WELL_BEHAVED);
    const second = await launchFake(WELL_BEHAVED);
    expect(first.token).not.toBe(second.token);
  });

  it("rejects when the process exits before printing the port", async () => {
    const script = `process.stderr.write("boom from backend\\n"); process.exit(3);`;
    await expect(launchFake(script)).rejects.toThrow(/代码 3[\s\S]*boom from backend/);
  });

  it("rejects when the port is not printed in time", async () => {
    const script = `setInterval(() => {}, 1000);`;
    await expect(launchFake(script, 300)).rejects.toThrow(/启动超时/);
  });

  it("rejects when the interpreter cannot be started", async () => {
    await expect(
      launchBackend({
        command: path.join(workDir(), "no-such-python"),
        args: [],
        cwd: workDir(),
      }),
    ).rejects.toThrow();
  });
});

describe("BackendSupervisor", () => {
  it("stops a cooperative backend through the stdin command", async () => {
    const backend = await launchFake(WELL_BEHAVED);
    const supervisor = new BackendSupervisor(backend.child);
    const exit = await supervisor.stop(5000);
    expect(exit.code).toBe(0);
    expect(supervisor.running).toBe(false);
  });

  it("force-kills a backend that ignores the stop command", async () => {
    const script = `
      process.stdout.write("PET_PORT=4322\\n");
      setInterval(() => {}, 1000);
    `;
    const backend = await launchFake(script);
    const supervisor = new BackendSupervisor(backend.child);
    const exit = await supervisor.stop(300);
    expect(exit.code !== 0 || exit.signal !== null).toBe(true);
    expect(supervisor.running).toBe(false);
  });

  it("reports unexpected exits", async () => {
    const script = `process.stdout.write("PET_PORT=4323\\n"); setTimeout(() => process.exit(2), 100);`;
    const backend = await launchFake(script);
    const exits: number[] = [];
    new BackendSupervisor(backend.child, (info) => exits.push(info.code ?? -1));
    await waitFor(() => exits.length === 1);
    expect(exits).toEqual([2]);
  });

  it("does not report exits that were requested by stop", async () => {
    const backend = await launchFake(WELL_BEHAVED);
    const exits: number[] = [];
    const supervisor = new BackendSupervisor(backend.child, (info) => exits.push(info.code ?? -1));
    await supervisor.stop(5000);
    expect(exits).toEqual([]);
  });

  it("stop is idempotent after the process has exited", async () => {
    const backend = await launchFake(WELL_BEHAVED);
    const supervisor = new BackendSupervisor(backend.child);
    const first = await supervisor.stop(5000);
    const second = await supervisor.stop(5000);
    expect(second).toEqual(first);
  });
});

const realBackendAvailable = existsSync(REAL_PYTHON);

describe.skipIf(!realBackendAvailable)("real backend", () => {
  it("starts, serves an authenticated health check, and shuts down gracefully", async () => {
    const dataDir = workDir();
    const backend = await launchBackend({
      command: REAL_PYTHON,
      args: ["main.py"],
      cwd: BACKEND_DIR,
      env: { PET_DATA_DIR: dataDir, PET_LOG_MAX_WAIT_SECONDS: "60" },
      startupTimeoutMs: 30000,
    });
    created.push(backend);
    const base = `http://127.0.0.1:${backend.port}`;

    const unauthorized = await fetch(`${base}/api/health`);
    expect(unauthorized.status).toBe(401);

    const healthy = await fetch(`${base}/api/health`, {
      headers: { "X-Pet-Token": backend.token },
    });
    expect(healthy.status).toBe(200);
    expect(await healthy.json()).toEqual({ status: "ok" });

    const supervisor = new BackendSupervisor(backend.child);
    const exit = await supervisor.stop(15000);
    expect(exit.code).toBe(0);
  }, 60000);
});

async function waitFor(predicate: () => boolean, timeoutMs = 5000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error("waitFor timed out");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}
