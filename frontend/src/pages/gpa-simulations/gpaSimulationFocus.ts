export const GPA_FOCUS_INVALIDATION_EVENT = "imtegrale:gpa-focus-invalidation";

export type GpaFocusIntent = Readonly<{
  generation: number;
  targetId: string;
}>;

export function createGpaFocusIntentController() {
  let generation = 0;

  return {
    create(targetId: string): GpaFocusIntent {
      generation += 1;
      return { generation, targetId };
    },
    invalidate(): void {
      generation += 1;
    },
    restore(intent: GpaFocusIntent): boolean {
      if (intent.generation !== generation) return false;
      return restoreCurrentGpaFocus(intent.targetId);
    },
  };
}

export function invalidatePendingGpaFocus(): void {
  document.dispatchEvent(new Event(GPA_FOCUS_INVALIDATION_EVENT));
}

export function restoreCurrentGpaFocus(targetId: string): boolean {
  const target = document.getElementById(targetId);
  if (!(target instanceof HTMLElement) || !target.isConnected) return false;
  if (target.hidden || target.closest("[hidden], [aria-hidden='true']")) return false;
  if (target.matches(":disabled, [inert], [aria-disabled='true']")) return false;
  const style = window.getComputedStyle(target);
  if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") return false;

  if (document.activeElement !== target) target.focus({ preventScroll: true });
  if (document.activeElement !== target) return false;
  target.scrollIntoView?.({ block: "nearest", behavior: "auto" });
  return true;
}
