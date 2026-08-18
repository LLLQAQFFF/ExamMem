import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

function findWebRoot(): string {
  let directory = __dirname;
  for (let depth = 0; depth < 8; depth += 1) {
    if (
      fs.existsSync(path.join(directory, "components", "exam-mem")) &&
      fs.existsSync(path.join(directory, "locales", "en", "app.json"))
    ) {
      return directory;
    }
    directory = path.dirname(directory);
  }
  throw new Error(`could not locate web root from ${__dirname}`);
}

function examMemTranslationKeys(webRoot: string): Set<string> {
  const componentRoot = path.join(webRoot, "components", "exam-mem");
  const keys = new Set<string>();
  const callPattern = /\bt\(\s*"((?:[^"\\]|\\.)*)"/gs;

  for (const entry of fs.readdirSync(componentRoot, { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".tsx")) continue;
    const source = fs.readFileSync(path.join(componentRoot, entry.name), "utf8");
    for (const match of source.matchAll(callPattern)) {
      keys.add(JSON.parse(`"${match[1]}"`) as string);
    }
  }

  return keys;
}

const webRoot = findWebRoot();
const english = JSON.parse(
  fs.readFileSync(path.join(webRoot, "locales", "en", "app.json"), "utf8"),
) as Record<string, string>;
const chinese = JSON.parse(
  fs.readFileSync(path.join(webRoot, "locales", "zh", "app.json"), "utf8"),
) as Record<string, string>;

test("every static ExamMem UI string exists in both interface languages", () => {
  const keys = [...examMemTranslationKeys(webRoot)].sort();
  const missingEnglish = keys.filter((key) => !(key in english));
  const missingChinese = keys.filter((key) => !(key in chinese));

  assert.deepEqual(missingEnglish, []);
  assert.deepEqual(missingChinese, []);
});

test("ExamMem navigation and derived issue labels have Chinese copy", () => {
  const expected = {
    "Smart Exam Prep": "智能备考",
    "Learning Paths": "学习路径",
    Practice: "练习",
    "Learning Memory": "学习记忆",
    "Learning Profile": "学习画像",
    "Review Center": "复习中心",
    "Exam Review": "考试复盘",
    "Memory Issues": "记忆问题",
    Configuration: "配置",
    "Workflow failure": "工作流故障",
    "Grade disputed": "评分存在异议",
    "Memory inaccurate": "记忆内容不准确",
    "Contested evidence": "存在争议的证据",
    "Projection pending": "投影待处理",
  };

  for (const [key, value] of Object.entries(expected)) {
    assert.equal(chinese[key], value, key);
  }
});
