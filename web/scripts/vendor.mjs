// SPDX-License-Identifier: GPL-3.0-or-later
//
// Copies third-party browser code next to the compiled page (decisions.md D36,
// D52): the FT8/FT4 decoder ft8ts and its licence. Run by `npm run build`, so
// CI checks that the committed copy matches the pinned version.

import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(web, "node_modules", "@e04", "ft8ts");
const target = join(web, "..", "src", "remoteg90ctl", "server", "static", "vendor", "ft8ts");
mkdirSync(target, { recursive: true });
// The source map is not shipped; drop the comment that points at it.
const code = readFileSync(join(source, "dist", "ft8ts.mjs"), "utf8").replace(/\n\/\/# sourceMappingURL=.*\n?$/, "\n");
writeFileSync(join(target, "ft8ts.mjs"), code);
copyFileSync(join(source, "LICENSE"), join(target, "LICENSE"));
