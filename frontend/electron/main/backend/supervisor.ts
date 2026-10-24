import type { ChildProcessWithoutNullStreams } from "node:child_process";

export const SHUTDOWN_COMMAND = "shutdown\n";
export const DEFAULT_STOP_TIMEOUT_MS = 8000;

export interface ExitInfo {
  code: number | null;
  signal: NodeJS.Signals | null;
}

export class BackendSupervisor {
  private exitInfo: ExitInfo | null = null;
  private stopping = false;
  private readonly exited: Promise<ExitInfo>;

  constructor(
    private readonly child: ChildProcessWithoutNullStreams,
    private readonly onUnexpectedExit?: (info: ExitInfo) => void,
  ) {
    this.exited = new Promise<ExitInfo>((resolve) => {
      const record = (code: number | null, signal: NodeJS.Signals | null): void => {
        if (this.exitInfo) return;
        this.exitInfo = { code, signal };
        resolve(this.exitInfo);
        if (!this.stopping) this.onUnexpectedExit?.(this.exitInfo);
      };
      if (child.exitCode !== null || child.signalCode !== null) {
        record(child.exitCode, child.signalCode);
      } else {
        child.once("exit", record);
      }
    });
  }

  get running(): boolean {
    return this.exitInfo === null;
  }

  async stop(timeoutMs = DEFAULT_STOP_TIMEOUT_MS): Promise<ExitInfo> {
    if (this.exitInfo) return this.exitInfo;
    this.stopping = true;
    if (this.child.stdin.writable) {
      this.child.stdin.write(SHUTDOWN_COMMAND);
      this.child.stdin.end();
    }
    const timer = setTimeout(() => {
      if (this.running) this.child.kill();
    }, timeoutMs);
    try {
      return await this.exited;
    } finally {
      clearTimeout(timer);
    }
  }
}
