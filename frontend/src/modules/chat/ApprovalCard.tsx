import { decideApproval } from "../../service/chat.service";
import type { PendingApproval } from "../../service/stores/chat.store";

interface ApprovalCardProps {
  approval: PendingApproval;
  busy: boolean;
}

export function ApprovalCard({ approval, busy }: ApprovalCardProps) {
  const decide = (approved: boolean) => {
    void decideApproval(approval.approval_id, approved);
  };

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
      <header style={{ fontSize: 13, fontWeight: 600 }}>待审批：{approval.capability}</header>
      <p style={{ fontSize: 13, margin: 0 }}>任务：{approval.task}</p>
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
