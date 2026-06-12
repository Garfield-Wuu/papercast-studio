import { useReducer } from "react";

/**
 * Cross-tab review state.
 *
 * P5b layout: 3 tabs.
 *   figures: keyed by figure_id (string)
 *   slides:  keyed by page_no (number) — drives BOTH slides_plan and
 *            script regenerate batches; the UI presents one checkbox
 *            per page so the reviewer doesn't think about the split.
 *   facts:   keyed by fact_card index (number) — flagged items go
 *            into a reading regenerate batch as a `fact_cards` section.
 *
 * `globalFeedback` is the textarea at the bottom of the panel —
 * applied to every regenerate request as a context note.
 */

export type Tab = "figures" | "slides" | "facts";
type ItemKey = string | number;

export interface ReviewItem {
  checked: boolean;
  feedback: string;
}

export interface ReviewState {
  figures: Record<string, ReviewItem>;
  slides: Record<number, ReviewItem>;
  facts: Record<number, ReviewItem>;
  globalFeedback: string;
  /**
   * Slide pages whose JSON / script has been edited via PageEditDialog
   * since the last rebuild. SlidesScriptTab maintains the membership by
   * deep-comparing the current artifact against an initial snapshot;
   * ReviewPanel reads `dirtyPages.size` to enable the "全部重新生成"
   * action. Cleared on successful rebuild.
   */
  dirtyPages: Set<number>;
}

type Action =
  | { type: "toggle"; tab: Tab; key: ItemKey }
  | { type: "feedback"; tab: Tab; key: ItemKey; value: string }
  | { type: "globalFeedback"; value: string }
  | { type: "clearTab"; tab: Tab }
  | { type: "markDirty"; pageNo: number }
  | { type: "clearDirty"; pageNo: number }
  | { type: "clearAllDirty" }
  | { type: "reset" };

const empty: ReviewState = {
  figures: {},
  slides: {},
  facts: {},
  globalFeedback: "",
  dirtyPages: new Set<number>(),
};

function reducer(state: ReviewState, action: Action): ReviewState {
  switch (action.type) {
    case "toggle": {
      const map = { ...(state[action.tab] as Record<string | number, ReviewItem>) };
      const cur = map[action.key as keyof typeof map];
      map[action.key as keyof typeof map] = {
        checked: !cur?.checked,
        feedback: cur?.feedback ?? "",
      };
      return { ...state, [action.tab]: map };
    }
    case "feedback": {
      const map = { ...(state[action.tab] as Record<string | number, ReviewItem>) };
      const cur = map[action.key as keyof typeof map];
      map[action.key as keyof typeof map] = {
        checked: cur?.checked ?? false,
        feedback: action.value,
      };
      return { ...state, [action.tab]: map };
    }
    case "globalFeedback":
      return { ...state, globalFeedback: action.value };
    case "clearTab":
      return { ...state, [action.tab]: {} };
    case "markDirty": {
      if (state.dirtyPages.has(action.pageNo)) return state;
      const next = new Set(state.dirtyPages);
      next.add(action.pageNo);
      return { ...state, dirtyPages: next };
    }
    case "clearDirty": {
      if (!state.dirtyPages.has(action.pageNo)) return state;
      const next = new Set(state.dirtyPages);
      next.delete(action.pageNo);
      return { ...state, dirtyPages: next };
    }
    case "clearAllDirty":
      if (state.dirtyPages.size === 0) return state;
      return { ...state, dirtyPages: new Set<number>() };
    case "reset":
      return empty;
  }
}

export function useReviewState() {
  const [state, dispatch] = useReducer(reducer, empty);

  const itemFor = <K extends ItemKey>(tab: Tab, key: K): ReviewItem => {
    const map = state[tab] as Record<string | number, ReviewItem>;
    return map[key as keyof typeof map] ?? { checked: false, feedback: "" };
  };

  const checkedCount = (tab: Tab): number =>
    Object.values(state[tab] as Record<string, ReviewItem>).filter(
      (v) => v.checked,
    ).length;

  const checkedItems = (tab: Tab): { key: ItemKey; feedback: string }[] => {
    const map = state[tab] as Record<string, ReviewItem>;
    return Object.entries(map)
      .filter(([, v]) => v.checked)
      .map(([key, v]) => ({
        key: tab === "slides" || tab === "facts" ? Number(key) : key,
        feedback: v.feedback,
      }));
  };

  return {
    state,
    toggle: (tab: Tab, key: ItemKey) => dispatch({ type: "toggle", tab, key }),
    setFeedback: (tab: Tab, key: ItemKey, value: string) =>
      dispatch({ type: "feedback", tab, key, value }),
    setGlobalFeedback: (value: string) =>
      dispatch({ type: "globalFeedback", value }),
    clearTab: (tab: Tab) => dispatch({ type: "clearTab", tab }),
    markDirty: (pageNo: number) => dispatch({ type: "markDirty", pageNo }),
    clearDirty: (pageNo: number) => dispatch({ type: "clearDirty", pageNo }),
    clearAllDirty: () => dispatch({ type: "clearAllDirty" }),
    reset: () => dispatch({ type: "reset" }),
    itemFor,
    checkedCount,
    checkedItems,
    totalChecked:
      checkedCount("figures") +
      checkedCount("slides") +
      checkedCount("facts"),
    dirtyCount: state.dirtyPages.size,
    isDirty: (pageNo: number) => state.dirtyPages.has(pageNo),
  };
}
