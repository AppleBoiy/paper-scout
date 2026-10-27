#!/usr/bin/env python3
"""Build dist/<skill>.zip for Agensi: the HTML templates are embedded into the script so it runs as one file."""
import json, shutil, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = ROOT.name if (ROOT / "SKILL.md").exists() else "skill"
DIST = ROOT / "dist"
INCLUDE = ["SKILL.md", "LICENSE", "references"]


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    out = DIST / NAME
    (out / "scripts").mkdir(parents=True)
    for item in INCLUDE:
        src = ROOT / item
        if src.is_dir():
            shutil.copytree(src, out / item)
        elif src.exists():
            shutil.copy(src, out / item)
    templates = {p.name: p.read_text("utf-8") for p in sorted((ROOT / "assets").glob("*.html"))}
    marker = '\n\nif __name__ == "__main__":'
    for script in sorted((ROOT / "scripts").glob("*.py")):
        code = script.read_text("utf-8")
        if templates and marker in code:
            block = ("\n\n# HTML templates, embedded so this script runs as a single file.\n"
                     "EMBEDDED_TEMPLATES = " + json.dumps(templates, ensure_ascii=False) + "\n")
            code = code.replace(marker, block + marker, 1)
        (out / "scripts" / script.name).write_text(code, "utf-8")
    zpath = DIST / f"{NAME}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(DIST))
    print(f"built {zpath.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
