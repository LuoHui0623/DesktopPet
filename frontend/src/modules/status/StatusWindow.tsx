import { useStore } from "zustand";
import type { UsageCall, UsagePanelTurn } from "../../api/types";
import { statusStore } from "../../service/stores/status.store";
import { callLabel, formatDuration, formatTokens, turnStateLabel } from "./status-view";

const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

function Badge({ text, tone }: { text: string; tone: "ok" | "warn" | "muted" }) {
  const color = tone === "ok" ? "#166534" : tone === "warn" ? "#b91c1c" : "#4b5563";
  const background = tone === "ok" ? "#dcfce7" : tone === "warn" ? "#fee2e2" : "#f3f4f6";
  return (
    <span style={{ padding: "1px 8px", borderRadius: 10, fontSize: 12, color, background }}>
      {text}
    </span>
  );
}

function Summary({ turn }: { turn: UsagePanelTurn }) {
  return (
    <section
      aria-label="本轮汇总"
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
        gap: 8,
        padding: 10,
        borderRadius: 8,
        background: "#f9fafb",
        border: "1px solid #e5e7eb",
      }}
    >
      <div>
        <div style={{ fontSize: 12, color: "#6b7280" }}>输入 token</div>
        <div style={{ fontSize: 18 }}>{formatTokens(turn.tokens_in)}</div>
      </div>
      <div>
        <div style={{ fontSize: 12, color: "#6b7280" }}>输出 token</div>
        <div style={{ fontSize: 18 }}>{formatTokens(turn.tokens_out)}</div>
      </div>
      <div>
        <div style={{ fontSize: 12, color: "#6b7280" }}>模型耗时</div>
        <div style={{ fontSize: 18 }}>{formatDuration(turn.model_time_ms)}</div>
      </div>
      {!turn.tokens_complete && (
        <div style={{ gridColumn: "1 / -1", fontSize: 12, color: "#b45309" }}>
          token 统计不完整：有调用没有返回用量，未知值不按 0 计。
        </div>
      )}
    </section>
  );
}

function CallCard({ call, dev }: { call: UsageCall; dev: boolean }) {
  const state =
    call.success === null ? (
      <Badge text="进行中" tone="muted" />
    ) : call.success ? (
      <Badge text="成功" tone="ok" />
    ) : (
      <Badge text="失败" tone="warn" />
    );
  return (
    <article
      style={{
        padding: 10,
        borderRadius: 8,
        border: "1px solid #e5e7eb",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <strong style={{ fontSize: 14 }}>{callLabel(call.purpose)}</strong>
        {state}
        {dev && call.provider !== null && (
          <span style={{ fontSize: 12, color: "#6b7280" }}>
            {call.provider} / {call.model ?? "未知模型"}
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 12, color: "#6b7280" }}>
          {call.started_at}
        </span>
      </header>
      <div style={{ display: "flex", gap: 16, fontSize: 13 }}>
        <span>输入 {formatTokens(call.tokens_in)}</span>
        <span>输出 {formatTokens(call.tokens_out)}</span>
        <span>耗时 {formatDuration(call.latency_ms)}</span>
      </div>
      {dev && (
        <>
          <div>
            <div style={{ fontSize: 12, color: "#6b7280" }}>输入</div>
            <pre
              style={{
                margin: 0,
                maxHeight: 160,
                overflow: "auto",
                padding: 8,
                borderRadius: 6,
                background: "#f3f4f6",
                fontFamily: MONO,
                fontSize: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {call.prompt_text ?? "（无）"}
            </pre>
          </div>
          <div>
            <div style={{ fontSize: 12, color: "#6b7280" }}>原始响应（未解析）</div>
            <pre
              style={{
                margin: 0,
                maxHeight: 160,
                overflow: "auto",
                padding: 8,
                borderRadius: 6,
                background: "#f3f4f6",
                fontFamily: MONO,
                fontSize: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {call.reply_raw ?? (call.finished_at === null ? "等待响应…" : "（无）")}
            </pre>
          </div>
          {call.reply_text !== null && call.reply_raw !== call.reply_text && (
            <details>
              <summary style={{ fontSize: 12, color: "#6b7280", cursor: "pointer" }}>
                解析后正文
              </summary>
              <pre
                style={{
                  margin: "4px 0 0",
                  maxHeight: 160,
                  overflow: "auto",
                  padding: 8,
                  borderRadius: 6,
                  background: "#f3f4f6",
                  fontFamily: MONO,
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {call.reply_text}
              </pre>
            </details>
          )}
          {call.usage_raw !== null && (
            <details>
              <summary style={{ fontSize: 12, color: "#6b7280", cursor: "pointer" }}>
                原始用量
              </summary>
              <pre
                style={{
                  margin: "4px 0 0",
                  padding: 8,
                  borderRadius: 6,
                  background: "#f3f4f6",
                  fontFamily: MONO,
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {call.usage_raw}
              </pre>
            </details>
          )}
        </>
      )}
    </article>
  );
}

export function StatusWindow() {
  const connected = useStore(statusStore, (state) => state.connected);
  const mode = useStore(statusStore, (state) => state.mode);
  const turn = useStore(statusStore, (state) => state.turn);
  const dev = mode === "dev";

  return (
    <main
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        height: "100vh",
        boxSizing: "border-box",
        padding: 12,
        overflow: "auto",
        fontFamily: "system-ui, sans-serif",
        color: "#222",
        background: "#fff",
      }}
    >
      <header style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <h1 style={{ margin: 0, fontSize: 18 }}>状态</h1>
        <Badge text={connected ? "已连接" : "连接中"} tone={connected ? "ok" : "warn"} />
        {mode !== null && (
          <Badge text={dev ? "开发模式" : "正式模式"} tone={dev ? "ok" : "muted"} />
        )}
        {turn !== null && (
          <span style={{ marginLeft: "auto", fontSize: 13, color: "#6b7280" }}>
            {turnStateLabel(turn.finished)}
          </span>
        )}
      </header>

      {turn === null ? (
        <p style={{ margin: 0, color: "#6b7280" }}>
          {connected ? "等待下一轮对话…" : "正在连接后端…"}
        </p>
      ) : (
        <>
          <Summary turn={turn} />
          {turn.calls.length === 0 ? (
            <p style={{ margin: 0, color: "#6b7280" }}>本轮尚未发起模型调用。</p>
          ) : (
            turn.calls.map((call, index) => (
              <CallCard
                key={`${call.purpose ?? "unknown"}-${call.started_at}-${index}`}
                call={call}
                dev={dev}
              />
            ))
          )}
        </>
      )}
    </main>
  );
}
