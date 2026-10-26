import { describe, expect, it } from "vitest";
import type { ProviderInfo } from "../../src/api/types";
import { modelOptions, optionValue, selectedValue } from "../../src/modules/settings/model-options";

const providers: ProviderInfo[] = [
  {
    name: "deepseek",
    display_name: "DeepSeek",
    available: true,
    reason: null,
    models: [
      {
        name: "flash",
        id: "deepseek-chat",
        api: "chat_completions",
        roles: ["chat", "consolidate"],
      },
      {
        name: "reasoner",
        id: "deepseek-reasoner",
        api: "chat_completions",
        roles: ["consolidate"],
      },
    ],
  },
  {
    name: "openai",
    display_name: "OpenAI",
    available: false,
    reason: "缺少环境变量 OPENAI_API_KEY",
    models: [{ name: "gpt", id: "gpt-x", api: "responses", roles: ["chat"] }],
  },
];

describe("modelOptions", () => {
  it("lists only models that carry the requested role", () => {
    expect(modelOptions(providers, "chat").map((option) => option.label)).toEqual([
      "DeepSeek · flash",
      "OpenAI · gpt",
    ]);
    expect(modelOptions(providers, "consolidate").map((option) => option.label)).toEqual([
      "DeepSeek · flash",
      "DeepSeek · reasoner",
    ]);
  });

  it("marks options of unavailable providers as unavailable", () => {
    const gpt = modelOptions(providers, "chat").find((option) => option.model === "gpt");
    expect(gpt?.available).toBe(false);
    expect(gpt?.provider).toBe("openai");
  });

  it("never exposes the model api id as the selection", () => {
    const option = modelOptions(providers, "chat")[0];
    expect(option?.model).toBe("flash");
    expect(option?.value).toBe(optionValue("deepseek", "flash"));
  });
});

describe("selectedValue", () => {
  it("is empty when no model is active", () => {
    expect(selectedValue(null)).toBe("");
  });

  it("matches the option value of the active model", () => {
    const active = { provider: "deepseek", model: "flash", source: "database" as const };
    const value = selectedValue(active);
    expect(modelOptions(providers, "chat").some((option) => option.value === value)).toBe(true);
  });
});
