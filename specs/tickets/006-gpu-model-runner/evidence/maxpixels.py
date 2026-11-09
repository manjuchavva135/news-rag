"""Does qwen3-vl-32b see a full-resolution page? Compare image tokens with the 20 MP setting.

Qwen3-VL uses 16-px patches merged 2x2: one token per 32x32 px. A 5376x7600 page (40.9 MP) is
resized to <= max_pixels: about 19.5k tokens at 20 MP, about 16.4k at the 16.7 MP default.
"""

import time

from PIL import Image, ImageDraw, ImageFont

from newsrag.models import use

W, H = 5376, 7600
page = Image.new("RGB", (W, H), "white")
draw = ImageDraw.Draw(page)
font = ImageFont.load_default(size=120)
for row in range(0, H, 400):
    draw.text((200, row + 100), f"LINE {row // 400:02d} SAKSHI TEST PAGE", fill="black", font=font)

with use("qwen3-vl-32b") as m:
    text_only = m.raw.chat.completions.create(
        model="qwen3-vl-32b", messages=[{"role": "user", "content": "Hi"}], max_tokens=1
    ).usage.prompt_tokens
    t0 = time.monotonic()
    from newsrag.models.client import build_messages

    resp = m.raw.chat.completions.create(
        model="qwen3-vl-32b",
        messages=build_messages("What is written on line 07? Reply with the text only.", [page]),
        max_tokens=32,
        temperature=0,
    )
    dt = time.monotonic() - t0
    tokens = resp.usage.prompt_tokens - text_only
    print(f"page {W}x{H} = {W * H / 1e6:.1f} MP")
    print(f"image tokens ~ {tokens} -> {tokens * 32 * 32 / 1e6:.1f} MP seen by the model")
    print(f"reply: {resp.choices[0].message.content!r} ({dt:.1f}s)")
