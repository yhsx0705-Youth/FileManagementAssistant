"""Convert the approved artwork into application icon formats (Pillow, build time only)."""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "app-icon-source.png"
DESTINATION = ROOT / "quick_access" / "assets"


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    with Image.open(SOURCE) as original:
        image = original.convert("RGBA")
    image.resize((256, 256), Image.Resampling.LANCZOS).save(DESTINATION / "app-256.png")
    image.save(DESTINATION / "app.ico", format="ICO", bitmap_format="bmp",
               sizes=[(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)])

    ball = image.resize((44, 44), Image.Resampling.LANCZOS)
    # Tk's Windows color-key transparency cannot blend against the desktop.
    # A binary outer mask avoids a magenta fringe on the transparent canvas.
    ball.putalpha(ball.getchannel("A").point(lambda value: 255 if value >= 128 else 0))
    ball.save(DESTINATION / "app-ball.png")
    print(f"Icon assets saved to {DESTINATION}")


if __name__ == "__main__":
    main()
