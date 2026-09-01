"""Render LAB-GUIDE.md into the static site served at trajectory.cedemo.app.

    python site/build.py          # -> site/public/

The guide is the single source of truth: edit LAB-GUIDE.md, rebuild, redeploy.
Nothing about the content lives in this file except presentation.
"""
from __future__ import annotations

import html
import pathlib
import re

import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "public"
SRC = ROOT / "LAB-GUIDE.md"

CSS = """
:root{color-scheme:light;
 --surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);--accent:#2a78d6;
 --code-bg:#f4f3ef;--warn:#fab219;--good:#0ca30c;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
 --surface:#1a1a19;--plane:#0d0d0d;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--accent:#3987e5;
 --code-bg:#141413;}}
:root[data-theme="dark"]{color-scheme:dark;
 --surface:#1a1a19;--plane:#0d0d0d;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;
 --grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--accent:#3987e5;
 --code-bg:#141413;}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:20px}
body{margin:0;background:var(--plane);color:var(--ink);
 font:15px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif;
 -webkit-text-size-adjust:100%}
.layout{display:grid;grid-template-columns:288px minmax(0,1fr);gap:0;
 max-width:1400px;margin:0 auto}
nav{position:sticky;top:0;height:100vh;overflow-y:auto;padding:26px 18px 60px;
 border-right:1px solid var(--ring);background:var(--surface)}
nav .brand{font-weight:600;font-size:15px;margin-bottom:2px}
nav .tag{color:var(--muted);font-size:12px;margin-bottom:16px}
nav a{display:block;color:var(--ink-2);text-decoration:none;font-size:13.5px;
 padding:4px 8px;border-radius:6px;border-left:2px solid transparent}
nav a:hover{background:var(--code-bg);color:var(--ink)}
nav a.h2{font-weight:600;color:var(--ink);margin-top:12px}
nav a.h3{padding-left:18px;font-size:13px}
nav a.active{color:var(--accent);border-left-color:var(--accent);background:var(--code-bg)}
main{padding:34px 44px 120px;max-width:900px;min-width:0}
h1{font-size:30px;line-height:1.2;margin:0 0 6px}
img{max-width:100%;height:auto;border:1px solid var(--ring);border-radius:8px;
 background:#fff;margin:14px 0}
h2{font-size:22px;margin:44px 0 10px;padding-top:10px;border-top:1px solid var(--grid)}
h3{font-size:17px;margin:28px 0 8px}
h4{font-size:15px;margin:22px 0 6px;color:var(--ink-2)}
p,li{color:var(--ink)}
a{color:var(--accent)}
hr{border:0;border-top:1px solid var(--grid);margin:34px 0}
/* The guide uses --- before most h2s, and h2 already draws its own rule.
   Together they render as two lines with a gap. Let the heading win. */
hr + h2{border-top:0;padding-top:0;margin-top:26px}
main > hr:last-of-type{display:none}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;
 background:var(--code-bg);padding:1px 5px;border-radius:4px;
 overflow-wrap:anywhere}
pre{background:var(--code-bg);border:1px solid var(--ring);border-radius:8px;
 padding:14px 16px;overflow-x:auto;position:relative}
pre code{background:none;padding:0;font-size:13px;line-height:1.55}
.copy{position:absolute;top:8px;right:8px;font:500 11px/1 system-ui,sans-serif;
 color:var(--ink-2);background:var(--surface);border:1px solid var(--ring);
 border-radius:5px;padding:5px 8px;cursor:pointer;opacity:0;transition:opacity .12s}
pre:hover .copy,.copy:focus{opacity:1}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:14px 0;display:block;
 overflow-x:auto}
th{text-align:left;font-weight:600;padding:8px 12px 8px 0;border-bottom:1px solid var(--axis);
 white-space:nowrap}
td{padding:8px 12px 8px 0;border-bottom:1px solid var(--grid);vertical-align:top}
blockquote{margin:16px 0;padding:12px 16px;border-left:3px solid var(--accent);
 background:var(--code-bg);border-radius:0 8px 8px 0}
blockquote p{margin:0}
.path{color:var(--ink-2);background:var(--code-bg);white-space:nowrap}
.top{display:none}
@media(max-width:920px){
 .layout{grid-template-columns:1fr}
 nav{position:static;height:auto;border-right:0;border-bottom:1px solid var(--ring);
  padding:16px}
 nav .links{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:2px}
 nav a.h3{display:none}
 main{padding:24px 18px 80px}
 h1{font-size:24px}
}
"""

