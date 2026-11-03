export interface PetHitState {
  /** 按压命中后锁定；返回是否命中（命中即进入拖动候选）。 */
  down: (x: number, y: number) => boolean;
  /** 未锁定时重新判定悬停；锁定期间不再查询掩码。 */
  move: (x: number, y: number) => boolean;
  enter: (x: number, y: number) => boolean;
  leave: () => void;
  up: () => void;
  isLocked: () => boolean;
  isHovered: () => boolean;
}

/**
 * 交互命中状态机：按压命中即锁定，拖动持续到 pointerup / pointercancel；
 * 锁定期间换帧、指针移出或穿透开关变化都不重新判定。
 */
export function createPetHitState(isHit: (x: number, y: number) => boolean): PetHitState {
  let locked = false;
  let hovered = false;

  return {
    down: (x, y) => {
      if (locked) return true;
      hovered = isHit(x, y);
      if (hovered) locked = true;
      return hovered;
    },
    move: (x, y) => {
      if (locked) return true;
      hovered = isHit(x, y);
      return hovered;
    },
    enter: (x, y) => {
      if (locked || hovered) return true;
      hovered = isHit(x, y);
      return hovered;
    },
    leave: () => {
      if (locked) return;
      hovered = false;
    },
    up: () => {
      locked = false;
    },
    isLocked: () => locked,
    isHovered: () => hovered,
  };
}
