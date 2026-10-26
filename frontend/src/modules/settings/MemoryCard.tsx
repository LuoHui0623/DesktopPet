import { useState } from "react";
import type { MemoryCategory, MemoryItem, MemoryPatch } from "../../api/types";
import { describeRequestError } from "../../service/api-client";
import { CATEGORY_OPTIONS, categoryLabel } from "./memory-view";

interface MemoryCardProps {
  item: MemoryItem;
  inCore: boolean;
  onEdit: (memoryId: string, patch: MemoryPatch) => Promise<void>;
  onDelete: (memoryId: string) => Promise<void>;
  onToggleCore: (memoryId: string, inCore: boolean) => void;
}

const buttonStyle = { padding: "2px 8px", fontSize: 12 } as const;

export function MemoryCard({ item, inCore, onEdit, onDelete, onToggleCore }: MemoryCardProps) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [draftTitle, setDraftTitle] = useState(item.title);
  const [draftContent, setDraftContent] = useState(item.content);
  const [draftCategory, setDraftCategory] = useState<MemoryCategory>(
    item.category as MemoryCategory,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startEdit = () => {
    setDraftTitle(item.title);
    setDraftContent(item.content);
    setDraftCategory(item.category as MemoryCategory);
    setError(null);
    setEditing(true);
  };

  const save = () => {
    const title = draftTitle.trim();
    const content = draftContent.trim();
    if (!title || !content) {
      setError("标题和内容不能为空");
      return;
    }
    setSaving(true);
    setError(null);
    onEdit(item.id, { title, content, category: draftCategory })
      .then(
        () => setEditing(false),
        (caught: unknown) => setError(describeRequestError(caught)),
      )
      .finally(() => setSaving(false));
  };

  const confirmDelete = () => {
    setError(null);
    onDelete(item.id).catch((caught: unknown) => {
      setError(describeRequestError(caught));
      setConfirming(false);
    });
  };

  return (
    <li style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 10, fontSize: 13 }}>
      {editing ? (
        <div style={{ display: "grid", gap: 6 }}>
          <input
            aria-label="标题"
            value={draftTitle}
            onChange={(event) => setDraftTitle(event.target.value)}
            style={{ fontSize: 13, padding: 4 }}
          />
          <select
            aria-label="类别"
            value={draftCategory}
            onChange={(event) => setDraftCategory(event.target.value as MemoryCategory)}
          >
            {CATEGORY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {categoryLabel(option)}
              </option>
            ))}
          </select>
          <textarea
            aria-label="内容"
            value={draftContent}
            rows={3}
            onChange={(event) => setDraftContent(event.target.value)}
            style={{ fontSize: 13, padding: 4, resize: "vertical" }}
          />
        </div>
      ) : (
        <>
          <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <strong>{item.title}</strong>
            <span style={{ color: "#777" }}>
              {categoryLabel(item.category)} · 来自 {item.source.date}
            </span>
          </div>
          <p style={{ margin: "4px 0 0", whiteSpace: "pre-wrap" }}>{item.content}</p>
        </>
      )}

      {error && (
        <p role="alert" style={{ color: "#991b1b", margin: "6px 0 0" }}>
          {error}
        </p>
      )}

      <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
        {editing ? (
          <>
            <button type="button" onClick={save} disabled={saving} style={buttonStyle}>
              保存
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              disabled={saving}
              style={buttonStyle}
            >
              取消
            </button>
          </>
        ) : confirming ? (
          <>
            <span style={{ color: "#991b1b" }}>确认删除这条记忆？</span>
            <button type="button" onClick={confirmDelete} style={buttonStyle}>
              确认删除
            </button>
            <button type="button" onClick={() => setConfirming(false)} style={buttonStyle}>
              取消
            </button>
          </>
        ) : (
          <>
            <button type="button" onClick={startEdit} style={buttonStyle}>
              编辑
            </button>
            <button type="button" onClick={() => setConfirming(true)} style={buttonStyle}>
              删除
            </button>
            <button type="button" onClick={() => onToggleCore(item.id, inCore)} style={buttonStyle}>
              {inCore ? "移出典藏" : "加入典藏"}
            </button>
          </>
        )}
      </div>
    </li>
  );
}
