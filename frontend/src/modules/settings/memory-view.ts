export interface NumberedLine {
  line: number;
  text: string;
}

export function numberedLines(text: string): NumberedLine[] {
  if (text === "") return [];
  const body = text.endsWith("\n") ? text.slice(0, -1) : text;
  return body.split("\n").map((line, index) => ({ line: index + 1, text: line }));
}

export function allDates(dailyMemory: Record<string, string[]>): string[] {
  return Object.values(dailyMemory).flat().sort().reverse();
}
