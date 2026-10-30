import { strict as assert } from "node:assert";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { ensureHeading, joinManuscript, manuscriptSectionHref, nextEmptySection, parseManuscript, sectionBody, suggestCiteKey, writingDeskHref } from "./manuscriptSections.js";

test("edit one section and keep the others", () => {
  const md = "开头\n\n## 相关工作\n\n甲\n\n## 方法\n\n乙\n";
  const parsed = parseManuscript(md);
  assert.equal(parsed.focused, true);
  assert.equal(parsed.sections[0].key, "相关工作");
  assert.equal(parsed.sections[0].body, "甲");
  assert.equal(parsed.sections[1].body, "乙");
  const next = joinManuscript(parsed, parsed.sections[0].id, "甲甲");
  const again = parseManuscript(next);
  assert.equal(again.sections[0].body, "甲甲");
  assert.equal(again.sections[1].body, "乙");
  assert.match(next, /## 方法/);
});

test("no target headings stay one document until related work is added", () => {
  const plain = "整篇还没有标题";
  assert.equal(parseManuscript(plain).focused, false);
  const added = ensureHeading(plain, "相关工作");
  const parsed = parseManuscript(added);
  assert.equal(parsed.focused, true);
  assert.equal(sectionBody(added, "相关工作"), "");
  assert.equal(ensureHeading(added, "相关工作"), added);
});

test("next section is the earliest empty one, unless it is already open", () => {
  const md = "## 相关工作\n\n甲\n\n## 方法\n\n";
  const parsed = parseManuscript(md);
  assert.deepEqual(nextEmptySection(parsed, "相关工作"), { name: "方法", exists: true, id: parsed.sections[1].id });
  assert.equal(nextEmptySection(parseManuscript("## 相关工作\n\n"), "相关工作"), null);
  const all = parseManuscript("## 相关工作\n\n甲\n\n## 方法\n\n乙\n\n## 实验\n\n丙\n");
  assert.equal(nextEmptySection(all, "实验"), null);
});
test("hash heading still receives the new paragraph in that section", () => {
  const md = "# 相关工作\n\n甲\n";
  const parsed = parseManuscript(md);
  assert.equal(parsed.sections[0].headingLine, "# 相关工作");
  const next = joinManuscript(parsed, parsed.sections[0].id, "甲\n\n乙");
  const again = parseManuscript(next);
  assert.equal(again.sections.length, 1);
  assert.match(again.sections[0].body, /乙/);
  assert.equal(again.sections[0].headingLine, "# 相关工作");
});
test("cite key stays short and unique", () => {
  assert.equal(suggestCiteKey({ authors: "Ada Lovelace", year: 2018 }), "Lovelace2018");
  assert.equal(suggestCiteKey({ authors: "Ada Lovelace", publish_year: 2018 }, [{ citation_key: "Lovelace2018" }]), "Lovelace20182");
  assert.equal(suggestCiteKey({ authors: "张三", title: "注意力机制", year: 2020 }), "注意力机制2020");
});

test("writing links keep the section", () => {
  assert.equal(
    writingDeskHref("/labs/l/writing?ms=m", "l", "m", "相关工作"),
    "/labs/l/writing?ms=m&heading=%E7%9B%B8%E5%85%B3%E5%B7%A5%E4%BD%9C"
  );
  assert.equal(writingDeskHref("/labs/l/writing?ms=m&heading=方法", "l", "m", "相关工作"), "/labs/l/writing?ms=m&heading=方法");
  assert.equal(manuscriptSectionHref("m", "相关工作"), "/manuscripts/m?section=%E7%9B%B8%E5%85%B3%E5%B7%A5%E4%BD%9C");
});

test("manuscript desk copy", () => {
  const ms = readFileSync(new URL("./pages/Manuscripts.jsx", import.meta.url), "utf8");
  const writing = readFileSync(new URL("./pages/Writing.jsx", import.meta.url), "utf8");
  const lang = readFileSync(new URL("./pages/Language.jsx", import.meta.url), "utf8");
  assert.match(ms, /还没写到/);
  assert.match(ms, /参考文献/);
  assert.match(ms, /意见/);
  assert.match(ms, /帮看结构/);
  assert.match(ms, /以前的版本/);
  assert.match(ms, /加上相关工作这一节/);
  assert.match(ms, /帮我写这一节/);
  assert.match(ms, /换成这版/);
  assert.match(ms, /已换成这版/);
  assert.match(ms, /这一节还是空的/);
  assert.match(ms, /论文已建。先写相关工作这一节。/);
  assert.match(ms, /写进相关工作/);
  assert.match(ms, /去补实验/);
  assert.match(ms, /改这段/);
  assert.match(ms, /译这段/);
  assert.match(ms, /接着写/);
  assert.match(ms, /记一条意见/);
  assert.match(ms, /钉在这一段/);
  assert.match(ms, /已经写进这一节/);
  assert.match(ms, /加进这篇/);
  assert.match(ms, /引用写成/);
  assert.match(ms, /这一节可以引用/);
  const reader = readFileSync(new URL("./pages/reader/ReaderToolbar.jsx", import.meta.url), "utf8");
  assert.match(reader, /加进这篇/);
  assert.match(ms, /每篇的依据/);
  assert.match(ms, /看整篇/);
  assert.match(writing, /选好文献，写进这一节/);
  assert.match(writing, /其他写法/);
  assert.match(writing, /这一节已经有文字/);
  assert.match(writing, /正在帮这篇论文写/);
  assert.match(writing, /先建论文/);
  assert.match(lang, /正在改这篇论文的/);
  assert.match(lang, /写进这一节/);
  assert.match(lang, /语言工作台/);
});
