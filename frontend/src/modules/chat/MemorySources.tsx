import { useState } from "react";
import type { MemorySourceRef } from "../../api/types";
import { sourceHeading } from "./source-view";

const chipStyle = {
  padding: "2px 8px",
  fontSize: 12,
  borderRadius: 10,
  border: "1px solid #c7d2fe",
  background: "#eef2ff",
  color: "#3730a3",
  cursor: "pointer",
} as const;

export function MemorySources({ sources }: { sources: MemorySourceRef[] }) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  if (sources.length === 0) return null;
  const open = openIndex === null ? null : (sources[openIndex] ?? null);

  return (
    <div style={{ display: "grid", gap: 6 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {sources.map((source, index) => (
          <button
            key={`${index}-${source.id}`}
            type="button"
            aria-expanded={openIndex === index}
            onClick={() => setOpenIndex(openIndex === index ? null : index)}
            style={chipStyle}
          >
            {sourceHeading(source)}
          </button>
        ))}
      </div>
      {open && (
        <div
          role="region"
          aria-label="记忆来源"
          style={{
            border: "1px solid #c7d2fe",
            borderRadius: 8,
            padding: 8,
            fontSize: 12,
            background: "#fff",
            display: "grid",
            gap: 4,
          }}
        >
          <div style={{ color: "#555" }}>{sourceHeading(open)}</div>
          <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{open.text}</div>
          <div style={{ color: "#999", fontSize: 11 }}>标识：{open.id}</div>
        </div>
      )}
    </div>
  );
}
