// SPDX-License-Identifier: GPL-3.0-or-later
//
// Keyboard shortcuts of the page (owner's request 2026-11-13): which key does
// what, and the arithmetic behind it. Pure functions; app.ts wires them to the
// controls. No shortcut transmits: PTT stays on left Ctrl + left Alt (app.ts),
// so no shortcut here uses Ctrl, and Ctrl or Meta with any key is left to the
// browser (zoom, copy, tabs).
// Shortcuts shown in the about window, in the order of the owner's request.
export const SHORTCUTS = [
    ["Left Ctrl + left Alt (hold)", "PTT: transmit voice while both are held; also stops FT8/FT4 transmission"],
    ["← →", "tune down / up by the waterfall's step"],
    ["Alt + ← →", "tune by 10 Hz"],
    ["Shift + ← →", "tune by 100 Hz"],
    ["Alt + ↑ ↓", "next band up / down"],
    ["> / <  (or . / ,)", "seek the next station up / down (again: stop)"],
    ["Alt + PgUp / PgDn", "the same"],
    ["F8 / F4", "FT8 / FT4 reception on the current band"],
    ["L", "station log"],
    ["P / A / N", "preamp / attenuator / neither"],
    ["Alt + A", "next AGC setting"],
    ["Alt + N", "noise blanker on / off"],
    ["+ / −", "RF gain up / down"],
    ["Esc", "leave a field, so that the keys work again"],
    ["Audio spectrum: click", "lower edge of the filter (receive: passband; transmit: low cut)"],
    ["Audio spectrum: Shift + click", "upper edge of the filter (receive: passband; transmit: high cut)"],
    ["Receive audio spectrum: right-click", "notch"],
    ["Waterfall: right-click", "RTTY window: tones around the click; CW window: narrow filter around the click"],
];
export const RF_GAIN_STEP = 5; // percent
const AGC_ORDER = ["off", "fast", "slow", "auto"];
// The action of a key, or null for keys the page leaves to the browser.
export function actionFor(e) {
    if (e.ctrlKey || e.metaKey)
        return null;
    const arrow = e.code === "ArrowRight" ? 1 : e.code === "ArrowLeft" ? -1 : 0;
    if (arrow !== 0) {
        if (e.altKey && e.shiftKey)
            return null;
        if (e.altKey)
            return { kind: "tune", direction: arrow, stepHz: 10 };
        if (e.shiftKey)
            return { kind: "tune", direction: arrow, stepHz: 100 };
        return { kind: "tune", direction: arrow, stepHz: null };
    }
    if (e.altKey) {
        if (e.shiftKey)
            return null;
        if (e.code === "ArrowUp")
            return { kind: "band", direction: 1 };
        if (e.code === "ArrowDown")
            return { kind: "band", direction: -1 };
        if (e.code === "PageUp")
            return { kind: "seek", direction: 1 };
        if (e.code === "PageDown")
            return { kind: "seek", direction: -1 };
        if (e.code === "KeyA")
            return { kind: "agc" };
        if (e.code === "KeyN")
            return { kind: "nb" };
        return null; // Alt + F4 closes the window: never taken
    }
    if (e.code === "F8")
        return { kind: "digital", mode: "FT8" };
    if (e.code === "F4")
        return { kind: "digital", mode: "FT4" };
    // The keys of < and > (with or without Shift: the same key on most layouts).
    if (e.code === "Period")
        return { kind: "seek", direction: 1 };
    if (e.code === "Comma")
        return { kind: "seek", direction: -1 };
    if (e.key === "+" || e.key === "=")
        return { kind: "rfGain", direction: 1 };
    if (e.key === "-" || e.key === "_")
        return { kind: "rfGain", direction: -1 };
    if (e.shiftKey)
        return null;
    if (e.code === "KeyL")
        return { kind: "log" };
    if (e.code === "KeyP")
        return { kind: "frontEnd", state: "pre" };
    if (e.code === "KeyA")
        return { kind: "frontEnd", state: "att" };
    if (e.code === "KeyN")
        return { kind: "frontEnd", state: "off" };
    return null;
}
// The next point of a grid of `stepHz` in `direction`; from between two points,
// the nearest one that way.
export function gridStep(hz, stepHz, direction) {
    const below = Math.floor(hz / stepHz) * stepHz;
    if (direction > 0)
        return below + stepHz;
    return below === hz ? hz - stepHz : below;
}
// The band after the one at `hz` in `direction` (bands sorted by frequency); from
// outside every band, the nearest band that way. Null at the end of the list.
export function adjacentBand(bands, hz, direction) {
    const inside = bands.findIndex(([, low, high]) => hz >= low && hz <= high);
    if (inside >= 0)
        return bands[inside + direction] ?? null;
    if (direction > 0)
        return bands.find(([, low]) => low > hz) ?? null;
    return [...bands].reverse().find(([, , high]) => high < hz) ?? null;
}
// The AGC setting after `current` (off, fast, slow, auto, off…).
export function nextAgc(current) {
    const index = AGC_ORDER.indexOf(current);
    return AGC_ORDER[(index + 1) % AGC_ORDER.length];
}
// RF gain one step up or down, on the step grid, within 1-100 %.
export function rfGainStep(percent, direction) {
    const next = gridStep(percent, RF_GAIN_STEP, direction);
    return Math.min(100, Math.max(1, next));
}
// Controls set with the mouse give the focus back to the page (owner's choice
// 2026-11-15), so the shortcuts work again at once: lists, sliders, check
// boxes and buttons, not text or number fields, where one types.
const GIVES_FOCUS_BACK = new Set(["range", "checkbox", "radio", "button", "submit", "reset", "color"]);
export function givesFocusBack(target) {
    if (!(target instanceof HTMLElement) || target.closest("dialog") !== null)
        return false;
    if (target instanceof HTMLInputElement)
        return GIVES_FOCUS_BACK.has(target.type);
    return target instanceof HTMLSelectElement || target instanceof HTMLButtonElement;
}
// Keys typed into a field, a slider, a list or an open dialog belong to it;
// a focused button or check box does not take letters or arrows.
const NOT_TYPING = new Set(["checkbox", "radio", "button", "submit", "reset"]);
export function typingTarget(target) {
    if (!(target instanceof HTMLElement))
        return false;
    if (target.isContentEditable || target.closest("dialog") !== null)
        return true;
    if (target instanceof HTMLInputElement)
        return !NOT_TYPING.has(target.type);
    return target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement;
}
