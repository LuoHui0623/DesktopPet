import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApiClient, ApiError } from "../../src/api/client";
import { getHealth } from "../../src/api/endpoints";
import { launchBackend, type BackendProcess } from "../../electron/main/backend/launcher";
import { BackendSupervisor } from "../../electron/main/backend/supervisor";

const BACKEND_DIR = fileURLToPath(new URL("../../../backend", import.meta.url));
const PYTHON = path.join(
  BACKEND_DIR,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);

describe.skipIf(!existsSync(PYTHON))("api client against the real backend", () => {
  let backend: BackendProcess;
  let supervisor: BackendSupervisor;

  beforeAll(async () => {
    backend = await launchBackend({
      command: PYTHON,
      args: ["main.py"],
      cwd: BACKEND_DIR,
      env: {
        PET_DATA_DIR: mkdtempSync(path.join(tmpdir(), "pet-api-")),
        PET_LOG_MAX_WAIT_SECONDS: "60",
      },
      startupTimeoutMs: 30000,
    });
    supervisor = new BackendSupervisor(backend.child);
  }, 60000);

  afterAll(async () => {
    if (supervisor) await supervisor.stop(15000);
  });

  it("returns the health status with the launch token", async () => {
    const client = createApiClient({ port: backend.port, token: backend.token });
    expect(await getHealth(client)).toEqual({ status: "ok" });
  });

  it("reports unauthorized with the unified error for a wrong token", async () => {
    const client = createApiClient({ port: backend.port, token: "not-the-token" });
    const error = (await getHealth(client).catch((exc: unknown) => exc)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(401);
    expect(error.code).toBe("unauthorized");
  });
});
