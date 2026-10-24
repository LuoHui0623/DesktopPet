import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createInterface } from "node:readline";

const PORT_PREFIX = "PET_PORT=";
const STDERR_TAIL_LINES = 20;

export interface LaunchOptions {
  command: string;
  args: string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
  startupTimeoutMs?: number;
  onLog?: (line: string) => void;
}

export interface BackendProcess {
  port: number;
  token: string;
  child: ChildProcessWithoutNullStreams;
}

export function createToken(): string {
  return randomBytes(32).toString("base64url");
}

export function launchBackend(options: LaunchOptions): Promise<BackendProcess> {
  const { command, args, cwd, env, onLog, startupTimeoutMs = 15000 } = options;
  const token = createToken();

  return new Promise<BackendProcess>((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      env: { ...process.env, ...env, PET_PORT: "0", PET_TOKEN: token },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    child.stdin.on("error", () => undefined);

    const stderrTail: string[] = [];
    let settled = false;

    const fail = (error: Error): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    };

    const timer = setTimeout(() => {
      fail(new Error(`后端启动超时（${startupTimeoutMs} ms）`));
      child.kill();
    }, startupTimeoutMs);

    createInterface({ input: child.stdout }).on("line", (line) => {
      if (!settled && line.startsWith(PORT_PREFIX)) {
        const port = Number(line.slice(PORT_PREFIX.length));
        if (Number.isInteger(port) && port > 0) {
          settled = true;
          clearTimeout(timer);
          resolve({ port, token, child });
          return;
        }
      }
      onLog?.(`[backend] ${line}`);
    });

    createInterface({ input: child.stderr }).on("line", (line) => {
      stderrTail.push(line);
      if (stderrTail.length > STDERR_TAIL_LINES) stderrTail.shift();
      onLog?.(`[backend:err] ${line}`);
    });

    child.on("error", (error) => fail(error));
    child.on("exit", (code, signal) => {
      fail(new Error(`后端在启动前退出（代码 ${code}，信号 ${signal}）\n${stderrTail.join("\n")}`));
    });
  });
}
