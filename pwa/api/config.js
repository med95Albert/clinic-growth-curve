import { checkKey, redisEnabled } from './_redis.js';

// 前端啟動時取得伺服器設定（不含任何密鑰）
export default async function handler(req, res) {
  if (!checkKey(req)) return res.status(401).json({ error: '密鑰錯誤' });
  return res.status(200).json({
    growthToolUrl:
      process.env.GROWTH_TOOL_URL || '/tool.html',
    queueEnabled: redisEnabled,
  });
}
