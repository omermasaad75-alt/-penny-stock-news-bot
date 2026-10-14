"""إعادة إنتاج الأيقونات (اختياري: pip install Pillow؛ لا يلزم أثناء المسح)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def build(size: int, name: str) -> None:
    scale = 4
    n = size * scale
    im = Image.new("RGB", (n, n), "#080b11")
    d = ImageDraw.Draw(im)
    for y in range(n):
        t = y / n
        d.line((0, y, n, y), fill=(int(38-22*t), int(29+13*t), int(78+12*t)))
    # Support line with a sweep below it and an upward reclaim.
    d.line((n*.15, n*.77, n*.85, n*.77), fill="#586c92", width=max(2, int(n*.012)))
    points = [(n*.17,n*.62),(n*.32,n*.70),(n*.44,n*.84),(n*.56,n*.72),(n*.70,n*.59),(n*.82,n*.46)]
    d.line(points[:3], fill="#f85149", width=max(3,int(n*.026)))
    d.line(points[2:], fill="#3fb950", width=max(3,int(n*.026)))
    d.polygon([(n*.82,n*.46),(n*.70,n*.49),(n*.82,n*.58)], fill="#3fb950")
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(n*.35))
    box = d.textbbox((0,0), "RS", font=font)
    d.text(((n-box[2]+box[0])/2, n*.12-box[1]), "RS", font=font, fill="#e6edf7")
    # Full opaque square: OS applies its own mask when pinned to the home screen.
    path = ROOT / "web/icons" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    im.resize((size,size), Image.Resampling.LANCZOS).save(path)


if __name__ == "__main__":
    for size, name in ((32,"favicon-32.png"),(180,"apple-touch-icon.png"),(192,"icon-192.png"),(512,"icon-512.png")):
        build(size,name)
