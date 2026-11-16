import { useState } from "react";
import type { ActiveModelInfo, ProviderInfo, SettingsView } from "../../api/types";
import { describeRequestError } from "../../service/api-client";
import {
  reloadModelConfig,
  switchActiveModel,
  type ModelCapability,
} from "../../service/model.service";
import {
  CAPABILITY_LABELS,
  modelOptions,
  optionValue,
  selectedValue,
  type ModelOption,
} from "./model-options";
import { useSettingsSave } from "./use-settings-save";

const CAPABILITIES: ModelCapability[] = ["chat", "consolidate"];
const SOURCE_LABELS: Record<ActiveModelInfo["source"], string> = {
  database: "设置页切换",
  env: ".env 默认",
};

const sectionStyle = { display: "grid", gap: 8, marginBottom: 20 } as const;
const headingStyle = { fontSize: 15, margin: 0 } as const;
const buttonStyle = { padding: "4px 12px" } as const;

export function ModelSettings({
  settings,
  onSaved,
  onReloaded,
}: {
  settings: SettingsView;
  onSaved: (view: SettingsView) => void;
  onReloaded: () => void;
}) {
  const { busy, error: actionError, notice, save } = useSettingsSave(settings.version, onSaved);
  const [reloadError, setReloadError] = useState<string | null>(null);
  const [reloadNotice, setReloadNotice] = useState<string | null>(null);
  const [reloading, setReloading] = useState(false);

  const onSwitch = (capability: ModelCapability, option: ModelOption) => {
    setReloadNotice(null);
    setReloadError(null);
    save(
      (version) =>
        switchActiveModel(capability, { provider: option.provider, model: option.model }, version),
      `${CAPABILITY_LABELS[capability]}已切换为 ${option.label}，下一次对话生效`,
    );
  };

  const onReload = () => {
    setReloading(true);
    setReloadNotice(null);
    setReloadError(null);
    reloadModelConfig()
      .then(
        (result) => {
          setReloadNotice(`配置已重载，版本 ${result.version}`);
          onReloaded();
        },
        (error: unknown) => {
          setReloadError(`${describeRequestError(error)}（仍使用旧配置）`);
        },
      )
      .finally(() => setReloading(false));
  };

  const { providers, active } = settings.model;
  const { warnings, last_reload_errors: reloadErrors } = providers;

  return (
    <div>
      <section style={sectionStyle}>
        <h2 style={headingStyle}>当前模型</h2>
        {CAPABILITIES.map((capability) => {
          const options = modelOptions(providers.providers, capability);
          const current = active[capability];
          const value = selectedValue(current);
          const known = options.some((option) => option.value === value);
          return (
            <label key={capability} style={{ display: "grid", gap: 4, fontSize: 14 }}>
              <span>
                {CAPABILITY_LABELS[capability]}
                {current && (
                  <span style={{ color: "#777", marginLeft: 8 }}>
                    （{SOURCE_LABELS[current.source]}）
                  </span>
                )}
              </span>
              <select
                disabled={busy}
                value={value}
                onChange={(event) => {
                  const option = options.find((item) => item.value === event.target.value);
                  if (option) onSwitch(capability, option);
                }}
              >
                {!value && (
                  <option value="" disabled>
                    未设置
                  </option>
                )}
                {!known && value && (
                  <option value={value} disabled>
                    {current ? `${current.provider} · ${current.model}（不在可选列表）` : ""}
                  </option>
                )}
                {options.map((option) => (
                  <option key={option.value} value={option.value} disabled={!option.available}>
                    {option.label}
                    {option.available ? "" : "（不可用）"}
                  </option>
                ))}
              </select>
            </label>
          );
        })}
      </section>

      <section style={sectionStyle}>
        <h2 style={headingStyle}>模型来源</h2>
        {providers.providers.length === 0 && <p style={{ color: "#777" }}>未配置任何 provider</p>}
        {providers.providers.map((provider) => (
          <ProviderCard key={provider.name} provider={provider} />
        ))}
      </section>

      {warnings.length > 0 && (
        <section style={sectionStyle}>
          <h2 style={headingStyle}>配置提示</h2>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: "#8a5a00" }}>
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      )}

      <section style={sectionStyle}>
        <h2 style={headingStyle}>配置重载</h2>
        <div>
          <button type="button" onClick={onReload} disabled={reloading} style={buttonStyle}>
            重新加载配置
          </button>
        </div>
        {reloadErrors.length > 0 && (
          <div role="alert" style={{ color: "#991b1b", fontSize: 13 }}>
            <p style={{ margin: "4px 0" }}>最近一次重载失败，仍使用旧配置：</p>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {reloadErrors.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          </div>
        )}
        {reloadError && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13, margin: 0 }}>
            {reloadError}
          </p>
        )}
        {actionError && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13, margin: 0 }}>
            {actionError}
          </p>
        )}
        {notice && <p style={{ color: "#166534", fontSize: 13, margin: 0 }}>{notice}</p>}
        {reloadNotice && (
          <p style={{ color: "#166534", fontSize: 13, margin: 0 }}>{reloadNotice}</p>
        )}
      </section>
    </div>
  );
}

function ProviderCard({ provider }: { provider: ProviderInfo }) {
  return (
    <article style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 10, fontSize: 13 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <strong>
          {provider.display_name}
          <span style={{ color: "#777", fontWeight: 400, marginLeft: 6 }}>{provider.name}</span>
        </strong>
        <span style={{ color: provider.available ? "#166534" : "#991b1b" }}>
          {provider.available ? "可用" : "不可用"}
        </span>
      </div>
      {!provider.available && provider.reason && (
        <p style={{ color: "#991b1b", margin: "4px 0" }}>原因：{provider.reason}</p>
      )}
      <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
        {provider.models.map((model) => (
          <li key={optionValue(provider.name, model.name)}>
            {model.name}
            <span style={{ color: "#777" }}>
              {" "}
              · {model.id} · {model.api} · {model.roles.join("/")}
            </span>
          </li>
        ))}
      </ul>
    </article>
  );
}
