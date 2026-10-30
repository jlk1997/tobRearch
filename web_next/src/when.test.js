import { strict as assert } from "node:assert";
import { test } from "node:test";
import { fmtWhen, parseWhen } from "./when.js";

test("UTC instant becomes local clock", () => {
  const d = parseWhen("2026-09-20T07:15:00+00:00");
  assert.ok(d);
  assert.equal(d.toISOString(), "2026-09-20T07:15:00.000Z");
  const shown = fmtWhen("2026-09-20T07:15:00+00:00");
  const local = new Date("2026-09-20T07:15:00+00:00");
  const expectHm = `${String(local.getHours()).padStart(2, "0")}:${String(local.getMinutes()).padStart(2, "0")}`;
  assert.match(shown, new RegExp(`${expectHm}$`));
  assert.equal(shown.includes("T") || shown.includes("Z"), false);
});

test("naive ISO is treated as UTC", () => {
  const a = parseWhen("2026-09-20T07:15:12.345678");
  const b = parseWhen("2026-09-20T07:15:12.345678Z");
  assert.equal(a.getTime(), b.getTime());
});

test("date-only stays a calendar day", () => {
  assert.equal(fmtWhen("2026-09-01"), "2026-09-01");
});

test("empty is an em dash", () => {
  assert.equal(fmtWhen(""), "—");
  assert.equal(fmtWhen(null), "—");
});
