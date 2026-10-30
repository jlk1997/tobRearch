import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

test("KB declares tab before the auto-ask effect reads it", () => {
  const src = readFileSync(fileURLToPath(new URL("./KB.jsx", import.meta.url)), "utf8");
  const decl = src.indexOf("const tab =");
  const deps = src.indexOf("[tab, presetQ, id]");
  assert.ok(decl >= 0, "tab must be declared");
  assert.ok(deps >= 0, "auto-ask effect must keep [tab, presetQ, id]");
  assert.ok(decl < deps, "const tab must appear before useEffect(..., [tab, ...]) or the page TDZ-crashes");
});