JS = """
for(const pre of document.querySelectorAll('main pre')){
  const b=document.createElement('button');b.className='copy';b.textContent='copy';
  b.addEventListener('click',()=>{navigator.clipboard.writeText(pre.innerText.replace(/^copy\\n/,''));
    b.textContent='copied';setTimeout(()=>b.textContent='copy',1200);});
  pre.appendChild(b);
}
// Scroll-spy by position, not IntersectionObserver. With a -75% rootMargin
// several headings can report "intersecting" in one callback and the LAST one
// wins, so jumping to 2.4 could leave 2.3.3 highlighted. Picking the last
// heading above the fold is unambiguous.
const links=[...document.querySelectorAll('nav a')];
const map=new Map(links.map(a=>[a.getAttribute('href').slice(1),a]));
const heads=[...document.querySelectorAll('main h2[id],main h3[id]')];
const navEl=document.querySelector('nav');
let raf=0;
function spy(){
  raf=0;
  let cur=heads[0];
  // 60px, not 90: an h3 often sits ~85px under its h2, so a looser threshold
  // means landing on "Lab 2.4" instantly highlights "2.4.1" instead.
  for(const h of heads){ if(h.getBoundingClientRect().top<=60) cur=h; else break; }
  links.forEach(a=>a.classList.remove('active'));
  const a=map.get(cur?.id); if(!a) return;
  a.classList.add('active');
  // Scroll the NAV only, by arithmetic. scrollIntoView() walks every scrollable
  // ancestor including the window, so keeping the active link visible dragged
  // the whole page back to the top - every nav click looked broken.
  const box=navEl.getBoundingClientRect(), r=a.getBoundingClientRect();
  if(r.top<box.top||r.bottom>box.bottom)
    navEl.scrollTop += r.top - box.top - box.height/2;
}
addEventListener('scroll',()=>{ if(!raf) raf=requestAnimationFrame(spy); },{passive:true});
spy();
"""


def slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", s) or "section"


BANNER = """<div style="position:sticky;top:0;z-index:99;background:var(--surface,#fcfcfb);
border-bottom:1px solid rgba(11,11,11,.10);padding:10px 20px;font:13px/1.4 system-ui,sans-serif">
<a href="/" style="color:#2a78d6;text-decoration:none">&larr; Lab guide</a>
<span style="color:#898781;margin-left:14px">{label} &mdash; live output of the pipeline you build in Labs 2.4&ndash;2.5</span>
</div>"""
MARK = "<!--demo-banner-->"


def stage_demos() -> None:
    """Add a back-link to the generated dashboards, if they have been dropped in.

    labs/dashboard.py needs BigQuery, so it cannot run here; the deploy script
    generates the files first and this only adds the chrome. Idempotent, so
    rebuilding twice does not stack banners.
    """
    for name, label in (("demo.html", "Scripted corpus &middot; 2,006 sessions"),
                        ("demo-real.html", "Live-model corpus &middot; 1,992 sessions")):
        f = OUT / name
        if not f.exists():
            continue
        html_text = f.read_text()
        if MARK in html_text:
            continue
        f.write_text(html_text.replace(
            "<body>", "<body>" + MARK + BANNER.format(label=label), 1))
        print(f"  staged {name}")


def build() -> None:
    md = markdown.Markdown(extensions=["tables", "fenced_code", "attr_list",
                                       "sane_lists", "toc"],
                           extension_configs={"toc": {"slugify":
                                                      lambda v, s: slug(v)}})
    body = md.convert(SRC.read_text())

    # Repo-relative links (sql/..., labs/...) have no target on a static site -
    # attendees read them in their own checkout. Render them as paths rather
    # than as links that 404. Absolute paths (/demo) ARE served by this site,
    # so they must survive: without the leading-slash exclusion the demo links
    # in the briefing turn into dead text.
    body = re.sub(r'<a href="(?!https?:|#|/)([^"]+)">([^<]+)</a>',
                  r'<code class="path">\2</code>', body)

    nav = []
    for item in md.toc_tokens:                       # h1
        for h2 in item.get("children", []):
            nav.append(f'<a class="h2" href="#{h2["id"]}">'
                       f'{html.escape(h2["name"])}</a>')
            for h3 in h2.get("children", []):
                nav.append(f'<a class="h3" href="#{h3["id"]}">'
                           f'{html.escape(h3["name"])}</a>')

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent trajectory monitoring — lab guide</title>
<link rel="icon" href="/favicon.ico">
<style>{CSS}</style></head><body>
<div class="layout">
<nav><div class="brand">Agent trajectory monitoring</div>
<div class="tag">7-hour hands-on lab</div>
<div class="links">{''.join(nav)}</div></nav>
<main>{body}</main>
</div>
<script>{JS}</script></body></html>"""

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(page)
    stage_demos()
    print(f"  wrote {OUT/'index.html'}  ({len(nav)} nav entries, "
          f"{len(page)//1024} KB)")


if __name__ == "__main__":
    build()
