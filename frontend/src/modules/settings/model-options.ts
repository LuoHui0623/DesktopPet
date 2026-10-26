import type { ActiveModelInfo, ProviderInfo } from "../../api/types";
import type { ModelCapability } from "../../service/model.service";

export const CAPABILITY_LABELS: Record<ModelCapability, string> = {
  chat: "对话模型",
  consolidate: "整理模型",
};

export interface ModelOption {
  value: string;
  provider: string;
  model: string;
  label: string;
  available: boolean;
}

export function optionValue(provider: string, model: string): string {
  return JSON.stringify([provider, model]);
}

export function modelOptions(
  providers: ProviderInfo[],
  capability: ModelCapability,
): ModelOption[] {
  return providers.flatMap((provider) =>
    provider.models
      .filter((model) => model.roles.includes(capability))
      .map((model) => ({
        value: optionValue(provider.name, model.name),
        provider: provider.name,
        model: model.name,
        label: `${provider.display_name} · ${model.name}`,
        available: provider.available,
      })),
  );
}

export function selectedValue(active: ActiveModelInfo | null): string {
  return active ? optionValue(active.provider, active.model) : "";
}
