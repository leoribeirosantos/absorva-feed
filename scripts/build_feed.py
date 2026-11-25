"""Gera o feed RSS do Absorva (absorva.cc) a partir das páginas publicadas.

Lê o sitemap, abre cada post em /posts/, extrai título, tipo, data, capa e
texto, e escreve _site/feed.xml (RSS 2.0) e _site/index.html.
"""

import os
import re
import sys
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

SITE = "https://absorva.cc"
FEED_TITLE = "Absorva"
FEED_DESCRIPTION = "Ensaios de Léo Ribeiro sobre estratégia de marca, cultura e eficácia em marketing."
FEED_LANGUAGE = "pt-BR"
MAX_ITEMS = 30
OUT_DIR = Path("_site")
BRT = timezone(timedelta(hours=-3))
FEED_URL = os.environ.get("FEED_URL", "feed.xml")

ALLOWED_TAGS = {
    "p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i",
    "u", "a", "ul", "ol", "li", "blockquote", "img", "figure", "figcaption",
    "code", "pre", "hr",
}
ALLOWED_ATTRS = {"a": {"href"}, "img": {"src", "alt"}}

session = requests.Session()
session.headers["User-Agent"] = "absorva-feed-builder (+https://absorva.cc)"


def get(url: str) -> str:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def post_urls() -> list[str]:
    xml = get(f"{SITE}/sitemap.xml")
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    return [u.strip() for u in urls if "/posts/" in u]


def meta(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    return tag.get("content") if tag and tag.get("content") else None


def clean_html(node) -> str:
    """Mantém só a marcação de texto e remove classes, estilos e scripts."""
    for bad in node.find_all(["script", "style", "noscript", "svg", "iframe"]):
        bad.decompose()
    for tag in node.find_all(True):
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
            continue
        allowed = ALLOWED_ATTRS.get(tag.name, set())
        tag.attrs = {k: v for k, v in tag.attrs.items() if k in allowed}
        if tag.name == "a" and tag.get("href", "").startswith("/"):
            tag["href"] = SITE + tag["href"]
    html = node.decode_contents()
    return re.sub(r"\s+\n", "\n", html).strip()

def parse_post(url: str) -> dict | None:
    soup = BeautifulSoup(get(url), "html.parser")

    title = meta(soup, "og:title") or (soup.title.string if soup.title else url)
    title = re.sub(r"\s+[—-]\s+Absorva\s*$", "", title).strip()

    intro = soup.find(attrs={"data-framer-name": "Post introduction"})
    intro_lines = []
    if intro:
        intro_lines = [s.strip() for s in intro.get_text("\n").split("\n") if s.strip()]

    date = None
    category = None
    for i, line in enumerate(intro_lines):
        m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", line)
        if m:
            d, mo, y = map(int, m.groups())
            date = datetime(y, mo, d, 9, 0, tzinfo=BRT)
            if i > 0:
                category = intro_lines[i - 1]
            break

    content_html = ""
    excerpt = ""
    texto = soup.find(attrs={"data-framer-name": "Texto"})
    if texto:
        rich = texto.find(attrs={"data-framer-component-type": "RichTextContainer"}) or texto
        blocks = rich.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote"])
        raw = " ".join(b.get_text() for b in blocks) if blocks else rich.get_text(" ")
        excerpt_text = " ".join(raw.split())
        excerpt = excerpt_text[:280].rsplit(" ", 1)[0] + "…" if len(excerpt_text) > 280 else excerpt_text
        content_html = clean_html(rich)

    image = meta(soup, "og:image")

    return {
        "url": url,
        "title": title,
        "date": date,
        "category": category.capitalize() if category else None,
        "excerpt": excerpt,
        "content": content_html,
        "image": image,
    }


def cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def build_rss(items: list[dict]) -> str:
    now = format_datetime(datetime.now(timezone.utc))
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
        'xmlns:atom="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">',
        "<channel>",
        f"<title>{escape(FEED_TITLE)}</title>",
        f"<link>{SITE}</link>",
        f"<description>{escape(FEED_DESCRIPTION)}</description>",
        f"<language>{FEED_LANGUAGE}</language>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        f'<atom:link href="{escape(FEED_URL)}" rel="self" type="application/rss+xml"/>',
    ]
    for it in items:
        out.append("<item>")
        out.append(f"<title>{escape(it['title'])}</title>")
        out.append(f"<link>{escape(it['url'])}</link>")
        out.append(f'<guid isPermaLink="true">{escape(it["url"])}</guid>')
        if it["date"]:
            out.append(f"<pubDate>{format_datetime(it['date'])}</pubDate>")
        if it["category"]:
            out.append(f"<category>{escape(it['category'])}</category>")
        out.append(f"<description>{cdata(it['excerpt'] or it['title'])}</description>")
        body = it["content"]
        if it["image"]:
            body = f'<p><img src="{escape(it["image"])}" alt=""/></p>' + body
            out.append(f'<media:content url="{escape(it["image"])}" medium="image"/>')
        body += f'<p><a href="{escape(it["url"])}">Ler no Absorva</a></p>'
        out.append(f"<content:encoded>{cdata(body)}</content:encoded>")
        out.append("</item>")
    out += ["</channel>", "</rss>"]
    return "\n".join(out) + "\n"


INDEX_HTML = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Feed do Absorva</title>
<link rel="alternate" type="application/rss+xml" title="Absorva" href="feed.xml">
<style>body{font-family:system-ui,sans-serif;max-width:560px;margin:64px auto;padding:0 16px;color:#111;line-height:1.5}
a{color:#111}code{background:#f0f0f0;padding:2px 6px;border-radius:4px}</style>
</head><body>
<h1>Feed do Absorva</h1>
<p>Para receber os textos do <a href="https://absorva.cc">Absorva</a> no seu leitor de RSS, copie este endereço:</p>
<p><code>FEED_URL</code></p>
<p><a href="feed.xml">Abrir o feed</a></p>
</body></html>
"""


def main() -> int:
    urls = post_urls()
    if not urls:
        print("Nenhum post encontrado no sitemap.", file=sys.stderr)
        return 1
    items = []
    for u in urls:
        try:
            p = parse_post(u)
            if p:
                items.append(p)
                print(f"ok  {p['date'].date() if p['date'] else '????-??-??'}  {p['title']}")
        except Exception as e:  # um post com problema não derruba o feed todo
            print(f"erro em {u}: {e}", file=sys.stderr)
    if not items:
        return 1
    oldest = datetime(1970, 1, 1, tzinfo=timezone.utc)
    items.sort(key=lambda x: x["date"] or oldest, reverse=True)
    items = items[:MAX_ITEMS]

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "feed.xml").write_text(build_rss(items), encoding="utf-8")
    (OUT_DIR / "index.html").write_text(INDEX_HTML.replace("FEED_URL", FEED_URL), encoding="utf-8")
    print(f"\n{len(items)} posts no feed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
