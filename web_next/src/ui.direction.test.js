import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const src = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

test("direction hang helpers live in ui.jsx", () => {
  const ui = src("./ui.jsx");
  assert.match(ui, /export function DirectionSelectEmpty/);
  assert.match(ui, /export function DirectionGapAlert/);
  assert.match(ui, /export function DirectionHangChips/);
  assert.match(ui, /export function DirectionDraftList/);
  assert.match(ui, /export function DirectionFilterHint/);
  assert.match(ui, /export async function adoptDirectionDraft/);
  assert.match(ui, /export async function hangSuggestedPapers/);
  assert.match(ui, /采用并挂上/);
  assert.match(ui, /全部采用/);
  assert.match(ui, /按题名挂上/);
  assert.match(ui, /scan_window/);
  assert.match(ui, /名称可改/);
  assert.match(ui, /从文献归纳/);
  assert.match(ui, /挂到/);
});

test("literature and graph empty direction select point to compose", () => {
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /DirectionSelectEmpty/);
  assert.match(lit, /DirectionGapAlert/);
  assert.match(lit, /DirectionHangChips/);
  assert.match(lit, /direction_id: directionId/);
  assert.match(lit, /dirExtra/);
  const graph = src("./pages/Graph.jsx");
  assert.match(graph, /DirectionSelectEmpty/);
  const reader = src("./pages/LiteratureReader.jsx");
  assert.match(reader, /DirectionHangChips/);
  const dirs = src("./pages/Directions.jsx");
  assert.match(dirs, /kwHints/);
  assert.match(dirs, /不会自动建方向/);
  assert.match(dirs, /DirectionDraftList/);
  assert.match(dirs, /DirectionGapAlert/);
  const members = src("./pages/Members.jsx");
  assert.match(members, /DirectionSelectEmpty/);
  assert.match(members, /DirectionFilterHint/);
  assert.match(members, /DirectionGapAlert/);
  const home = src("./pages/Home.jsx");
  assert.match(home, /DirectionGapAlert/);
  assert.match(home, /从文献归纳/);
});
