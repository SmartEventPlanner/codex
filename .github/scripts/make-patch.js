// make-patch.js
// Usage: node .github/scripts/make-patch.js "<issue_title>" "<issue_body>" > ai.patch
import fs from "node:fs";
import path from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { GoogleGenAI } from "@google/genai";

const [ISSUE_TITLE = "", ISSUE_BODY = ""] = process.argv.slice(2);
const API_KEY = process.env.GEMINI_API_KEY;
if (!API_KEY) {
  console.error("GEMINI_API_KEY is not set.");
  process.exit(1);
}

// リポ文脈（入れすぎるとコスト増なので一部のみ）
function safeRead(p, limit = 8000) {
  try { return fs.readFileSync(p, "utf8").slice(0, limit); } catch { return ""; }
}
const ctx = [];
["README.md", "README", "package.json"].forEach((f) => {
  const p = path.resolve(process.cwd(), f);
  const c = safeRead(p);
  if (c) ctx.push(`== ${f} ==\n${c}`);
});
const repoContext = ctx.join("\n\n").slice(0, 16000);

// 制約
const constraints = `
- 出力は **統一diff (unified patch)** のみ。説明文やコードブロック記号は不要。
- .git / 隠しファイル / dist, build, out / *.lock は変更禁止。
- 既存変更は '--- <old>' と '+++ <new>'、新規は /dev/null からの追加で表現。
- 変更は最小限。ビルド/テストを壊さない。
`;

const prompt = `
あなたは熟練エンジニアです。次の課題を解決する最小変更パッチ（統一diff）を作成してください。

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

const ai = new GoogleGenAI({ apiKey: API_KEY });

// 429/503 対策：バックオフ＋モデルフォールバック（Flash → Pro）
async function generateWithRetry(contents) {
  const models = ["gemini-2.5-flash", "gemini-2.5-pro"];
  const backoff = [2000, 5000, 10000, 20000];

  for (const model of models) {
    for (let i = 0; i <= backoff.length; i++) {
      try {
        const r = await ai.models.generateContent({ model, contents });
        return (r.text || "").trim();
      } catch (e) {
        const msg = String(e?.message || e);
        const retryable = /429|RESOURCE_EXHAUSTED|rate|temporar|timeout|503/i.test(msg);
        if (!retryable || i === backoff.length) {
          // これ以上無理 → 空文字で返して空パッチ扱い（ジョブ失敗させない）
          return "";
        }
        await sleep(backoff[i]);
      }
    }
  }
  return "";
}

const raw = await generateWithRetry(prompt);
let text = raw;

// フェンス付き（```diff …```）なら中身だけ抽出
const fenced = text.match(/```(?:diff|patch)?\s*([\s\S]*?)```/i);
if (fenced) text = fenced[1].trim();

// diff 判定
const looksLikeDiff =
  /^(\-\-\- |\+\+\+ |diff --git )/m.test(text) || /^@@\s*-\d+,\d+ \+\d+,\d+/m.test(text);

if (!looksLikeDiff || text.length < 10) {
  console.error("Model did not return a valid unified diff (possibly quota).");
  process.exit(0); // 空パッチで静かに終了
}

process.stdout.write(text.endsWith("\n") ? text : text + "\n");
