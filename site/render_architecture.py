"""Render the mermaid diagrams in ARCHITECTURE.md to architecture.png.

    python site/render_architecture.py

The previous architecture.png was made by hand, so it drifted from the source
the moment a diagram changed. This reads the fenced ```mermaid blocks straight
out of ARCHITECTURE.md, so the picture cannot disagree with the document.
"""
from __future__ import annotations

import base64
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "ARCHITECTURE.md"
PNG = ROOT / "architecture.png"
# The UMD bundle, deliberately. The ESM build (mermaid.esm.min.mjs) code-splits
# and dynamically imports its diagram modules RELATIVE TO ITS OWN URL, so
# inlining it as a data: URI leaves those imports with no base to resolve
# against and mermaid.run() never settles - it just hangs until the timeout.
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"

TITLES = ["Scenario 1 — the managed path (what you get for free)",
          "Scenario 2 — capture, store, detect, act",
          "Telemetry contract"]


def main() -> int:
    blocks = re.findall(r"```mermaid\n(.*?)```", SRC.read_text(), re.S)
    if not blocks:
        print("no mermaid blocks found", file=sys.stderr)
        return 1
    print(f"  {len(blocks)} diagrams from ARCHITECTURE.md")

    # Fetch once to a sibling file: kept off the network at screenshot time, and
    # a real file gives the bundle a base URL to resolve anything it needs.
    lib_path = ROOT / "site" / ".mermaid.min.js"
    lib_path.write_bytes(urllib.request.urlopen(MERMAID, timeout=60).read())

    panels = "".join(
        f'<section><h2>{i+1}. {TITLES[i] if i < len(TITLES) else ""}</h2>'
        f'<pre class="mermaid">{b}</pre></section>'
        for i, b in enumerate(blocks))

    html = f"""<!doctype html><meta charset="utf-8">
<style>
 body{{margin:0;background:#fcfcfb;font:14px/1.5 system-ui,sans-serif;padding:40px}}
 h1{{font-size:26px;margin:0 0 4px}} .sub{{color:#52514e;margin:0 0 28px;font-size:14px}}
 section{{margin-bottom:38px}}
 body{{width:max-content;min-width:100%}}
 h2{{font-size:15px;color:#52514e;font-weight:600;margin:0 0 10px}}
 .mermaid{{background:#fff;border:1px solid rgba(11,11,11,.10);border-radius:10px;
   padding:20px;margin:0;overflow:visible}}
 /* Mermaid stamps max-width:100% on the SVG, so a wide flowchart is squeezed
    into the container and its labels become unreadable - which is the entire
    point of exporting a PNG. Let each diagram take its natural size and size
    the viewport to the widest one instead. */
 .mermaid svg{{max-width:none !important;width:auto !important;height:auto}}
</style>
<h1>Agent Trajectory Monitoring — architecture</h1>
<p class="sub">Generated from ARCHITECTURE.md by site/render_architecture.py</p>
{panels}
<script src="./.mermaid.min.js"></script>
<script>
mermaid.initialize({{startOnLoad:false, theme:"base", themeVariables:{{
  fontFamily:"system-ui, -apple-system, Segoe UI, sans-serif", fontSize:"14px",
  primaryColor:"#eef3fa", primaryTextColor:"#0b0b0b", primaryBorderColor:"#2a78d6",
  lineColor:"#898781", secondaryColor:"#f4f3ef", tertiaryColor:"#fcfcfb"}}}});
mermaid.run({{querySelector:".mermaid"}})
  .then(()=>{{window.__done = true;}})
  .catch(e=>{{window.__err = String(e);}});
</script>"""

    tmp = ROOT / "site" / ".architecture.html"
    tmp.write_text(html)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": 1500, "height": 1200},
                            device_scale_factor=2)
            errs: list[str] = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto(tmp.as_uri())
            pg.wait_for_function("window.__done === true || window.__err",
                                 timeout=60000)
            if pg.evaluate("window.__err"):
                print(f"  mermaid error: {pg.evaluate('window.__err')}",
                      file=sys.stderr)
                return 1
            pg.wait_for_timeout(400)
            svgs = pg.eval_on_selector_all(".mermaid svg", "e=>e.length")
            if errs or svgs != len(blocks):
                print(f"  render failed: {svgs}/{len(blocks)} svgs, errors={errs}",
                      file=sys.stderr)
                return 1
            widest = pg.evaluate(
                "Math.ceil(Math.max(...[...document.querySelectorAll('.mermaid svg')]"
                ".map(s=>s.getBoundingClientRect().width)))")
            pg.set_viewport_size({"width": min(max(widest + 140, 1400), 4000),
                                  "height": 1200})
            pg.wait_for_timeout(400)
            print(f"  widest diagram {widest}px -> canvas "
                  f"{min(max(widest + 140, 1400), 4000)}px")
            pg.screenshot(path=str(PNG), full_page=True)
            b.close()
    finally:
        tmp.unlink(missing_ok=True)
        lib_path.unlink(missing_ok=True)
    print(f"  wrote {PNG}  ({PNG.stat().st_size//1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
