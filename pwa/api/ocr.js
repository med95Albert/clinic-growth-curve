import Anthropic from '@anthropic-ai/sdk';
import { z } from 'zod';
import { zodOutputFormat } from '@anthropic-ai/sdk/helpers/zod';
import { checkKey, rateLimit, dailyCap, clientIp } from './_redis.js';

export const config = { maxDuration: 60 };

const client = new Anthropic();

// 讀方格單一數字不需要 Opus；Sonnet 5 視覺解析度更高（2576px）且便宜近半。
// 想換模型時設環境變數 OCR_MODEL（如 claude-haiku-4-5 更省、claude-opus-5 保險）。
const MODEL = process.env.OCR_MODEL || 'claude-sonnet-5';

const Measurement = z.object({
  measureDate: z.string().nullable(),   // 西元 YYYY-MM-DD
  height: z.number().nullable(),        // 公分
  weight: z.number().nullable(),        // 公斤
});

const FormRead = z.object({
  seq: z.string().nullable(),                        // 候診序號
  gender: z.enum(['male', 'female']).nullable(),
  birthDate: z.string().nullable(),                  // 西元 YYYY-MM-DD
  fatherHeight: z.number().nullable(),
  motherHeight: z.number().nullable(),
  measurements: z.array(Measurement),
  uncertain: z.array(z.string()),   // 不確定的欄位路徑，如 "measurements[1].height"
  notes: z.string(),                // 給人看的備註，例如「表單被手指遮住一角」
});

const SYSTEM = `你是一個表單判讀器，負責讀取台灣兒科診所的「兒童生長曲線 資料填寫單」照片，輸出結構化資料。

【表單結構】
- 四個角落有黑色方塊定位標記；右上角的方塊下方多一個小方塊，用來判斷方向。若照片顛倒或旋轉，請先在心中校正再讀。
- 所有數字都寫在格子裡，一格一個數字。
- 「候診序號」：3 格數字，由櫃檯填寫，可能空白。
- 「性別」：兩個打勾方框，男生 / 女生。
- 「出生日期」：民國 [][][] 年 [][] 月 [][] 日。
- 「父母身高」：爸爸 [][][] 公分、媽媽 [][][] 公分，可能空白。
- 「身高體重紀錄」表格共 6 列（第 1–5 列為歷史紀錄，第 6 列標示「今日量測」）：
  - 日期欄第一格已預印「1」（淺灰底），代表民國年的百位數，一律當作 1。
  - 身高欄格式為 [][][] . []，例如 1 2 3 . 5 → 123.5 公分。
  - 體重欄格式為 [][][] . []，例如 0 2 4 . 0 → 24.0 公斤。整數位可能補前導 0，請去掉。

【判讀規則】
1. 民國年轉西元：西元年 = 民國年 + 1911。例如民國 108 年 3 月 5 日 → "2019-03-05"。日期一律輸出 YYYY-MM-DD。
2. 空白、看不清楚、或你無法確定的欄位，一律輸出 null，並把欄位路徑加入 uncertain。**絕對不要猜測數字**——寧可回 null 讓人工補，也不要輸出可能錯誤的值。
3. 格子被整格塗黑代表作廢，請讀旁邊重寫的數值；若無法判斷重寫值，該欄回 null 並記入 uncertain。
4. 性別：恰好一個框有打勾/打叉/塗滿才判定；兩個都有或都沒有，回 null 並記入 uncertain。
5. measurements 只回傳「至少有身高或體重」的列，依表單由上而下的順序；完全空白的列請略過。
6. 若「今日量測」那一列有身高體重但日期欄空白，measureDate 填入使用者訊息告知的今天日期，並把該列的 measureDate 加入 uncertain。
7. uncertain 的欄位路徑格式："seq"、"gender"、"birthDate"、"fatherHeight"、"motherHeight"、"measurements[0].height" 等。索引對應你輸出的 measurements 陣列位置。
8. notes 用繁體中文寫給護理師看的簡短提醒（例如「表單右下角反光，第 4 列體重無法確認」）。沒有特別狀況就填空字串。
9. 只輸出結構化資料，不要有任何額外說明文字。`;

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }
  if (!checkKey(req)) {
    return res.status(401).json({ error: '密鑰錯誤或伺服器未設定 CLINIC_KEY' });
  }
  if (!process.env.ANTHROPIC_API_KEY) {
    return res.status(500).json({ error: '伺服器未設定 ANTHROPIC_API_KEY' });
  }

  try {
    if (!(await rateLimit(`ocr:${clientIp(req)}`, 40))) {
      return res.status(429).json({ error: '請求過於頻繁，請稍後再試' });
    }

    const dailyLimit = Number(process.env.OCR_DAILY_LIMIT || 300);
    if (!(await dailyCap('ocr', dailyLimit))) {
      return res.status(429).json({
        error: `今日判讀張數已達上限（${dailyLimit} 張），請明天再試或聯絡管理者`,
      });
    }

    const { image, today } = req.body || {};
    if (typeof image !== 'string') {
      return res.status(400).json({ error: '缺少 image 欄位' });
    }

    const m = image.match(/^data:(image\/(jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$/);
    if (!m) {
      return res.status(400).json({ error: '影像格式不支援（僅接受 jpeg / png / webp 的 data URL）' });
    }
    const [, mediaType, , b64] = m;
    if (b64.length > 8_000_000) {
      return res.status(413).json({ error: '影像過大，請重新壓縮後上傳' });
    }

    const todayStr = /^\d{4}-\d{2}-\d{2}$/.test(today || '')
      ? today
      : new Date().toISOString().slice(0, 10);

    const response = await client.messages.parse({
      model: MODEL,
      max_tokens: 16000,
      system: SYSTEM,
      thinking: { type: 'adaptive' },
      output_config: {
        effort: 'high',
        format: zodOutputFormat(FormRead),
      },
      messages: [
        {
          role: 'user',
          content: [
            { type: 'image', source: { type: 'base64', media_type: mediaType, data: b64 } },
            { type: 'text', text: `今天是 ${todayStr}。請判讀這張填寫單。` },
          ],
        },
      ],
    });

    // 安全分類器可能擋下請求；讀 content 之前先確認
    if (response.stop_reason === 'refusal') {
      return res.status(422).json({ error: '判讀請求被拒絕，請改用手動輸入' });
    }
    if (!response.parsed_output) {
      return res.status(502).json({ error: '判讀結果解析失敗，請重拍一次' });
    }

    return res.status(200).json({
      data: response.parsed_output,
      usage: {
        input_tokens: response.usage?.input_tokens,
        output_tokens: response.usage?.output_tokens,
      },
    });
  } catch (err) {
    console.error('[ocr]', err);
    const status = err?.status && err.status >= 400 && err.status < 600 ? err.status : 500;
    return res.status(status).json({ error: err?.message || '判讀失敗' });
  }
}
