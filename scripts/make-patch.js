// make-patch.js
// 使い方: node .github/scripts/make-patch.js "<issue_title>" "<issue_body>" > ai.patch
import fs from "node:fs";
import path from "node:path";
import { GoogleGenerativeAI } from "@google/genai";

const [ISSUE_TITLE = "", ISSUE_BODY = ""] = process.argv.slice(2);
const API_KEY = process.env.GEMINI_API_KEY;
if (!API_KEY) {
  console.error("GEMINI_API_KEY is not set.");
  process.exit(1);
}

// リポジトリの軽量コンテキスト（READMEとpackage.jsonなど）を少量だけ渡す
function safeRead(p, limit = 6000) {
  try {
    const s = fs.readFileSync(p, "utf8");
    return s.slice(0, limit);
  } catch {
    return "";
  }
}

const repoContextPieces = [];
["README.md", "README", "package.json"].forEach((f) => {
  const p = path.resolve(process.cwd(), f);
  const c = safeRead(p);
  if (c) repoContextPieces.push(`== ${f} ==\n${c}`);
});
const repoContext = repoContextPieces.join("\n\n").slice(0, 12000);

const constraints = `
- 出力は **統一diff (unified patch)** のみ。説明文やコードブロック記号は不要。
- 既存ファイルの変更は '--- <old>' と '+++ <new>'、新規ファイルは '/dev/null' からの追加で表現。
- .git や隠しファイル、依存ロック、ビルド生成物は変更しない。
- 変更は最小限に。コンパイル/テストが通る範囲で。
`;

const prompt = `
あなたは熟練エンジニアです。次の課題を解決するための最小変更パッチを作ってください。

[課題タイトル]
${ISSUE_TITLE}

[課題詳細]
${ISSUE_BODY}

[リポジトリ概要（一部）]
${repoContext || "(情報なし)"}

[要件]
${constraints}

必ず **統一diffのみ** を出力してください。
`;

const client = new GoogleGenerativeAI({ apiKey: API_KEY });
const model = client.getGenerativeModel({ model: "gemini-2.5-pro" });

// 生成
const res = await model.generateContent(prompt);
let text = res.response.text().trim();

// フェンス付きで返る場合の除去
const fence = text.match(/```(?:diff|patch)?\s*([\s\S]*?)```/i);
if (fence) text = fence[1].trim();

// diff ヘッダがなければ何も出さない（保守的）
const looksLikeDiff =
  /^(\-\-\- |\+\+\+ |diff --git )/m.test(text) || /^@@\s*-\d+,\d+ \+\d+,\d+/m.test(text);

if (!looksLikeDiff) {
  // 念のためエコー
  console.error("Model did not return a unified diff.");
  process.exit(0);
}

// 出力
process.stdout.write(text.endsWith("\n") ? text : text + "\n");
