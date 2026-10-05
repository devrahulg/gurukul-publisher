"""Make a LinkedIn carousel (a multi-page PDF) from one reel's steps.

Library use (add_linkedin_queue.py does this for you):
    from make_carousel import build_carousel
    build_carousel(spec, "carousels/linkedin-ep105.pdf")

Command line, to make one by hand:
    python tools/make_carousel.py --posts tools/buzz_posts.json --episode 105 --out carousels/x.pdf

Needs Pillow only when you make new carousels:  pip install pillow
Pages are 1080x1350 (4:5), the size LinkedIn shows best on phones. Fonts: Poppins (SIL OFL 1.1).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("Pillow is needed to draw carousels:  pip install pillow")

W, H = 1080, 1350
HERE = pathlib.Path(__file__).resolve().parent
FONTS = HERE / "fonts"
BG_TOP, BG_BOT = (11, 10, 31), (21, 12, 51)
WHITE, SOFT, YELLOW = (255, 255, 255), (221, 214, 254), (255, 212, 0)
SERIES = {
    "kiro": {"name": "KIRO", "accent": (167, 139, 250), "glow": (91, 33, 182)},
    "claude": {"name": "CLAUDE", "accent": (217, 119, 87), "glow": (150, 70, 40)},
}
DISCLAIMER = {
    "kiro": "Independent creator, not affiliated with AWS or Kiro.",
    "claude": "Independent creator, not affiliated with Anthropic. Claude is a trademark of Anthropic.",
}
_STEP = re.compile(r"^(\d+)\. (.+?)(?: - (.+))?$")


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    name = {"bold": "Poppins-Bold.ttf", "semi": "Poppins-Medium.ttf", "med": "Poppins-Medium.ttf"}[weight]
    for cand in (FONTS / name, pathlib.Path("C:/Windows/Fonts/segoeuib.ttf" if weight != "med" else "C:/Windows/Fonts/segoeui.ttf"),
                 pathlib.Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        if cand.exists():
            return ImageFont.truetype(str(cand), size)
    return ImageFont.load_default()


def parse_steps(description: str) -> list[tuple[str, str]]:
    steps = []
    for line in description.split("\n"):
        m = _STEP.match(line.strip())
        if m:
            steps.append((m.group(2).strip(), (m.group(3) or "").strip()))
    return steps


def wrap(draw: ImageDraw.ImageDraw, text: str, f, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=f) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def fit(draw, text, weight, max_w, max_h, start, minimum, spacing=1.25):
    size = start
    while True:
        f = font(weight, size)
        lines = wrap(draw, text, f, max_w)
        if len(lines) * size * spacing <= max_h or size <= minimum:
            return f, lines, size
        size -= 2


def base(series: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    st = SERIES[series]
    img = Image.new("RGB", (W, H), BG_TOP)
    px = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        px.line([(0, y), (W, y)], fill=tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-250, 800, 700, 1700], fill=st["glow"])
    gd.ellipse([600, -350, 1400, 450], fill=tuple(int(c * 0.8) for c in st["glow"]))
    glow = glow.filter(ImageFilter.GaussianBlur(190))
    img = Image.blend(img, Image.composite(glow, img, glow.convert("L")), 0.55)
    return img, ImageDraw.Draw(img)


def chrome(d, series, label, idx, total):
    st = SERIES[series]
    f = font("bold", 30)
    tw = d.textlength(label, font=f)
    d.rounded_rectangle([60, 60, 60 + tw + 44, 122], radius=16, fill=st["accent"])
    d.text((82, 66), label, font=f, fill=(17, 12, 40))
    f2 = font("semi", 30)
    handle = "theAIgurukul"
    d.text((W - 60 - d.textlength(handle, font=f2), 68), handle, font=f2, fill=SOFT)
    d.text((60, H - 96), f"{idx} / {total}", font=font("semi", 28), fill=SOFT)
    if idx < total:  # arrow drawn as shapes: Poppins has no arrow glyph
        f3 = font("semi", 28)
        x1 = W - 60
        d.text((x1 - 50 - d.textlength("Swipe", font=f3), H - 96), "Swipe", font=f3, fill=st["accent"])
        yc = H - 80
        d.line([(x1 - 38, yc), (x1, yc)], fill=st["accent"], width=4)
        d.polygon([(x1, yc), (x1 - 14, yc - 11), (x1 - 14, yc + 11)], fill=st["accent"])


def blocks(d, items, top=170, bottom=H - 150, shift=-20):
    """items: list of (text, weight, size_start, size_min, color, max_h, spacing, gap_after).
    Lays them out as one block, vertically centred between top and bottom."""
    laid, total = [], 0
    for text, weight, start, minimum, color, max_h, spacing, gap in items:
        f, lines, size = fit(d, text, weight, W - 140, max_h, start, minimum, spacing)
        h = int(len(lines) * size * spacing)
        laid.append((f, lines, size, spacing, color, gap))
        total += h + gap
    total -= laid[-1][5]
    y = max(top, top + (bottom - top - total) // 2 + shift)
    for f, lines, size, spacing, color, gap in laid:
        for ln in lines:
            d.text((60, y), ln, font=f, fill=color)
            y += int(size * spacing)
        y += gap


def build_carousel(spec: dict, out: str | pathlib.Path) -> pathlib.Path:
    """spec: series ('kiro'|'claude'), label ('KIRO TIP 105'), title, kicker, steps [(head, detail)],
    takeaway, cta."""
    series = spec["series"]
    st = SERIES[series]
    steps = spec["steps"]
    total = len(steps) + 2
    accent = st["accent"]
    pages = []

    # cover
    img, d = base(series)
    chrome(d, series, spec["label"], 1, total)
    blocks(d, [(spec.get("kicker", "").upper() or " ", "bold", 38, 38, accent, 60, 1.2, 24),
               (spec["title"], "bold", 96, 52, WHITE, 640, 1.2, 40),
               (f"{len(steps)} steps. Save this for later.", "med", 42, 42, SOFT, 80, 1.2, 0)])
    pages.append(img)

    # steps
    for i, (head, detail) in enumerate(steps, 1):
        img, d = base(series)
        chrome(d, series, spec["label"], i + 1, total)
        items = [(f"{i:02d}", "bold", 300, 300, YELLOW, 330, 1.0, 30),
                 (head, "bold", 88, 50, WHITE, 340, 1.2, 36)]
        if detail:
            items.append((detail, "med", 52, 32, SOFT, 420, 1.38, 0))
        blocks(d, items)
        pages.append(img)

    # closing
    img, d = base(series)
    chrome(d, series, spec["label"], total, total)
    blocks(d, [(spec.get("takeaway") or "Try it today.", "bold", 74, 44, WHITE, 420, 1.22, 50),
               (spec.get("cta") or f"Follow theAIgurukul for a {st['name'].title()} tip every day.",
                "semi", 48, 34, accent, 280, 1.3, 0)], bottom=H - 260)
    f3 = font("med", 26)
    for j, ln in enumerate(wrap(d, DISCLAIMER[series], f3, W - 140)):
        d.text((60, H - 200 + j * 36), ln, font=f3, fill=(167, 160, 200))
    pages.append(img)

    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=150.0, quality=88)
    return out


def spec_from_post(post: dict, series: str) -> dict:
    """Build a carousel spec from a buzz_posts.json / claude_posts.json entry."""
    desc = post["yt_description"]
    first = desc.split("\n")[0].strip()
    paras = [x.strip() for x in desc.split("\n\n")]
    takeaway = paras[1] if len(paras) > 1 and not _STEP.match(paras[1]) else first
    ep = post["episode"]
    return {"series": series, "label": f"{SERIES[series]['name']} TIP {ep}", "title": first,
            "kicker": post.get("cluster", ""), "steps": parse_steps(desc), "takeaway": takeaway,
            "cta": f"Follow theAIgurukul for a {SERIES[series]['name'].title()} tip every day."}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", required=True)
    ap.add_argument("--episode", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    posts = json.load(open(a.posts, encoding="utf-8"))
    post = next((p for p in posts if p["episode"] == a.episode), None)
    if not post:
        sys.exit(f"episode {a.episode} not found in {a.posts}")
    series = "kiro" if 101 <= a.episode <= 150 else "claude"
    print(build_carousel(spec_from_post(post, series), a.out))


if __name__ == "__main__":
    main()
