# -*- coding: utf-8 -*-
"""產生一張「模擬填寫單」PNG，用來在沒有真實紙本照片時驗證整條判讀管線
（密鑰、API 金鑰、SDK 結構化輸出、回傳格式）。不依賴 PIL。

它只是印刷體數字＋英文標籤的粗略版面，不是準確率測試——
真實準確率一定要用紙本手寫實拍。

用法：python3 scripts/make-test-form.py out.png
預期判讀結果（西元）：男、2019-03-05、父 175、母 162、
  2024-08-22 112.0/19.0、2025-08-22 118.5/21.5、2026-08-22 123.5/24.0
"""
import sys, zlib, struct

W, H = 800, 1100
px = bytearray(b'\xff' * (W * H))          # 灰階，255 = 白

def rect(x, y, w, h, fill=False, t=2):
    if fill:
        for yy in range(y, y + h):
            px[yy * W + x: yy * W + x + w] = b'\x00' * w
        return
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            if xx < x + t or xx >= x + w - t or yy < y + t or yy >= y + h - t:
                px[yy * W + xx] = 0

GLYPH = {  # 5x7 點陣
 'C':"01110 10001 10000 10000 10000 10001 01110",'L':"10000 10000 10000 10000 10000 10000 11111",
 '0':"01110 10001 10011 10101 11001 10001 01110",'1':"00100 01100 00100 00100 00100 00100 01110",
 '2':"01110 10001 00001 00010 00100 01000 11111",'3':"11111 00010 00100 00010 00001 10001 01110",
 '4':"00010 00110 01010 10010 11111 00010 00010",'5':"11111 10000 11110 00001 00001 10001 01110",
 '6':"00110 01000 10000 11110 10001 10001 01110",'7':"11111 00001 00010 00100 01000 01000 01000",
 '8':"01110 10001 10001 01110 10001 10001 01110",'9':"01110 10001 10001 01111 00001 00010 01100",
 '.':"00000 00000 00000 00000 00000 01100 01100",'X':"10001 10001 01010 00100 01010 10001 10001",
 'S':"01111 10000 10000 01110 00001 00001 11110",'E':"11111 10000 10000 11110 10000 10000 11111",
 'Q':"01110 10001 10001 10001 10101 10010 01101",'M':"10001 11011 10101 10101 10001 10001 10001",
 'F':"11111 10000 10000 11110 10000 10000 10000",'B':"11110 10001 10001 11110 10001 10001 11110",
 'I':"01110 00100 00100 00100 00100 00100 01110",'R':"11110 10001 10001 11110 10100 10010 10001",
 'T':"11111 00100 00100 00100 00100 00100 00100",'H':"10001 10001 10001 11111 10001 10001 10001",
 'A':"01110 10001 10001 11111 10001 10001 10001",'O':"01110 10001 10001 10001 10001 10001 01110",
 'D':"11110 10001 10001 10001 10001 10001 11110",'W':"10001 10001 10001 10101 10101 10101 01010",
 'G':"01110 10001 10000 10111 10001 10001 01111",'Y':"10001 10001 01010 00100 00100 00100 00100",
 ' ':"00000 00000 00000 00000 00000 00000 00000",'/':"00001 00010 00010 00100 01000 01000 10000",
}

def text(x, y, s, scale=3):
    for ch in s:
        rows = GLYPH[ch.upper()].split()
        for r, bits in enumerate(rows):
            for c, b in enumerate(bits):
                if b == '1':
                    rect(x + c * scale, y + r * scale, scale, scale, fill=True)
        x += 6 * scale

BOX = 34
def boxes(x, y, digits, pre=None):
    """一格一數字；pre 為預印的灰底格（模擬表格裡的民國「1」）"""
    if pre is not None:
        for yy in range(y + 2, y + BOX - 2):
            px[yy * W + x + 2: yy * W + x + BOX - 2] = b'\xd8' * (BOX - 4)
        rect(x, y, BOX, BOX); text(x + 9, y + 7, pre); x += BOX
    for d in digits:
        if d == '.':
            text(x + 3, y + 8, '.', 4); x += 14; continue
        rect(x, y, BOX, BOX); text(x + 9, y + 7, d); x += BOX
    return x

# 四角定位＋右上方向標記
for (x, y) in ((20, 20), (W - 50, 20), (20, H - 50), (W - 50, H - 50)):
    rect(x, y, 30, 30, fill=True)
rect(W - 36, 58, 12, 12, fill=True)

text(70, 30, 'GROWTH FORM', 4)
text(70, 90, 'SEQ'); boxes(150, 80, '012')
text(420, 90, 'M'); rect(450, 80, BOX, BOX); text(459, 87, 'X')
text(520, 90, 'F'); rect(550, 80, BOX, BOX)

text(70, 160, 'BIRTH'); x = boxes(190, 150, '108'); text(x + 6, 160, 'Y')
x = boxes(x + 34, 150, '03'); text(x + 6, 160, 'M'); x = boxes(x + 34, 150, '05'); text(x + 6, 160, 'D')

text(70, 230, 'FATHER'); boxes(220, 220, '175'); text(340, 230, 'CM')
text(420, 230, 'MOTHER'); boxes(570, 220, '162'); text(690, 230, 'CM')

text(70, 310, 'DATE'); text(330, 310, 'HEIGHT'); text(580, 310, 'WEIGHT')
rows = [('13 08 22', '112.0', '019.0'), ('14 08 22', '118.5', '021.5'), ('15 08 22', '123.5', '024.0')]
y = 350
for i, (d, h, w) in enumerate(rows):
    label = 'TODAY' if i == len(rows) - 1 else str(i + 1)
    text(30, y + 10, label)
    x = boxes(120, y, d[:2], pre='1'); text(x + 4, y + 10, 'Y')
    x = boxes(x + 22, y, d[3:5]); text(x + 4, y + 10, 'M')
    x = boxes(x + 22, y, d[6:8]); text(x + 4, y + 10, 'D')
    boxes(440, y, h); boxes(640, y, w)
    y += 70

def png(gray):
    raw = b''.join(b'\x00' + bytes(gray[r * W:(r + 1) * W]) for r in range(H))
    def ch(t, d):
        c = struct.pack('>I', len(d)) + t + d
        return c + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + ch(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 0, 0, 0, 0))
            + ch(b'IDAT', zlib.compress(bytes(raw), 9)) + ch(b'IEND', b''))

out = sys.argv[1] if len(sys.argv) > 1 else 'test-form.png'
open(out, 'wb').write(png(px))
print('wrote', out)
