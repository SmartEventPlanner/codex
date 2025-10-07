// summarize-diff.js
// 使い方: node .github/scripts/summarize-diff.js pr-diff.txt > pr-summary.md
import fs from "node:fs";
import { GoogleGenerativeAI } from "@google/genai";

const file = process.argv[2] || "pr-diff.txt";
const API_KEY = process.env.GEMINI_API_KEY;
if (!API_KEY) {
  console.error("GEMINI_API_KEY is not set.");
  process.exit(1);
}

const raw = fs.readFileSync(file, "utf8");

// 大きすぎるdiffはチャンクに分割 → 各チャンクを要約 → 最後に統合要約
const MAX_CHARS = 100_000;
function chunk(s, size = MAX_CHARS) {
  const chunks = [];
  for (let i = 0; i < s.length; i += size) chunks.push(s.slice(i, i + size));
  return chunks;
}

const client = new GoogleGenerativeAI({ apiKey: API_KEY });
const fast = client.getGenerativeModel({ model: "gemini-2.5-flash" });
const precise = client.getGenerativeModel({ model: "gemini-2.5-pro" });

async function summarizeOnce(model, input, instruction) {
  const res = await model.generateContent([
    {
      text:
        instruction +
        "\n\n---BEGIN DIFF---\n" +
        input +
        "\n---END DIFF---\n"
    }
  ]);
  return res.response.text().trim();
}

const perChunkInstruction = `
次のGit差分を日本語で要約してください。出力はMarkdown。
- 重要な変更点（機能/バグ修正/破壊的変更）
- 追加/変更/削除ファイル
- 影響範囲（テスト/ドキュメント/設定）
- 確認観点（レビュワーが見るべき点）
箇条書きで簡潔に。
`;

const chunks = chunk(raw);
const partials = [];
for (const c of chunks) {
  // まず速いモデルでチャンク要約
  const sum = await summarizeOnce(fast, c, perChunkInstruction);
  partials.push(sum);
}

// 最終統合
const finalInstruction = `
以下の複数サマリを統合し、重複を整理して1つのMarkdownにしてください。
- 見出し # 変更点サマリ を先頭に
- 次レベルの見出しで「主な変更」「影響範囲」「レビューポイント」「破壊的変更があれば注意」を分ける
- 箇条書き中心で簡潔に
`;

const finalText = await summarizeOnce(
  precise,
  partials.join("\n\n---\n\n"),
  finalInstruction
);

const output = `# 変更点サマリ

${finalText}
`;

process.stdout.write(output);
