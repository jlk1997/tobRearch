import { strict as assert } from "node:assert";
import { test } from "node:test";
import { itemText, methodItems, methodSections } from "./methodCard.js";

test("prefers said when grounded text was cleared", () => {
  assert.equal(itemText({ text: "", said: "we use a residual detector", ungrounded: true }), "we use a residual detector");
});

test("never stringifies empty ungrounded blocks", () => {
  assert.equal(itemText({ text: "", pages: [], ungrounded: true }), "");
  assert.equal(methodItems({ text: "", pages: [], ungrounded: true }).length, 0);
});

test("lifts claim/support into a readable sentence", () => {
  const items = methodItems([
    {
      claim: "Small objects are sparsely distributed and locally clustered.",
      support: "VisDrone images occupy 8.1% of pixels.",
      pages: [1],
    },
  ]);
  assert.equal(items.length, 1);
  assert.match(items[0].text, /Small objects are sparsely distributed/);
  assert.match(items[0].text, /VisDrone/);
  assert.equal(items[0].text.includes("{"), false);
  assert.equal(items[0].ungrounded, true);
  assert.deepEqual(items[0].pages, [1]);
});

test("sections skip empty problem and keep claims", () => {
  const secs = methodSections({
    problem: { text: "", pages: [], ungrounded: true },
    claims: [{ claim: "Existing filter-then-detect wastes computation.", pages: [1] }],
    warning: "摘录缺少原文依据",
    source: "pdf",
  });
  assert.equal(secs.some((s) => s.key === "problem"), false);
  const claims = secs.find((s) => s.key === "claims");
  assert.ok(claims);
  assert.equal(claims.items[0].text.includes("filter-then-detect"), true);
  assert.equal(JSON.stringify(secs).includes("ungrounded"), true);
  assert.equal(JSON.stringify(secs).includes('"text":""'), false);
});
