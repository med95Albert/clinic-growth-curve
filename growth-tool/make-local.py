# -*- coding: utf-8 -*-
"""由線上版產生「完全離線版」：把 CDN script 換成 vendor/ 的本機檔案。
改完 Growth_Curve_Tool.html 後重跑一次即可同步。
"""
import io, sys

SRC, DST = 'Growth_Curve_Tool.html', 'Growth_Curve_Tool.local.html'
MAP = {
    'https://cdnjs.cloudflare.com/ajax/libs/react/18.2.0/umd/react.development.js': 'vendor/react.js',
    'https://cdnjs.cloudflare.com/ajax/libs/react-dom/18.2.0/umd/react-dom.development.js': 'vendor/react-dom.js',
    'https://cdnjs.cloudflare.com/ajax/libs/babel-standalone/7.23.5/babel.min.js': 'vendor/babel.min.js',
    'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js': 'vendor/chart.umd.min.js',
    'https://cdn.tailwindcss.com': 'vendor/tailwind.js',
    'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js': 'vendor/html2canvas.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js': 'vendor/qrcode.min.js',
}

s = io.open(SRC, encoding='utf-8').read()
missing = [u for u in MAP if u not in s]
if missing:
    sys.exit('原始檔找不到這些 CDN 連結，請確認版本是否變動：\n  ' + '\n  '.join(missing))

for url, local in MAP.items():
    s = s.replace(url, local)

s = s.replace('<title>兒童生長評估計算器（含異常檢測）</title>',
              '<title>兒童生長評估計算器（離線版）</title>\n'
              '    <!-- 離線版：所有相依套件都在 vendor/，不需要對外連網。 -->\n'
              '    <!-- 由 make-local.py 從 Growth_Curve_Tool.html 產生，請勿直接編輯。 -->')

io.open(DST, 'w', encoding='utf-8').write(s)
print('產生 %s（%d 個 CDN 連結已本機化）' % (DST, len(MAP)))
