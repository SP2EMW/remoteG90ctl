// SPDX-License-Identifier: GPL-3.0-or-later
//
// Number fields with arrows in place of sliders (owner's request 2026-11-21):
// the filters and every value in dB. A value takes effect on the arrows (the
// field's spinner, Up/Down, PgUp/PgDn) at once, and when typed on Enter or on
// leaving the field; Escape or an empty or unreadable entry puts back the last
// value. Typing alone applies nothing, so "3000" never passes through 3, 30
// and 300 on its way.
// The typed text as a value of the field: clamped to min..max and rounded to
// the step (counted from min); null when it is not a number.
export function fieldValue(text, min, max, step) {
    const trimmed = text.trim().replace(",", ".");
    if (trimmed === "")
        return null;
    const n = Number(trimmed);
    if (!Number.isFinite(n))
        return null;
    const clamped = Math.min(max, Math.max(min, n));
    if (!(step > 0))
        return clamped;
    const stepped = min + Math.round((clamped - min) / step) * step;
    // Rounding may step past max (e.g. max not on the grid): stay inside.
    return Math.min(max, Math.max(min, Number(stepped.toFixed(6))));
}
const STEP_KEYS = new Set(["ArrowUp", "ArrowDown", "PageUp", "PageDown"]);
// Wires a number field: apply(value) on every committed change.
export function numberField(input, apply) {
    let last = input.value;
    let stepping = false;
    const bound = (name, fallback) => {
        const v = Number(input[name]);
        return input[name] === "" || !Number.isFinite(v) ? fallback : v;
    };
    const commit = () => {
        const step = input.step === "" || input.step === "any" ? 1 : Number(input.step);
        const value = fieldValue(input.value, bound("min", -Infinity), bound("max", Infinity), step);
        if (value === null) {
            input.value = last;
            return;
        }
        input.value = String(value);
        last = input.value;
        apply(value);
    };
    // The page sets the value (a preset, the radio, auto levels): the new base.
    input.addEventListener("focus", () => (last = input.value));
    input.addEventListener("keydown", (e) => {
        if (STEP_KEYS.has(e.key)) {
            stepping = true;
        }
        else if (e.key === "Enter") {
            commit();
        }
        else if (e.key === "Escape") {
            input.value = last;
            input.blur();
        }
    });
    // The spinner's arrows: a pointer on the field that changes the value.
    input.addEventListener("pointerdown", () => (stepping = true));
    input.addEventListener("pointerup", () => window.setTimeout(() => (stepping = false), 0));
    input.addEventListener("input", () => {
        if (stepping)
            commit();
    });
    input.addEventListener("keyup", (e) => {
        if (STEP_KEYS.has(e.key))
            stepping = false;
    });
    input.addEventListener("change", commit);
}
