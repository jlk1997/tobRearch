import assert from "node:assert/strict";
import test from "node:test";
import { rectsForQuote } from "./pdfText.js";

test("quote on the page becomes a rectangle", () => {
  const items = [
    { str: "hello ", transform: [1, 0, 0, 1, 10, 100], width: 20, height: 8 },
    { str: "watermark", transform: [1, 0, 0, 1, 40, 100], width: 40, height: 8 },
    { str: " end", transform: [1, 0, 0, 1, 90, 40], width: 16, height: 8 },
  ];
  const rects = rectsForQuote(items, "watermark");
  assert.equal(rects.length, 1);
  assert.ok(rects[0].w > 0);
  assert.ok(rects[0].h > 0);
  assert.equal(rectsForQuote(items, "不存在的句子").length, 0);
});
