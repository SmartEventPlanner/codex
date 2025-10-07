// summarize-diff.js
// Usage: node .github/scripts/summarize-diff.js pr-diff.txt > pr-summary.md
import fs from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";
import { GoogleGenAI } from "@google/genai";

const file = process.argv[2] || "pr-diff.txt";
const API_KEY = process.env.GEMINI_API_KEY;
if (!API_KEY) {
  console.error("GEMINI_API_KEY is not set.");
  process.exit(1);
}

const raw = fs.readFileSync(file, "utf8");

// 大きい差分は分割 → 要約 → 統合
const MAX_CHARS = 100_000;
const chunks = [];
for (let i = 0; i < raw.length; i += MAX_CHARS) chunks.push(raw.slice(i, i + MAX_CHARS));

const ai = new GoogleGenAI({ apiKey: API_KEY });

async function genWithRetry(model, contents) {
  const backoff = [2000, 5000, 10000, 20000];
  for (let i = 0; i <= backoff.length; i++) {
    try {
      const r = await ai.models.generateContent({ model, contents });
      return (r.text || "").trim();
    } catch (e) {
      const msg = String(e?.message || e);
      const retryable = /429|RESOURCE_EXHAUSTED|rate|temporar|timeout|503/i.test(msg);
      if (!retryable || i === backoff.length) throw e;
      await sleep(backoff[i]);
    }
  }
}

async function summarize(modelName, text, instruction) {
  return await genWithRetry(
    modelName,
    `${instruction}\n\n---BEGIN DIFF---\n${text}\n---END DIFF---`
  );
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
  partials.push(await summarize("gemini-2.5-flash", c, perChunkInstruction)); // 速いモデル
}

const finalInstruction = `
以下の複数サマリを統合し、重複を整理して1つのMarkdownにしてください。
- 先頭に「# 変更点サマリ」
- 「主な変更」「影響範囲」「レビューポイント」「注意（破壊的変更）」の見出しを付ける
- 箇条書き中心で簡潔に
`;

const finalText = await summarize(
  "gemini-2.5-pro",
  partials.join("\n\n---\n\n"),
  finalInstruction
); // 精度重視で統合

process.stdout.write(`# 変更点サマリ\n\n${finalText}\n`);
