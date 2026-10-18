"""Render a tailored CV to PDF from cv_template.html for a specific job application.

Usage: python scripts/generate_cv.py <slug> <summary_file> <competencies_file>

summary_file: plain text, one or more paragraphs separated by blank lines.
competencies_file: lines of "Label: item, item, item" (category per line, in display order).
Output: data/cv_exports/<slug>.pdf
"""
import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE = _ROOT / "scripts" / "cv_template.html"
_OUT_DIR = _ROOT / "data" / "cv_exports"

_CHROME_CANDIDATES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]


def _find_chrome() -> str:
    for name in _CHROME_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(f"No Chrome/Chromium binary found (tried: {', '.join(_CHROME_CANDIDATES)})")


def _render_summary(raw_text: str) -> str:
    paragraphs = [p.strip() for p in raw_text.strip().split("\n\n") if p.strip()]
    return "\n".join(f"    <p>{html.escape(p)}</p>" for p in paragraphs)


def _render_competencies(raw_text: str) -> str:
    lines = []
    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        label, _, rest = line.partition(":")
        lines.append(f"      <li><b>{html.escape(label.strip())}:</b> {html.escape(rest.strip())}</li>")
    return "\n".join(lines)


def generate(slug: str, summary_text: str, competencies_text: str) -> Path:
    template = _TEMPLATE.read_text(encoding="utf-8")
    filled = template.replace("__SUMMARY__", _render_summary(summary_text))
    filled = filled.replace("__COMPETENCIES__", _render_competencies(competencies_text))

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = _OUT_DIR / f"{slug}.html"
    pdf_path = _OUT_DIR / f"{slug}.pdf"
    html_path.write_text(filled, encoding="utf-8")

    chrome = _find_chrome()
    subprocess.run(
        [
            chrome, "--headless", "--disable-gpu", "--no-sandbox",
            f"--print-to-pdf={pdf_path}", "--print-to-pdf-no-header",
            "--no-pdf-header-footer", "--virtual-time-budget=5000",
            f"file://{html_path}",
        ],
        check=True, capture_output=True, timeout=30,
    )
    html_path.unlink()
    return pdf_path


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    slug_arg, summary_file, competencies_file = sys.argv[1:4]
    slug_arg = re.sub(r"[^a-zA-Z0-9_-]+", "_", slug_arg)
    summary = Path(summary_file).read_text(encoding="utf-8")
    competencies = Path(competencies_file).read_text(encoding="utf-8")
    out = generate(slug_arg, summary, competencies)
    print(f"Wrote {out}")
