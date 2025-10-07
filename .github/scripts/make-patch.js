// make-patch.js
// Usage: node .github/scripts/make-patch.js "<issue_title>" "<issue_body>" > ai.patch
import fs from "node:fs";
import path from "node:path";
import { GoogleGenerativeAI } from "@google/genai";

const [ISSUE_TITLE = "", ISSUE_BODY = ""] = process.argv.slice(2);
const API_KEY = process.env.GEMINI_API_KEY;
if (!API_KEY) {
  console.error("GEMINI_API_KEY is not set.");
  process.exit(1);
}

// 軽い文脈（大きすぎるとトークン超過になるため一部だけ）
function safeRead(p, limit = 8000) {
  try {
    return fs.readFileSync(p, "utf8").slice(0, limit);
  } catch {
    return "";
  }
}
const contextPieces = [];
["README.md", "README", "package.json"].forEach((f) => {
  const p = path.resolve(process.cwd(), f);
  const c = safeRead(p);
  if (c) contextPieces.push(`== ${f} ==\n${c}`);
});
const repoContext = contextPieces.join("\n\n").slice(0, 16000);

// 厳しめの制約（触ってほしくないものはここへ追加）
const constraints = `
- 出力は **統一diff (unified patch)** のみ。説明文やコードブロック記号は不要。
- .git / 隠しファイル / ビルド生成物 (dist, build, out) / lockファイル (*.lock) は変更禁止。
- 既存ファイルの変更は '--- <old>' と '+++ <new>'、新規は /dev/null からの追加で表現。
- 変更は最小限。ビルド/テストが壊れないように。
`;

const prompt = `
あなたは熟練エンジニアです。次の課題を解決するための最小変更パッチ（統一diff）を作成してください。

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

const res = await model.generateContent(prompt);
let text = (res.response.text() || "").trim();

// もし ```diff ... ``` のようなフェンス付きで返ってきたら中身だけ抽出
const fenced = text.match(/```(?:diff|patch)?\s*([\s\S]*?)```/i);
if (fenced) text = fenced[1].trim();

// diff っぽいヘッダが無ければ終了（安全側）
const looksLikeDiff =
  /^(\-\-\- |\+\+\+ |diff --git )/m.test(text) || /^@@\s*-\d+,\d+ \+\d+,\d+/m.test(text);

if (!looksLikeDiff || text.length < 10) {
  console.error("Model did not return a valid unified diff.");
  process.exit(0);
}

process.stdout.write(text.endsWith("\n") ? text : text + "\n");
