import test from "node:test";
import assert from "node:assert/strict";

import { autofillTitleFromFilename, titleFromFilename } from "../lib/file-attachments";

test("titleFromFilename removes only the final extension", () => {
  assert.equal(titleFromFilename("人工智能简史.pdf"), "人工智能简史");
  assert.equal(titleFromFilename("2027.考研数学一.v2.md"), "2027.考研数学一.v2");
  assert.equal(titleFromFilename("syllabus.TXT"), "syllabus");
});

test("titleFromFilename preserves extensionless and hidden filenames", () => {
  assert.equal(titleFromFilename("考试大纲"), "考试大纲");
  assert.equal(titleFromFilename(".pdf"), ".pdf");
  assert.equal(titleFromFilename("  教材.pdf  "), "教材");
});

test("autofillTitleFromFilename updates empty or previously automatic titles", () => {
  assert.deepEqual(autofillTitleFromFilename("", false, "人工智能简史.pdf"), {
    title: "人工智能简史",
    isAutomatic: true,
  });
  assert.deepEqual(autofillTitleFromFilename("旧教材", true, "新教材.pdf"), {
    title: "新教材",
    isAutomatic: true,
  });
});

test("autofillTitleFromFilename preserves a manually edited title", () => {
  assert.deepEqual(autofillTitleFromFilename("我的教材名称", false, "文件名.pdf"), {
    title: "我的教材名称",
    isAutomatic: false,
  });
});
