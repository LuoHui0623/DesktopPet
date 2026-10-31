import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { searchHistory } from "../../service/chat.service";
import { describeRequestError } from "../../service/api-client";
import type { ChatHistoryItem } from "../../api/types";
import { formatClock } from "./clock-view";

const PAGE_SIZE = 50;
const ROLE_LABELS: Record<string, string> = { master: "我", me: "宠物", event: "会话" };

export function HistoryPanel() {
  const [keyword, setKeyword] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [items, setItems] = useState<ChatHistoryItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestRef = useRef(0);

  const fetchPage = useCallback((query: string, offset: number): Promise<void> => {
    requestRef.current += 1;
    const requestId = requestRef.current;
    return searchHistory({ keyword: query || undefined, limit: PAGE_SIZE, offset })
      .then(
        (page) => {
          if (requestId !== requestRef.current) return;
          setItems((previous) => (offset === 0 ? page.items : [...previous, ...page.items]));
          setHasMore(page.has_more);
        },
        (caught: unknown) => {
          if (requestId !== requestRef.current) return;
          setError(describeRequestError(caught));
        },
      )
      .finally(() => {
        if (requestId === requestRef.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    void fetchPage("", 0);
  }, [fetchPage]);

  const startLoad = (query: string, offset: number) => {
    setLoading(true);
    setError(null);
    void fetchPage(query, offset);
  };

  const onSearch = (event: FormEvent) => {
    event.preventDefault();
    const query = keyword.trim();
    setSubmitted(query);
    startLoad(query, 0);
  };

  return (
    <section style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      <form onSubmit={onSearch} style={{ display: "flex", gap: 8, padding: 12 }}>
        <input
          type="search"
          aria-label="搜索历史"
          placeholder="关键词"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ flex: 1, fontSize: 14, padding: 4 }}
        />
        <button type="submit" disabled={loading}>
          搜索
        </button>
      </form>
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "0 12px 12px" }}>
        {error && (
          <p role="alert" style={{ color: "#991b1b", fontSize: 13 }}>
            {error}
          </p>
        )}
        {!loading && !error && items.length === 0 && (
          <p style={{ color: "#777", fontSize: 13 }}>
            {submitted ? "没有匹配的记录" : "暂无历史记录"}
          </p>
        )}
        <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 8 }}>
          {items.map((item) => (
            <li
              key={item.id}
              style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 8, fontSize: 13 }}
            >
              <div style={{ color: "#666", marginBottom: 4 }}>
                {item.date} {formatClock(item.created_at)} · {ROLE_LABELS[item.role] ?? item.role}
              </div>
              <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{item.content}</div>
            </li>
          ))}
        </ul>
        {hasMore && (
          <button
            type="button"
            disabled={loading}
            onClick={() => startLoad(submitted, items.length)}
            style={{ marginTop: 8 }}
          >
            加载更多
          </button>
        )}
      </div>
    </section>
  );
}
