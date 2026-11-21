-- SPDX-License-Identifier: GPL-3.0-or-later
--
-- Keep PipeWire away from the I/Q card: the remoteg90ctl server opens it
-- directly through ALSA (decisions.md D35). WirePlumber 0.4 (Lua) syntax; for
-- WirePlumber 0.5 use 51-remoteg90ctl-iq.conf instead.
--
-- Install for the desktop user:
--   mkdir -p ~/.config/wireplumber/main.lua.d
--   cp deploy/wireplumber/51-remoteg90ctl-iq.lua ~/.config/wireplumber/main.lua.d/
--   systemctl --user restart wireplumber
table.insert(alsa_monitor.rules, {
  matches = {
    { { "device.name", "matches", "alsa_card.usb-Speed_Dragon_USB_Advanced_Audio_Device*" } },
  },
  apply_properties = {
    ["device.disabled"] = true,
  },
})
