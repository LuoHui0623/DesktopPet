import type { MemoryEntryView, TagPathChange } from "../../api/types";

export function allDates(dailyMemory: Record<string, string[]>): string[] {
  return Object.values(dailyMemory).flat().sort().reverse();
}

/** 有序 tags 路径的展示写法；tag 只含中英文数字，分隔符不会与 tag 内容冲突。 */
export function pathLabel(path: string[]): string {
  return path.join(" / ");
}

/** 把设置页输入的路径文本解析为有序 tags：按 / 切分、去空白、丢空段；去重与校验仍由后端统一执行。 */
export function parseTagsInput(text: string): string[] {
  return text
    .split("/")
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0);
}

export interface EntryGroup {
  path: string[];
  entries: MemoryEntryView[];
}

/** 按条目自身的有序 tags 路径分组（Q25：不另建分类树）；保留首次出现顺序，含停用条目。 */
export function groupEntriesByPath(entries: MemoryEntryView[]): EntryGroup[] {
  const groups = new Map<string, EntryGroup>();
  for (const entry of entries) {
    const key = JSON.stringify(entry.tags);
    const existing = groups.get(key);
    if (existing) {
      existing.entries.push(entry);
    } else {
      groups.set(key, { path: [...entry.tags], entries: [entry] });
    }
  }
  return [...groups.values()];
}

export interface AnnotatedPathChange extends TagPathChange {
  /** 一级标题变化：有序路径首元素不同。 */
  titleChanged: boolean;
  /** 去重折叠：合并后路径长度收缩。 */
  folded: boolean;
}

/** 据后端给出的确定性 before/after 路径标注标题变化与去重折叠（Q27/第 29 条）。 */
export function annotatePathChange(change: TagPathChange): AnnotatedPathChange {
  return {
    ...change,
    titleChanged: change.before[0] !== change.after[0],
    folded: change.after.length < change.before.length,
  };
}
