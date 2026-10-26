import { useCallback, useEffect, useState } from "react";
import type { ActiveModelInfo, ProviderInfo } from "../../api/types";
import { describeRequestError } from "../../service/api-client";
import {
  loadModelSettings,
  reloadModelConfig,
  switchActiveModel,
  type ModelCapability,
  type ModelSettingsSnapshot,
} from "../../service/model.service";
import {
  CAPABILITY_LABELS,
  modelOptions,
  optionValue,
  selectedValue,
  type ModelOption,
} from "./model-options";

const CAPABILITIES: ModelCapability[] = ["chat", "consolidate"];
const SOURCE_LABELS: Record<ActiveModelInfo["source"], string> = {
  database: "设置页切换",
  env: ".env 默认",
};

const sectionStyle = { display: "grid", gap: 8, marginBottom: 20 } as const;
const headingStyle = { fontSize: 15, margin: 0 } as const;
const buttonStyle = { padding: "4px 12px" } as const;

export function ModelSettings() {
  const [snapshot, setSnapshot] = useState<ModelSettingsSnapshot | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(
    () =>
      loadModelSettings().then(
        (next) => {
          setSnapshot(next);
          setLoadError(null);
        },
        (error: unknown) => {
          setLoadError(describeRequestError(error));
        },
      ),
    [],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onSwitch = (capability: ModelCapability, option: ModelOption) => {
    setBusy(true);
    setNotice(null);
    setActionError(null);
    switchActiveModel(capability, { provider: option.provider, model: option.model })
      .then(
        () => {
          setNotice(`${CAPABILITY_LABELS[capability]}已切换为 ${option.label}，下一次对话生效`);
        },
        (error: unknown) => {
          setActionError(describeRequestError(error));
        },
      )
      .finally(() => {
        setBusy(false);
        void refresh();
      });
  };

  const onReload = () => {
    setBusy(true);
    setNotice(null);
    setActionError(null);
    reloadModelConfig()
      .then(
        (result) => {
          setNotice(`配置已重载，版本 ${result.version}`);
        },
        (error: unknown) => {
          setActionError(`${describeRequestError(error)}（仍使用旧配置）`);
        },
      )
      .finally(() => {
        setBusy(false);
        void refresh();
      });
  };

  if (snapshot === null) {
    return (
      <section>
        {loadError ? (
          <p role="alert" style={{ color: "#991b1b" }}>
            {loadError}
          </p>
        ) : (
          <p style={{ color: "#777" }}>加载中…</p>
        )}
      </section>
    );
  }

  const { providers, active } = snapshot;
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
          <button type="button" onClick={onReload} disabled={busy} style={buttonStyle}>
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
        {actionError && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13, margin: 0 }}>
            {actionError}
          </p>
        )}
        {notice && <p style={{ color: "#166534", fontSize: 13, margin: 0 }}>{notice}</p>}
        {loadError && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13, margin: 0 }}>
            {loadError}
          </p>
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
