import fs from "node:fs";
import path from "node:path";
import { GoogleGenAI } from "@google/genai";
import { setTimeout as sleep } from "node:timers/promises";

// … 中略（既存の変数/プロンプト定義はそのまま） …

const ai = new GoogleGenAI({ apiKey: API_KEY });

async function generateWithRetry(contents) {
  const models = ["gemini-2.5-pro", "gemini-2.5-flash"]; // Proが429ならFlashへフォールバック
  const backoff = [2_000, 5_000, 10_000, 20_000];        // 2s→5s→10s→20s

  for (const model of models) {
    for (let i = 0; i <= backoff.length; i++) {
      try {
        const r = await ai.models.generateContent({ model, contents });
        return (r.text || "").trim();
      } catch (e) {
        const msg = String(e?.message || e);
        const retryable = /429|RESOURCE_EXHAUSTED|rate|temporar|timeout|503/i.test(msg);
        if (!retryable || i === backoff.length) throw e;  // これ以上無理
        await sleep(backoff[i]);                           // 待って再試行
      }
    }
  }
  return "";
}

const textRaw = await generateWithRetry(prompt);
let text = textRaw;

// フェンス除去 & diff判定は従来どおり
const fenced = text.match(/```(?:diff|patch)?\s*([\s\S]*?)```/i);
if (fenced) text = fenced[1].trim();
const looksLikeDiff =
  /^(\-\-\- |\+\+\+ |diff --git )/m.test(text) || /^@@\s*-\d+,\d+ \+\d+,\d+/m.test(text);
if (!looksLikeDiff || text.length < 10) {
  console.error("Model did not return a valid unified diff (possibly quota).");
  process.exit(0); // 空パッチ扱いで終了（ジョブ失敗にしない）
}
process.stdout.write(text.endsWith("\n") ? text : text + "\n");
