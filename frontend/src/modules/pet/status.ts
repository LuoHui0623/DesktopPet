import type { PetState } from "../../api/types";

export const PET_STATE_LABELS: Record<PetState, string> = {
  idle: "待机",
  thinking: "思考",
  speaking: "说话",
  executing: "执行中",
  sleeping: "休眠",
};

export const DISCONNECTED_LABEL = "未连接";
export const PENDING_LABEL = "连接中";

export function petStatusLabel(connected: boolean, status: PetState | null): string {
  if (!connected) return DISCONNECTED_LABEL;
  if (status === null) return PENDING_LABEL;
  return PET_STATE_LABELS[status];
}
