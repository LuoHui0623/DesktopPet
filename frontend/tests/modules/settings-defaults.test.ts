import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const FRONTEND_DIR = fileURLToPath(new URL("../..", import.meta.url));

// T-066 验收:「前端不出现硬编码默认值」——配置初值必须来自 GET /api/settings。
const MANAGED_SOURCES = [
  "src/modules/settings/SettingsWindow.tsx",
  "src/modules/settings/PerceptionSettings.tsx",
  "src/modules/settings/ProactiveSettings.tsx",
  "src/modules/settings/AvatarSettings.tsx",
  "src/modules/settings/InteractionSettings.tsx",
  "src/modules/settings/use-settings-bundle.ts",
  "src/modules/settings/use-settings-save.ts",
];

// 唯一允许兜底默认值的地方：悬浮窗在后端响应到达前的短暂占位。
const FALLBACK_SOURCE = "src/service/interaction.service.ts";
const FALLBACK_ONLY = "FALLBACK_INTERACTION_CONFIG";

function read(relative: string): string {
  return readFileSync(path.join(FRONTEND_DIR, relative), "utf-8");
}

function stripComments(source: string): string {
  return source.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
}

describe("settings pages read their defaults from the backend", () => {
  for (const relative of MANAGED_SOURCES) {
    it(`${relative} has no hardcoded config default`, () => {
      const source = stripComments(read(relative));

      expect(source).not.toMatch(/useState\(\s*\d/);
      expect(source).not.toMatch(/\?\?\s*[\d"']/);
      expect(source).not.toMatch(/\|\|\s*[\d"]/);
    });
  }

  it("keeps the transient fallback in one place only", () => {
    const source = read(FALLBACK_SOURCE);

    expect(source).toContain(`export const ${FALLBACK_ONLY}`);
    for (const relative of MANAGED_SOURCES) {
      expect(read(relative)).not.toContain(FALLBACK_ONLY);
    }
  });

  it("serves the four groups through the aggregate endpoint", () => {
    const contracts = JSON.parse(read(path.join("src", "api", "api-contracts.json"))) as {
      operations: { operationId: string; method: string; path: string }[];
    };
    const operationIds = contracts.operations.map((item) => item.operationId);

    for (const id of [
      "get_settings",
      "update_settings_perception",
      "update_settings_proactive",
      "update_settings_avatar",
      "update_settings_interaction",
      "get_interaction_config",
    ]) {
      expect(operationIds).toContain(id);
    }
    expect(read("src/api/endpoints.ts")).toContain('"/api/settings"');
  });
});
