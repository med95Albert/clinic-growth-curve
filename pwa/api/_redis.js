// Upstash Redis REST 極簡封裝。沒設定環境變數時回傳 null，呼叫端自行降級。
const URL_ =
  process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL || '';
const TOKEN =
  process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN || '';

export const redisEnabled = Boolean(URL_ && TOKEN);

export async function redis(...command) {
  if (!redisEnabled) return null;
  const res = await fetch(URL_, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(command.map(String)),
  });
  if (!res.ok) {
    throw new Error(`Redis ${res.status}: ${await res.text()}`);
  }
  const json = await res.json();
  if (json.error) throw new Error(`Redis: ${json.error}`);
  return json.result;
}

// 共用密鑰驗證。回傳 true 表示放行。
export function checkKey(req) {
  const expected = process.env.CLINIC_KEY;
  if (!expected) return false; // 沒設定就一律拒絕，避免誤開成公開端點
  const got = req.headers['x-clinic-key'];
  if (typeof got !== 'string' || got.length !== expected.length) return false;
  // 長度相同時做定時比較，避免以回應時間猜測密鑰
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= got.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

// Redis 未啟用時的記憶體級計數備援。Vercel Fluid Compute 會重用執行實例、
// 但不保證同一部署只有單一實例，多實例下這份計數各自獨立、無法互相看見；
// 沒接 Redis 時「有總比沒有」，僅作下限防護，正式環境仍建議接 Upstash Redis。
const memStore = new Map(); // key -> { count, expiresAt(ms) }
let memSweepCounter = 0;

function memIncr(key, windowMs) {
  const now = Date.now();
  // 每 200 次呼叫順手清一次過期 key，避免 Map 隨執行時間無限成長
  if (++memSweepCounter >= 200) {
    memSweepCounter = 0;
    for (const [k, v] of memStore) {
      if (v.expiresAt <= now) memStore.delete(k);
    }
  }
  let entry = memStore.get(key);
  if (!entry || entry.expiresAt <= now) {
    entry = { count: 0, expiresAt: now + windowMs };
    memStore.set(key, entry);
  }
  entry.count += 1;
  return entry.count;
}

// 台灣時區（UTC+8）的日期字串，跟伺服器實際所在時區無關
function taiwanDateStr() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

// 每分鐘上限。Redis 未啟用時改用記憶體計數，邏輯（bucket、超過即拒）與 Redis 版一致。
export async function rateLimit(bucket, limit = 40) {
  const key = `rl:${bucket}:${Math.floor(Date.now() / 60000)}`;
  if (!redisEnabled) {
    return memIncr(key, 90_000) <= limit; // 視窗長度對齊下方 Redis EXPIRE 秒數
  }
  const n = await redis('INCR', key);
  if (n === 1) await redis('EXPIRE', key, 90);
  return n <= limit;
}

// 每日張數上限，用來擋密鑰外洩後被無限刷 Anthropic API 費用。
// key 依台灣時間（UTC+8）計日，跨日自動歸零；回傳 true 表示尚未超限。
export async function dailyCap(bucket, limit) {
  const key = `dc:${bucket}:${taiwanDateStr()}`;
  if (!redisEnabled) {
    return memIncr(key, 90_000_000) <= limit; // 90000 秒（=90_000_000 ms），對齊下方 Redis EXPIRE
  }
  const n = await redis('INCR', key);
  if (n === 1) await redis('EXPIRE', key, 90000);
  return n <= limit;
}

export function clientIp(req) {
  const fwd = req.headers['x-forwarded-for'];
  return (typeof fwd === 'string' ? fwd.split(',')[0].trim() : '') || 'unknown';
}
