#!/usr/bin/env python3
"""Render an HTML output to README / listing screenshots: light, dark and mobile.

    pip install playwright && python -m playwright install chromium
    python tools/screenshot.py path/to/output.html            # -> docs/screenshots/<name>-{light,dark,mobile}.png
    python tools/screenshot.py out.html --name map --click "#vTime" --height 1600
"""
import argparse
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


async def shoot(html: Path, name: str, click: str | None, height: int, wait: int) -> None:
    from playwright.async_api import async_playwright
    out = ROOT / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    shots = [("light", 1400, height, "light", 1), ("dark", 1400, height, "dark", 1), ("mobile", 390, 844, "light", 2)]
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for label, w, h, scheme, scale in shots:
            page = await browser.new_page(viewport={"width": w, "height": h}, color_scheme=scheme, device_scale_factor=scale)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(html.resolve().as_uri())
            await page.wait_for_timeout(wait)
            if click:
                await page.click(click)
                await page.wait_for_timeout(wait)
            path = out / f"{name}-{label}.png"
            await page.screenshot(path=str(path))
            print(f"wrote {path.relative_to(ROOT)}" + (f"  (page errors: {errors})" if errors else ""))
            await page.close()
        await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("html", type=Path)
    ap.add_argument("--name", help="file name prefix (default: the HTML file's stem)")
    ap.add_argument("--click", help="CSS selector to click before capturing, e.g. a view toggle")
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--wait", type=int, default=3000, help="ms to wait for layout/animation")
    a = ap.parse_args()
    asyncio.run(shoot(a.html, a.name or a.html.stem, a.click, a.height, a.wait))


if __name__ == "__main__":
    main()
