// summarize-diff.js
// Usage: node .github/scripts/summarize-diff.js pr-diff.txt > pr-summary.md
import fs from "node:fs";
import { GoogleGenerativeAI } from "@google/genai";

const file = process.argv[2] || "pr-diff.txt";
const API_KEY = process.env.GEMINI_API_KEY;
if (!API_KEY) {
  console.error("GEMINI_API_KEY is not set.");
  process.exit(1);
}

const raw = fs.readFileSync(file, "utf8");

// 大きい差分に備えてチャンク → 要約 → 統合
const MAX_CHARS = 100_000;
const chunks = [];
for (let i = 0; i < raw.length; i += MAX_CHARS) chunks.push(raw.slice(i, i + MAX_CHARS));

const client = new GoogleGenerativeAI({ apiKey: API_KEY });
const fast = client.getGenerativeModel({ model: "gemini-2.5-flash" });
const precise = client.getGenerativeModel({ model: "gemini-2.5-pro" });

async function summarize(model, text, instruction) {
  const res = await model.generateContent([
    { text: `${instruction}\n\n---BEGIN DIFF---\n${text}\n---END DIFF---` }
  ]);
  return (res.response.text() || "").trim();
}

const perChunkInstruction = `
次のGit差分を日本語で要約してください（Markdown出力）。
- 重要な変更点（機能/バグ修正/破壊的変更）
- 追加/変更/削除ファイル
- 影響範囲（テスト/ドキュメント/設定）
- レビューポイント
箇条書き中心で簡潔に。
`;

const partials = [];
for (const c of chunks) {
  partials.push(await summarize(fast, c, perChunkInstruction));
}

const finalInstruction = `
以下の複数サマリを統合し、重複を整理して1つのMarkdownにしてください。
- 先頭に「# 変更点サマリ」
- 「主な変更」「影響範囲」「レビューポイント」「注意（破壊的変更）」の見出しを付ける
- 箇条書き中心で簡潔に
`;

const finalText = await summarize(precise, partials.join("\n\n---\n\n"), finalInstruction);
process.stdout.write(`# 変更点サマリ\n\n${finalText}\n`);
