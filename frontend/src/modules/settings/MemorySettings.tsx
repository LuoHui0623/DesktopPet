import { useCallback, useEffect, useState } from "react";
import type { DailyRecord, MemoriesResponse, MemoryCategory, MemoryPatch } from "../../api/types";
import { describeRequestError } from "../../service/api-client";
import {
  addToCore,
  editMemory,
  loadMemories,
  removeFromCore,
  removeMemory,
  reorderCore,
  setTemporaryMode,
} from "../../service/memory.service";
import { MemoryCard } from "./MemoryCard";
import {
  allDates,
  CATEGORY_OPTIONS,
  categoryLabel,
  coreEntries,
  filterByCategory,
  moveId,
} from "./memory-view";

const sectionStyle = { display: "grid", gap: 8, marginBottom: 20 } as const;
const headingStyle = { fontSize: 15, margin: 0 } as const;
const buttonStyle = { padding: "2px 8px", fontSize: 12 } as const;
const ROLE_LABELS: Record<string, string> = { user: "我", assistant: "宠物" };

export function MemorySettings() {
  const [data, setData] = useState<MemoriesResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [category, setCategory] = useState<MemoryCategory | null>(null);
  const [busy, setBusy] = useState(false);
  const [day, setDay] = useState("");
  const [dayRecords, setDayRecords] = useState<DailyRecord[] | null>(null);
  const [dayError, setDayError] = useState<string | null>(null);

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

  const applyEdit = (memoryId: string, patch: MemoryPatch): Promise<void> =>
    editMemory(memoryId, patch).then((item) => {
      setData((previous) =>
        previous
          ? {
              ...previous,
              structured: previous.structured.map((existing) =>
                existing.id === memoryId ? item : existing,
              ),
            }
          : previous,
      );
    });

  const applyDelete = (memoryId: string): Promise<void> =>
    removeMemory(memoryId).then(() => {
      setData((previous) =>
        previous
          ? {
              ...previous,
              structured: previous.structured.filter((item) => item.id !== memoryId),
              core_ids: previous.core_ids.filter((id) => id !== memoryId),
            }
          : previous,
      );
    });

  const toggleCore = (memoryId: string, inCore: boolean) => {
    setActionError(null);
    const request = inCore ? removeFromCore(memoryId) : addToCore(memoryId);
    request.then(
      (result) => {
        setData((previous) => (previous ? { ...previous, core_ids: result.core_ids } : previous));
      },
      (error: unknown) => {
        setActionError(describeRequestError(error));
      },
    );
  };

  const moveCore = (memoryId: string, delta: -1 | 1) => {
    if (!data) return;
    const next = moveId(data.core_ids, memoryId, delta);
    if (next === data.core_ids) return;
    setActionError(null);
    reorderCore(next).then(
      (result) => {
        setData((previous) => (previous ? { ...previous, core_ids: result.core_ids } : previous));
      },
      (error: unknown) => {
        setActionError(describeRequestError(error));
      },
    );
  };

  const toggleTemporary = () => {
    if (!data) return;
    setBusy(true);
    setActionError(null);
    setTemporaryMode(!data.temporary_mode)
      .then(
        (result) => {
          setData((previous) =>
            previous ? { ...previous, temporary_mode: result.temporary_mode } : previous,
          );
        },
        (error: unknown) => {
          setActionError(describeRequestError(error));
        },
      )
      .finally(() => setBusy(false));
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

  const visible = filterByCategory(data.structured, category);
  const core = coreEntries(data.structured, data.core_ids);
  const dates = allDates(data.daily_memory);

  return (
    <div>
      {data.temporary_mode && (
        <div
          role="status"
          style={{
            border: "2px solid #d97706",
            background: "#fef3c7",
            color: "#78350f",
            borderRadius: 8,
            padding: 10,
            marginBottom: 16,
            fontSize: 13,
          }}
        >
          <strong>临时模式已开启</strong>：本次对话不写入每日记录，也不写入结构化记忆。
        </div>
      )}

      <section style={sectionStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={headingStyle}>临时模式</h2>
          <button type="button" onClick={toggleTemporary} disabled={busy} style={buttonStyle}>
            {data.temporary_mode ? "关闭临时模式" : "开启临时模式"}
          </button>
        </div>
      </section>

      <section style={sectionStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={headingStyle}>结构化记忆</h2>
          <select
            aria-label="按类别筛选"
            value={category ?? ""}
            onChange={(event) =>
              setCategory(event.target.value ? (event.target.value as MemoryCategory) : null)
            }
          >
            <option value="">全部类别</option>
            {CATEGORY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {categoryLabel(option)}
              </option>
            ))}
          </select>
        </div>
        {actionError && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13, margin: 0 }}>
            {actionError}
          </p>
        )}
        {visible.length === 0 && <p style={{ color: "#777", fontSize: 13 }}>暂无记忆</p>}
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
          {visible.map((item) => (
            <MemoryCard
              key={item.id}
              item={item}
              inCore={data.core_ids.includes(item.id)}
              onEdit={applyEdit}
              onDelete={applyDelete}
              onToggleCore={toggleCore}
            />
          ))}
        </ul>
      </section>

      <section style={sectionStyle}>
        <h2 style={headingStyle}>核心典藏（{data.core_ids.length} 条）</h2>
        {core.length === 0 && <p style={{ color: "#777", fontSize: 13 }}>暂无核心典藏</p>}
        <ol style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 6, fontSize: 13 }}>
          {core.map((entry, index) => (
            <li key={entry.id}>
              {entry.memory ? entry.memory.title : `（已不存在：${entry.id}）`}
              <span style={{ marginLeft: 8, display: "inline-flex", gap: 4 }}>
                <button
                  type="button"
                  aria-label="上移"
                  disabled={index === 0}
                  onClick={() => moveCore(entry.id, -1)}
                  style={buttonStyle}
                >
                  上移
                </button>
                <button
                  type="button"
                  aria-label="下移"
                  disabled={index === core.length - 1}
                  onClick={() => moveCore(entry.id, 1)}
                  style={buttonStyle}
                >
                  下移
                </button>
                <button
                  type="button"
                  onClick={() => toggleCore(entry.id, true)}
                  style={buttonStyle}
                >
                  移出
                </button>
              </span>
            </li>
          ))}
        </ol>
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
                {record.created_at.slice(11, 16)} · {ROLE_LABELS[record.role] ?? record.role}
              </div>
              <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {record.content}
              </div>
            </li>
          ))}
        </ul>
      </section>

      {data.skipped.length > 0 && (
        <section style={sectionStyle}>
          <h2 style={headingStyle}>已跳过的记忆文件</h2>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: "#8a5a00" }}>
            {data.skipped.map((item) => (
              <li key={item.name}>
                {item.name}：{item.reason}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
