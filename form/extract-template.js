// 在瀏覽器 console 於 growth-form-a4.html 頁面執行，輸出 template.json 內容（mm，sheet 左上為原點）
(() => {
  const sheet = document.querySelector('.sheet'); const sr = sheet.getBoundingClientRect(); const k = sr.width / 210;
  const mm = (r) => ({ x: +((r.left - sr.left) / k).toFixed(2), y: +((r.top - sr.top) / k).toFixed(2), w: +(r.width / k).toFixed(2), h: +(r.height / k).toFixed(2) });
  const reg = {}; document.querySelectorAll('.reg').forEach(e => { reg[e.className.replace('reg', '').trim()] = mm(e.getBoundingClientRect()); });
  reg.orientation_dot = mm(document.querySelector('.reg-dot').getBoundingClientRect());
  const ticks = [...document.querySelectorAll('.tick')].map(e => mm(e.getBoundingClientRect()));
  const groups = [...document.querySelectorAll('.boxes')].map(g => [...g.querySelectorAll('.bx')].map(b => Object.assign(mm(b.getBoundingClientRect()), b.classList.contains('pre') ? { pre: b.textContent.trim() } : {})));
  const take = (n) => { const g = groups.shift(); if (!g || g.length !== n) throw new Error('group size mismatch ' + n); return g; };
  const t = { unit: 'mm', origin: 'sheet top-left', page: [210, 297], registration: reg,
    fields: { seq: take(8), gender: { male: ticks[0], female: ticks[1] }, birth: { y: take(3), m: take(2), d: take(2) }, father: take(3), mother: take(3), rows: [] } };
  for (let i = 0; i < 10; i++) t.fields.rows.push({ label: i < 9 ? String(i + 1) : 'today', date: { y: take(3), m: take(2), d: take(2) }, height: { int: take(3), dec: take(1) }, weight: { int: take(3), dec: take(1) } });
  if (groups.length) throw new Error('leftover groups ' + groups.length);
  return JSON.stringify(t);
})();
