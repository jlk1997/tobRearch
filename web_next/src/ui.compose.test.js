import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const src = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

test("CreateDialog and compose query helper live in ui.jsx", () => {
  const ui = src("./ui.jsx");
  assert.match(ui, /export function useComposeOpen/);
  assert.match(ui, /export function CreateDialog/);
  assert.match(ui, /className="fs-create-modal"/);
  assert.match(ui, /destroyOnClose/);
});

test("list pages create from PageHeader extra into CreateDialog", () => {
  const checks = [
    ["./pages/Datasets.jsx", "登记数据集"],
    ["./pages/Manuscripts.jsx", "新建我的论文"],
    ["./pages/Runs.jsx", "创建文献调研"],
    ["./pages/Runs.jsx", "开始复现"],
    ["./pages/Directions.jsx", "新建方向"],
    ["./pages/Collab.jsx", "添加任务"],
    ["./pages/Collab.jsx", "写纪要"],
    ["./pages/Members.jsx", "邀请加入"],
    ["./pages/Literature.jsx", "入库"],
    ["./pages/KB.jsx", "锁定术语"],
    ["./pages/Admin.jsx", "新建空库"],
  ];
  for (const [file, label] of checks) {
    const text = src(file);
    assert.ok(text.includes("CreateDialog"), `${file} must use CreateDialog`);
    assert.ok(text.includes(label), `${file} must keep button copy ${label}`);
    assert.ok(text.includes("extra="), `${file} must put the action on PageHeader extra`);
  }
  const lit = src("./pages/Literature.jsx");
  assert.match(lit, /ingestOpen = tabParam === "manual"/);
  assert.ok(!lit.includes("fs-lit-ingest"), "literature ingest is a modal, not a collapse");
});
