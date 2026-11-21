// SPDX-License-Identifier: GPL-3.0-or-later
//
// Movable and resizable windows (owner's request 2026-11-10): the station log,
// FT8/FT4, the system log, the resources and the error window. Dragged by the
// title bar, resized from the bottom right corner, brought to the front when
// touched; a double click on the title puts the window back where it was by
// default. Place and size are kept on the account. Only the page's look: no
// request to the server.

export interface WindowPlace {
  left: number;
  top: number;
  width: number;
  height: number;
}

// So much of a window's title bar always stays on the screen, to grab it again.
export const TITLE_KEEP_PX = 60;
export const TITLE_BAR_PX = 32;
export const MIN_WIDTH_PX = 200;
export const MIN_HEIGHT_PX = 80;

const clamp = (value: number, low: number, high: number): number => Math.min(Math.max(value, low), Math.max(low, high));

// A place that fits a view of viewWidth x viewHeight CSS pixels.
export function clampPlace(place: WindowPlace, viewWidth: number, viewHeight: number): WindowPlace {
  const width = clamp(place.width, MIN_WIDTH_PX, viewWidth);
  const height = clamp(place.height, MIN_HEIGHT_PX, viewHeight);
  return {
    width,
    height,
    left: clamp(place.left, TITLE_KEEP_PX - width, viewWidth - TITLE_KEEP_PX),
    top: clamp(place.top, 0, viewHeight - TITLE_BAR_PX),
  };
}

// A place kept as text; null for anything unusable (the default place then).
export function parsePlace(text: string | null): WindowPlace | null {
  if (text === null || text === "") return null;
  try {
    const v = JSON.parse(text) as Partial<WindowPlace>;
    const ok = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
    if (!ok(v.left) || !ok(v.top) || !ok(v.width) || !ok(v.height) || v.width <= 0 || v.height <= 0) return null;
    return { left: v.left, top: v.top, width: v.width, height: v.height };
  } catch {
    return null;
  }
}

// A corner this large at the bottom right is the resize grip.
const GRIP_PX = 18;
// The windows' stacking: each touch puts one above the others. The error window
// stays above all of them (style.css).
let front = 30;

export function bringToFront(box: HTMLElement): void {
  box.style.zIndex = String(++front);
}

// Makes ``box`` movable by ``handle`` and resizable; ``load`` and ``save`` keep
// its place (null: the default place).
export function makeFloating(
  box: HTMLElement,
  handle: HTMLElement,
  load: () => string | null,
  save: (text: string | null) => void,
): void {
  box.classList.add("floating");
  // The place the operator chose (null: the default one). Kept apart from what
  // is on the screen: a hidden window measures 0, and a smaller browser window
  // only squeezes it for a while.
  let chosen: WindowPlace | null = null;
  // The size last written by this code: a different one in the style was written
  // by the browser's resize grip, i.e. by the operator.
  let size = "";
  const place = (p: WindowPlace): void => {
    chosen = p;
    const fitted = clampPlace(p, window.innerWidth, window.innerHeight);
    Object.assign(box.style, {
      left: `${fitted.left}px`,
      top: `${fitted.top}px`,
      width: `${fitted.width}px`,
      height: `${fitted.height}px`,
      right: "auto",
      bottom: "auto",
      transform: "none",
    });
    size = `${box.style.width}x${box.style.height}`;
    box.classList.add("placed");
  };
  const current = (): WindowPlace => {
    const r = box.getBoundingClientRect();
    return { left: r.left, top: r.top, width: r.width, height: r.height };
  };
  const remember = (): void => save(JSON.stringify(current()));
  const saved = parsePlace(load());
  if (saved !== null) place(saved);

  box.addEventListener("pointerdown", (e) => {
    bringToFront(box);
    const r = box.getBoundingClientRect();
    if (e.clientX > r.right - GRIP_PX && e.clientY > r.bottom - GRIP_PX) {
      // The resize grip: from now on the size is the operator's (no default limits).
      place(current());
    }
  });
  // The browser's own resizing writes the size into the element's style; content
  // that grows on its own does not, and is not remembered as a size.
  new ResizeObserver(() => {
    const now = `${box.style.width}x${box.style.height}`;
    if (now !== size && box.classList.contains("placed") && !box.hidden) {
      size = now;
      chosen = current();
      remember();
    }
  }).observe(box);

  let drag: { x: number; y: number; from: WindowPlace; moved: boolean } | null = null;
  handle.addEventListener("pointerdown", (e) => {
    if ((e.target as HTMLElement).closest("button, input, select, a") !== null) return;
    e.preventDefault();
    drag = { x: e.clientX, y: e.clientY, from: current(), moved: false };
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener("pointermove", (e) => {
    if (drag === null) return;
    if (!drag.moved && Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 3) return;
    drag.moved = true; // a click on the title is not a move: the default place stays
    place({ ...drag.from, left: drag.from.left + e.clientX - drag.x, top: drag.from.top + e.clientY - drag.y });
  });
  const drop = (): void => {
    if (drag === null) return;
    const moved = drag.moved;
    drag = null;
    if (moved) remember();
  };
  handle.addEventListener("pointerup", drop);
  handle.addEventListener("pointercancel", drop);
  handle.addEventListener("dblclick", (e) => {
    if ((e.target as HTMLElement).closest("button, input, select, a") !== null) return;
    for (const key of ["left", "top", "width", "height", "right", "bottom", "transform"] as const) {
      box.style[key] = "";
    }
    box.classList.remove("placed");
    chosen = null;
    size = "";
    save(null);
  });
  // A window that opens comes to the front (an error over everything else), and
  // fitted to the browser window as it is now.
  new MutationObserver(() => {
    if (box.hidden) return;
    bringToFront(box);
    if (chosen !== null) place(chosen);
  }).observe(box, { attributes: true, attributeFilter: ["hidden"] });
  // A smaller browser window: the title bar must stay reachable.
  window.addEventListener("resize", () => {
    if (chosen !== null && !box.hidden) place(chosen);
  });
}
