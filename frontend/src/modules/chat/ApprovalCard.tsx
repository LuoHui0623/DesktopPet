import { decideApproval } from "../../service/chat.service";
import type { PendingApproval } from "../../service/stores/chat.store";
import type { FilePreview } from "../../api/types";

interface ApprovalCardProps {
  approval: PendingApproval;
  busy: boolean;
}

const OP_LABELS: Record<string, string> = {
  append: "追加",
  insert: "插入",
  replace_lines: "整段替换",
  replace_regex: "正则替换",
  delete: "删除内容",
  delete_file: "删除文件",
};

/** 逐次读取审批的动作名称；有值即表示这是读取票据而不是结算卡片。 */
const READ_LABELS: Record<string, string> = {
  index: "查看",
  read: "读取",
  grep: "检索",
};

function readTarget(approval: PendingApproval): string | null {
  if (!approval.resource) return null;
  return approval.path ? `${approval.resource}/${approval.path}` : approval.resource;
}

function previewTitle(preview: FilePreview): string {
  if (preview.deleted) {
    return `删除文件：${preview.path}`;
  }
  const action = OP_LABELS[preview.op] ?? preview.op;
  return `${action}：${preview.path}${preview.created ? "（新建）" : ""}`;
}

function PreviewBlock({ preview }: { preview: FilePreview }) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <p style={{ fontSize: 12, fontWeight: 600, margin: 0 }}>{previewTitle(preview)}</p>
      <p style={{ fontSize: 11, color: "#777", margin: 0 }}>
        {preview.created ? "新建文件" : preview.deleted ? "删除文件" : "修改"} · 共{" "}
        {preview.hunk_count} 处改动
        {preview.authors.length > 0 ? ` · 起草 ${preview.authors.join("、")}` : ""}
      </p>
      {preview.folded ? (
        <p style={{ fontSize: 12, color: "#777", margin: 0 }}>
          文件较大，已折叠差异；批准后按提示词中的修改请求写入。
        </p>
      ) : (
        (preview.hunks ?? []).map((hunk, index) => (
          <div
            key={`${hunk.header}-${index}`}
            style={{
              fontFamily: "monospace",
              fontSize: 12,
              background: "#fff",
              borderRadius: 4,
              padding: 6,
            }}
          >
            <p style={{ margin: 0, color: "#777" }}>{hunk.header}</p>
            {hunk.removed.map((line, index) => (
              <p key={`r-${index}`} style={{ margin: 0, color: "#b42318", whiteSpace: "pre-wrap" }}>
                - {line}
              </p>
            ))}
            {hunk.added.map((line, index) => (
              <p key={`a-${index}`} style={{ margin: 0, color: "#067647", whiteSpace: "pre-wrap" }}>
                + {line}
              </p>
            ))}
          </div>
        ))
      )}
    </section>
  );
}

export function ApprovalCard({ approval, busy }: ApprovalCardProps) {
  const decide = (approved: boolean) => {
    void decideApproval(approval.approval_id, approved);
  };
  const readLabel = approval.action ? READ_LABELS[approval.action] : undefined;
  const target = readLabel ? readTarget(approval) : null;

  return (
    <article
      style={{
        border: "1px solid #f0c36d",
        background: "#fffdf5",
        borderRadius: 6,
        padding: 8,
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      <header style={{ fontSize: 13, fontWeight: 600 }}>
        {readLabel ? "读取审批" : "待审批"}：{approval.topic}
      </header>
      {approval.persona_id ? (
        <p style={{ fontSize: 12, color: "#777", margin: 0 }}>
          起草：{approval.subtask_id ?? approval.run_id} · 人设 {approval.persona_id} v
          {approval.persona_version ?? "?"}
          {approval.model ? ` · 模型 ${approval.model}` : ""}
        </p>
      ) : null}
      <p style={{ fontSize: 13, margin: 0 }}>任务：{approval.task}</p>
      {readLabel ? (
        <p style={{ fontSize: 12, color: "#777", margin: 0 }}>
          动作：{readLabel}
          {target ? ` · 目标：${target}` : ""}
        </p>
      ) : null}
      <p style={{ fontSize: 12, color: "#777", margin: 0 }}>{readLabel ? "参数" : "内容"}</p>
      <pre
        style={{
          margin: 0,
          fontSize: 12,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          background: "#fff",
          borderRadius: 4,
          padding: 6,
        }}
      >
        {approval.content}
      </pre>
      {(approval.previews ?? []).map((preview) => (
        <PreviewBlock key={`${preview.resource}:${preview.path}`} preview={preview} />
      ))}
      {approval.tools.length > 0 ? (
        <p style={{ fontSize: 12, color: "#777", margin: 0 }}>工具：{approval.tools.join("、")}</p>
      ) : null}
      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" onClick={() => decide(true)} disabled={busy}>
          同意
        </button>
        <button type="button" onClick={() => decide(false)} disabled={busy}>
          拒绝
        </button>
      </div>
    </article>
  );
}
