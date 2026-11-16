import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import type {
  DailyRecord,
  MemoriesResponse,
  MemoryEntryView,
  TagMergePreview,
} from "../../api/types";
import { describeRequestError } from "../../service/api-client";
import {
  createEntry,
  disableEntry,
  enableEntry,
  loadEntries,
  loadMemories,
  loadTags,
  mergeTags,
  previewMerge,
  updateEntry,
} from "../../service/memory.service";
import { formatClock } from "../chat/clock-view";
import {
  allDates,
  annotatePathChange,
  groupEntriesByPath,
  parseTagsInput,
  pathLabel,
} from "./memory-view";

const sectionStyle = { display: "grid", gap: 8, marginBottom: 20 } as const;
const headingStyle = { fontSize: 15, margin: 0 } as const;
const buttonStyle = { padding: "2px 8px", fontSize: 12 } as const;
const inputStyle = { fontSize: 13, padding: "3px 6px", fontFamily: "inherit" } as const;
const errorStyle = { color: "#991b1b", fontSize: 13, margin: 0 } as const;
const hintStyle = { color: "#777", fontSize: 12, margin: 0 } as const;
const ROLE_LABELS: Record<string, string> = {
  master: "我",
  me: "宠物",
  assistant: "子代理",
};

type EditorState = { mode: "add" } | { mode: "edit"; entry: MemoryEntryView };

