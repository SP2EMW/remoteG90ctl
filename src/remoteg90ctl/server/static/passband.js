// SPDX-License-Identifier: GPL-3.0-or-later
//
// A click on the receive audio spectrum (owner's request 2026-11-15): the
// passband with its lower or upper audio edge at the clicked frequency. The
// side comes from the mode, not from the passband's signs (a passband slid
// across the dial must not decide it): audio is below the dial in LSB, L-D and
// CW-R (negative offsets), above it in USB, U-D and CW; in AM and FM the upper
// edge sets a symmetric width and the lower one does nothing (null).
const BELOW = new Set(["LSB", "L-D", "CW-R"]);
const ABOVE = new Set(["USB", "U-D", "CW"]);
export const STEP_HZ = 50;
export const LIMIT_HZ = 6000; // the sliders' range
export function passbandWithEdge(mode, band, edge, audioHz) {
    const hz = Math.round(audioHz / STEP_HZ) * STEP_HZ;
    let [low, high] = band;
    if (BELOW.has(mode)) {
        [low, high] = [Math.min(low, -STEP_HZ), Math.min(high, 0)];
        if (edge === "low")
            high = -hz;
        else
            low = -hz;
    }
    else if (ABOVE.has(mode)) {
        [low, high] = [Math.max(low, 0), Math.max(high, STEP_HZ)];
        if (edge === "low")
            low = hz;
        else
            high = hz;
    }
    else if (edge === "high") {
        [low, high] = [-hz, hz];
    }
    else {
        return null;
    }
    if (low >= high) {
        // An edge clicked beyond the other one: the other one moves along. The
        // lower offset was set when the lower audio edge was clicked above the
        // dial, or the upper one below it.
        const lowerOffsetSet = (edge === "low") !== BELOW.has(mode);
        if (lowerOffsetSet)
            high = low + STEP_HZ;
        else
            low = high - STEP_HZ;
    }
    low = Math.max(-LIMIT_HZ, Math.min(LIMIT_HZ - STEP_HZ, low));
    high = Math.max(low + STEP_HZ, Math.min(LIMIT_HZ, high));
    return [low, high];
}
