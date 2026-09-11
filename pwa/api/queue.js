import { randomUUID } from 'node:crypto';
import { redis, redisEnabled, checkKey, rateLimit, clientIp } from './_redis.js';

const IDX = 'growth:idx';
const TTL = Number(process.env.QUEUE_TTL_SECONDS || 1800);

// 只保留畫曲線需要的欄位，避免任何額外資料被寫進暫存
function sanitize(d) {
  const num = (v) => (v === null || v === undefined || v === '' ? null : Number(v));
  return {
    seq: d.seq ? String(d.seq).slice(0, 8) : null,
    gender: d.gender === 'male' || d.gender === 'female' ? d.gender : null,
    birthDate: /^\d{4}-\d{2}-\d{2}$/.test(d.birthDate || '') ? d.birthDate : null,
    fatherHeight: num(d.fatherHeight),
    motherHeight: num(d.motherHeight),
    measurements: (Array.isArray(d.measurements) ? d.measurements : [])
      .slice(0, 10)
      .map((m) => ({
        measureDate: /^\d{4}-\d{2}-\d{2}$/.test(m?.measureDate || '') ? m.measureDate : null,
        height: num(m?.height),
        weight: num(m?.weight),
      })),
  };
}

export default async function handler(req, res) {
  if (!checkKey(req)) {
    return res.status(401).json({ error: '密鑰錯誤或伺服器未設定 CLINIC_KEY' });
  }
  if (!redisEnabled) {
    return res.status(501).json({
      error: '佇列未啟用：伺服器沒有設定 Upstash Redis 環境變數',
      disabled: true,
    });
  }

  try {
    if (!(await rateLimit(`q:${clientIp(req)}`, 120))) {
      return res.status(429).json({ error: '請求過於頻繁' });
    }

    const now = Date.now();
    const cutoff = now - TTL * 1000;

    if (req.method === 'POST') {
      const data = sanitize(req.body?.data || {});
      if (!data.birthDate || !data.gender) {
        return res.status(400).json({ error: '缺少性別或出生日期，無法建檔' });
      }
      const id = randomUUID().slice(0, 8);
      const record = { id, createdAt: now, data };
      await redis('SET', `growth:${id}`, JSON.stringify(record), 'EX', TTL);
      await redis('ZADD', IDX, now, id);
      await redis('ZREMRANGEBYSCORE', IDX, '-inf', cutoff);
      return res.status(200).json({ id });
    }

    if (req.method === 'GET') {
      await redis('ZREMRANGEBYSCORE', IDX, '-inf', cutoff);
      const ids = (await redis('ZRANGE', IDX, cutoff, '+inf', 'BYSCORE')) || [];
      if (ids.length === 0) return res.status(200).json({ items: [] });
      const raw = await redis('MGET', ...ids.map((i) => `growth:${i}`));
      const items = (raw || [])
        .filter(Boolean)
        .map((s) => { try { return JSON.parse(s); } catch { return null; } })
        .filter(Boolean)
        .sort((a, b) => b.createdAt - a.createdAt);
      return res.status(200).json({ items });
    }

    if (req.method === 'DELETE') {
      const id = String(req.query?.id || '');
      if (!/^[0-9a-f]{8}$/.test(id)) return res.status(400).json({ error: 'id 不正確' });
      await redis('DEL', `growth:${id}`);
      await redis('ZREM', IDX, id);
      return res.status(200).json({ ok: true });
    }

    return res.status(405).json({ error: 'Method not allowed' });
  } catch (err) {
    console.error('[queue]', err);
    return res.status(500).json({ error: err?.message || '佇列操作失敗' });
  }
}