export function MemorySettings() {
  const [data, setData] = useState<MemoriesResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [day, setDay] = useState("");
  const [dayRecords, setDayRecords] = useState<DailyRecord[] | null>(null);
  const [dayError, setDayError] = useState<string | null>(null);

  const [entries, setEntries] = useState<MemoryEntryView[] | null>(null);
  const [entriesError, setEntriesError] = useState<string | null>(null);
  const [tags, setTags] = useState<string[]>([]);

  const [editor, setEditor] = useState<EditorState | null>(null);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [disabling, setDisabling] = useState<MemoryEntryView | null>(null);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refreshEntries = useCallback(
    () =>
      Promise.all([loadEntries(), loadTags()]).then(
        ([entryResult, tagResult]) => {
          setEntries(entryResult.entries);
          setTags(tagResult.tags);
          setEntriesError(null);
        },
        (error: unknown) => {
          setEntriesError(describeRequestError(error));
        },
      ),
    [],
  );

  const refreshDaily = useCallback(
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
    void refreshEntries();
    void refreshDaily();
  }, [refreshEntries, refreshDaily]);

  const closeEditor = () => {
    setEditor(null);
    setEditorError(null);
  };

  const submitEditor = (content: string, tags: string[]) => {
    if (!editor) return;
    const current = editor;
    setBusy(true);
    setEditorError(null);
    setActionError(null);
    setNotice(null);
    const done = (message: string) => {
      setEditor(null);
      setNotice(message);
      void refreshEntries();
    };
    const fail = (error: unknown) => {
      // revision 冲突：不自动重试或覆盖，刷新列表后提示重新编辑（Q27）。
      if (error instanceof ApiError && error.code === "conflict") {
        setEditor(null);
        setNotice("条目已被其他修改更新，已刷新列表，请重新编辑");
        void refreshEntries();
      } else {
        setEditorError(describeRequestError(error));
      }
    };
    if (current.mode === "add") {
      createEntry({ content, tags })
        .then((result) => done(result.reused ? "已复用现有条目" : "已新增条目"), fail)
        .finally(() => setBusy(false));
    } else {
      updateEntry(current.entry.id, {
        content,
        tags,
        expected_revision: current.entry.revision,
      })
        .then(() => done("已更新条目"), fail)
        .finally(() => setBusy(false));
    }
  };

  const enable = (entry: MemoryEntryView) => {
    setBusy(true);
    setActionError(null);
    setNotice(null);
    enableEntry(entry.id)
      .then(
        () => {
          setNotice("已启用条目");
          void refreshEntries();
        },
        (error: unknown) => setActionError(describeRequestError(error)),
      )
      .finally(() => setBusy(false));
  };

  const submitDisable = (reason: string) => {
    if (!disabling) return;
    const target = disabling;
    setBusy(true);
    setActionError(null);
    setNotice(null);
    disableEntry(target.id, reason)
      .then(
        () => {
          setDisabling(null);
          setNotice("已停用条目");
          void refreshEntries();
        },
        (error: unknown) => setActionError(describeRequestError(error)),
      )
      .finally(() => setBusy(false));
  };

  const merge = (source: string, target: string) => {
    setBusy(true);
    setActionError(null);
    setNotice(null);
    mergeTags({ source, target })
      .then(
        (result) => {
          // 合并/改名后关闭所有可能过期的表单，再刷新列表与标签（Q27）。
          setMergeOpen(false);
          setEditor(null);
          setDisabling(null);
          setNotice(
            `${result.target_existed ? "已合并" : "已改名"}：${source} → ${target}，影响 ${result.affected} 条`,
          );
          void refreshEntries();
        },
        (error: unknown) => setActionError(describeRequestError(error)),
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

  const groups = groupEntriesByPath(entries ?? []);
  const dates = data ? allDates(data.daily_memory) : [];

  return (
    <div>
      <section style={sectionStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={headingStyle}>结构化记忆</h2>
          <span style={{ display: "inline-flex", gap: 6 }}>
            <button
              type="button"
              style={buttonStyle}
              disabled={busy}
              onClick={() => {
                setDisabling(null);
                setMergeOpen(false);
                setEditorError(null);
                setActionError(null);
                setNotice(null);
                setEditor({ mode: "add" });
              }}
            >
              新增条目
            </button>
            <button
              type="button"
              style={buttonStyle}
              disabled={busy}
              onClick={() => {
                closeEditor();
                setDisabling(null);
                setActionError(null);
                setNotice(null);
                setMergeOpen(true);
              }}
            >
              合并/改名标签
            </button>
          </span>
        </div>
        <p style={hintStyle}>
          条目按各自的有序标签路径分组；人工编辑与 AI 写入共用同一校验。停用条目仍可见并标注原因。
        </p>
        {actionError && (
          <p role="alert" style={errorStyle}>
            {actionError}
          </p>
        )}
        {notice && <p style={{ color: "#166534", fontSize: 13, margin: 0 }}>{notice}</p>}

        {editor?.mode === "add" && (
          <EntryEditor
            key="add"
            title="新增条目"
            initialContent=""
            initialTags={[]}
            saving={busy}
            error={editorError}
            onSubmit={submitEditor}
            onCancel={closeEditor}
          />
        )}

        {mergeOpen && (
          <TagMergePanel
            tags={tags}
            busy={busy}
            onMerge={merge}
            onClose={() => setMergeOpen(false)}
          />
        )}

        {entriesError && (
          <p role="alert" style={errorStyle}>
            {entriesError}
          </p>
        )}
        {entries === null ? (
          !entriesError && <p style={{ color: "#777" }}>加载中…</p>
        ) : groups.length === 0 ? (
          <p style={{ color: "#777", fontSize: 13 }}>
            还没有条目：结构化记忆由 agent 写入，也可在这里手动新增。
          </p>
        ) : (
          groups.map((group) => (
            <div key={pathLabel(group.path)} style={{ display: "grid", gap: 6 }}>
              <h3 style={{ fontSize: 13, margin: "4px 0 0", color: "#374151" }}>
                {pathLabel(group.path)}
              </h3>
              <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
                {group.entries.map((entry) => (
                  <li
                    key={entry.id}
                    style={{
                      border: "1px solid #e5e7eb",
                      borderRadius: 8,
                      padding: 8,
                      fontSize: 13,
                      opacity: entry.status_state === "disabled" ? 0.7 : 1,
                    }}
                  >
                    {editor?.mode === "edit" && editor.entry.id === entry.id ? (
                      <EntryEditor
                        key={`edit-${entry.id}`}
                        title="编辑条目"
                        initialContent={entry.content}
                        initialTags={entry.tags}
                        saving={busy}
                        error={editorError}
                        onSubmit={submitEditor}
                        onCancel={closeEditor}
                      />
                    ) : (
                      <div style={{ display: "grid", gap: 4 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                          <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                            {entry.content}
                          </span>
                          <span style={{ display: "inline-flex", gap: 6, flexShrink: 0 }}>
                            <button
                              type="button"
                              style={buttonStyle}
                              disabled={busy}
                              onClick={() => {
                                setDisabling(null);
                                setEditorError(null);
                                setActionError(null);
                                setNotice(null);
                                setEditor({ mode: "edit", entry });
                              }}
                            >
                              编辑
                            </button>
                            {entry.status_state === "enabled" ? (
                              <button
                                type="button"
                                style={buttonStyle}
                                disabled={busy}
                                onClick={() => {
                                  setActionError(null);
                                  setNotice(null);
                                  setDisabling(entry);
                                }}
                              >
                                停用
                              </button>
                            ) : (
                              <button
                                type="button"
                                style={buttonStyle}
                                disabled={busy}
                                onClick={() => enable(entry)}
                              >
                                启用
                              </button>
                            )}
                          </span>
                        </div>
                        {entry.status_state === "disabled" && (
                          <p style={{ ...errorStyle, color: "#92400e" }}>
                            已停用{entry.status_reason ? `：${entry.status_reason}` : ""}
                          </p>
                        )}
                        {disabling?.id === entry.id && (
                          <DisableForm
                            key={`disable-${entry.id}`}
                            saving={busy}
                            onSubmit={submitDisable}
                            onCancel={() => setDisabling(null)}
                          />
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </section>

      <section style={sectionStyle}>
        <h2 style={headingStyle}>每日对话（只读）</h2>
        {loadError && (
          <p role="alert" style={errorStyle}>
            {loadError}
          </p>
        )}
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
          <p role="alert" style={errorStyle}>
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

function EntryEditor({
  title,
  initialContent,
  initialTags,
  saving,
  error,
  onSubmit,
  onCancel,
}: {
  title: string;
  initialContent: string;
  initialTags: string[];
  saving: boolean;
  error: string | null;
  onSubmit: (content: string, tags: string[]) => void;
  onCancel: () => void;
}) {
  const [content, setContent] = useState(initialContent);
  const [tagsText, setTagsText] = useState(pathLabel(initialTags));
  return (
    <div
      style={{
        border: "1px solid #3b82f6",
        borderRadius: 8,
        padding: 8,
        display: "grid",
        gap: 6,
        fontSize: 13,
      }}
    >
      <strong style={{ fontSize: 13 }}>{title}</strong>
      <label style={{ display: "grid", gap: 2 }}>
        <span>正文</span>
        <input
          aria-label="条目正文"
          style={inputStyle}
          value={content}
          disabled={saving}
          onChange={(event) => setContent(event.target.value)}
        />
      </label>
      <label style={{ display: "grid", gap: 2 }}>
        <span>标签路径</span>
        <input
          aria-label="条目标签路径"
          style={inputStyle}
          value={tagsText}
          disabled={saving}
          placeholder="食物 / 水果"
          onChange={(event) => setTagsText(event.target.value)}
        />
      </label>
      <p style={hintStyle}>
        正文单行、≤30 字，不含换行/顿号，不以 #/-/&gt;/* 开头；标签用 / 分隔、按顺序填写，至少一个。
      </p>
      {error && (
        <p role="alert" style={errorStyle}>
          {error}
        </p>
      )}
      <span style={{ display: "inline-flex", gap: 6 }}>
        <button
          type="button"
          style={buttonStyle}
          disabled={saving}
          onClick={() => onSubmit(content, parseTagsInput(tagsText))}
        >
          保存
        </button>
        <button type="button" style={buttonStyle} disabled={saving} onClick={onCancel}>
          取消
        </button>
      </span>
    </div>
  );
}

function DisableForm({
  saving,
  onSubmit,
  onCancel,
}: {
  saving: boolean;
  onSubmit: (reason: string) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
      <input
        aria-label="弃用原因"
        style={{ ...inputStyle, flex: 1, minWidth: 160 }}
        value={reason}
        disabled={saving}
        placeholder="弃用原因（必填）"
        onChange={(event) => setReason(event.target.value)}
      />
      <button
        type="button"
        style={buttonStyle}
        disabled={saving || reason.trim() === ""}
        onClick={() => onSubmit(reason)}
      >
        确认停用
      </button>
      <button type="button" style={buttonStyle} disabled={saving} onClick={onCancel}>
        取消
      </button>
    </div>
  );
}

function TagMergePanel({
  tags,
  busy,
  onMerge,
  onClose,
}: {
  tags: string[];
  busy: boolean;
  onMerge: (source: string, target: string) => void;
  onClose: () => void;
}) {
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [preview, setPreview] = useState<TagMergePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const resetPreview = () => {
    setPreview(null);
    setPreviewError(null);
  };

  const runPreview = () => {
    setPreviewing(true);
    resetPreview();
    previewMerge({ source, target })
      .then(
        (next) => setPreview(next),
        (error: unknown) => setPreviewError(describeRequestError(error)),
      )
      .finally(() => setPreviewing(false));
  };

  const canPreview = source !== "" && target !== "" && source !== target;
  // 阻断式确认：只有预览与当前选择一致时才允许执行，选择变化后必须重新预览（Q27/第 29 条）。
  const previewMatches = preview !== null && preview.source === source && preview.target === target;

  return (
    <div
      style={{
        border: "1px solid #3b82f6",
        borderRadius: 8,
        padding: 8,
        display: "grid",
        gap: 6,
        fontSize: 13,
      }}
    >
      <strong style={{ fontSize: 13 }}>合并/改名标签</strong>
      <p style={hintStyle}>
        手动选择源与目标标签：目标已存在即合并，不存在即改名。不自动建议近义标签。
      </p>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <label style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
          源
          <select
            aria-label="源标签"
            value={source}
            disabled={busy || previewing}
            onChange={(event) => {
              setSource(event.target.value);
              resetPreview();
            }}
          >
            <option value="">选择标签</option>
            {tags.map((tag) => (
              <option key={tag} value={tag}>
                {tag}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
          目标
          <select
            aria-label="目标标签"
            value={target}
            disabled={busy || previewing}
            onChange={(event) => {
              setTarget(event.target.value);
              resetPreview();
            }}
          >
            <option value="">选择标签</option>
            {tags.map((tag) => (
              <option key={tag} value={tag}>
                {tag}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          style={buttonStyle}
          disabled={busy || previewing || !canPreview}
          onClick={runPreview}
        >
          预览
        </button>
      </div>
      {previewError && (
        <p role="alert" style={errorStyle}>
          {previewError}
        </p>
      )}
      {preview && (
        <div style={{ display: "grid", gap: 6 }}>
          <p style={{ margin: 0 }}>
            {preview.target_existed ? "合并" : "改名"}：{preview.source} → {preview.target}，影响{" "}
            {preview.affected} 条
          </p>
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 4 }}>
            {preview.changes.map((change) => {
              const annotated = annotatePathChange(change);
              return (
                <li
                  key={change.entry_id}
                  style={{ border: "1px solid #e5e7eb", borderRadius: 6, padding: 6 }}
                >
                  <div style={{ wordBreak: "break-word" }}>{change.content}</div>
                  <div style={{ color: "#374151" }}>
                    {pathLabel(change.before)} → {pathLabel(change.after)}
                  </div>
                  <span style={{ display: "inline-flex", gap: 6 }}>
                    {annotated.titleChanged && <small style={{ color: "#92400e" }}>标题变化</small>}
                    {annotated.folded && <small style={{ color: "#92400e" }}>去重折叠</small>}
                  </span>
                </li>
              );
            })}
          </ul>
          <span style={{ display: "inline-flex", gap: 6 }}>
            <button
              type="button"
              style={buttonStyle}
              disabled={busy || !previewMatches}
              onClick={() => onMerge(source, target)}
            >
              {preview.target_existed ? "确认合并" : "确认改名"}
            </button>
            <button type="button" style={buttonStyle} disabled={busy} onClick={onClose}>
              取消
            </button>
          </span>
        </div>
      )}
      {!preview && (
        <span>
          <button type="button" style={buttonStyle} disabled={busy} onClick={onClose}>
            关闭
          </button>
        </span>
      )}
    </div>
  );
}
