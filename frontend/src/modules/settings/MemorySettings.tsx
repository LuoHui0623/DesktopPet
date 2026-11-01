import { useCallback, useEffect, useState } from "react";
import type { DailyRecord, MemoriesResponse } from "../../api/types";
import { describeRequestError } from "../../service/api-client";
import { loadMemories, saveStructured } from "../../service/memory.service";
import { formatClock } from "../chat/clock-view";
import { allDates, numberedLines } from "./memory-view";

const sectionStyle = { display: "grid", gap: 8, marginBottom: 20 } as const;
const headingStyle = { fontSize: 15, margin: 0 } as const;
const buttonStyle = { padding: "2px 8px", fontSize: 12 } as const;
const ROLE_LABELS: Record<string, string> = {
  master: "我",
  me: "宠物",
  assistant: "子代理",
};

export function MemorySettings() {
  const [data, setData] = useState<MemoriesResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [day, setDay] = useState("");
  const [dayRecords, setDayRecords] = useState<DailyRecord[] | null>(null);
  const [dayError, setDayError] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [structuredError, setStructuredError] = useState<string | null>(null);

  const refresh = useCallback(
    () =>
      loadMemories().then(
        (next) => {
          setData(next);
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

  const saveStructuredText = () => {
    if (draft === null) return;
    setSaving(true);
    setStructuredError(null);
    saveStructured(draft)
      .then(
        (result) => {
          setData((previous) => (previous ? { ...previous, structured: result.text } : previous));
          setDraft(null);
        },
        (error: unknown) => {
          setStructuredError(describeRequestError(error));
        },
      )
      .finally(() => setSaving(false));
  };

  const selectDay = (next: string) => {
    setDay(next);
    setDayRecords(null);
    setDayError(null);
    if (!next) return;
    loadMemories({ date: next }).then(
      (result) => setDayRecords(result.daily_records ?? []),
      (error: unknown) => setDayError(describeRequestError(error)),
    );
  };

  if (data === null) {
    return loadError ? (
      <p role="alert" style={{ color: "#991b1b" }}>
        {loadError}
      </p>
    ) : (
      <p style={{ color: "#777" }}>加载中…</p>
    );
  }

  const dates = allDates(data.daily_memory);
  const lines = numberedLines(data.structured);

  return (
    <div>
      <section style={sectionStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={headingStyle}>结构化记忆</h2>
          {draft === null ? (
            <button
              type="button"
              onClick={() => {
                setDraft(data.structured);
                setStructuredError(null);
              }}
              style={buttonStyle}
            >
              编辑
            </button>
          ) : (
            <span style={{ display: "inline-flex", gap: 6 }}>
              <button
                type="button"
                onClick={saveStructuredText}
                disabled={saving}
                style={buttonStyle}
              >
                保存
              </button>
              <button
                type="button"
                onClick={() => {
                  setDraft(null);
                  setStructuredError(null);
                }}
                disabled={saving}
                style={buttonStyle}
              >
                取消
              </button>
            </span>
          )}
        </div>
        {structuredError && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13, margin: 0 }}>
            {structuredError}
          </p>
        )}
        {draft === null ? (
          lines.length === 0 ? (
            <p style={{ color: "#777", fontSize: 13 }}>
              还没有内容：结构化记忆是空文件，结构由 agent 自行设计后写入。
            </p>
          ) : (
            <ol
              aria-label="结构化记忆正文"
              style={{ margin: 0, paddingLeft: 0, listStyle: "none", fontSize: 13 }}
            >
              {lines.map((entry) => (
                <li key={entry.line} style={{ display: "flex", gap: 8 }}>
                  <span
                    aria-hidden="true"
                    style={{ color: "#9ca3af", minWidth: 28, textAlign: "right" }}
                  >
                    {entry.line}
                  </span>
                  <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                    {entry.text}
                  </span>
                </li>
              ))}
            </ol>
          )
        ) : (
          <textarea
            aria-label="结构化记忆编辑"
            value={draft}
            rows={16}
            onChange={(event) => setDraft(event.target.value)}
            style={{ fontSize: 13, padding: 6, resize: "vertical", fontFamily: "inherit" }}
          />
        )}
      </section>

      <section style={sectionStyle}>
        <h2 style={headingStyle}>每日对话（只读）</h2>
        <select
          aria-label="选择日期"
          value={day}
          onChange={(event) => selectDay(event.target.value)}
        >
          <option value="">选择日期</option>
          {dates.map((date) => (
            <option key={date} value={date}>
              {date}
            </option>
          ))}
        </select>
        {dayError && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13, margin: 0 }}>
            {dayError}
          </p>
        )}
        {day && dayRecords === null && !dayError && <p style={{ color: "#777" }}>加载中…</p>}
        {dayRecords !== null && dayRecords.length === 0 && (
          <p style={{ color: "#777", fontSize: 13 }}>这一天没有对话记录</p>
        )}
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
          {(dayRecords ?? []).map((record) => (
            <li
              key={record.id}
              style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 8, fontSize: 13 }}
            >
              <div style={{ color: "#666", marginBottom: 2 }}>
                {formatClock(record.created_at)} · {ROLE_LABELS[record.role] ?? record.role}
              </div>
              <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {record.content}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
