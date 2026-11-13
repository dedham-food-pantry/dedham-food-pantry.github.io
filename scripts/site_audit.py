#!/usr/bin/env python3
"""Site URL audit for dedhamfoodpantry.org.

Run after `hugo` has built the site. Writes a CSV listing every URL with its
last-updated date, how it is linked, and a 1-5 "findability" score for how
easily a visitor browsing the site would come across it. The CSV is published
at /site-audit.csv and pulled into a Google Sheet with IMPORTDATA.

Usage: python3 scripts/site_audit.py <built site dir> <output csv>
Needs: hugo and git on PATH, run from the repository root (full git history
for accurate dates).
"""
import csv
import html
import io
import os
import re
import subprocess
import sys
from collections import defaultdict, deque

SITE = "https://dedhamfoodpantry.org"
LABELS = {5: "Very easy", 4: "Easy", 3: "Moderate", 2: "Hard", 1: "Very hard"}
NEWS = "/about/news/"


def main(build, out_path):
    pages = {}
    for root, _, files in os.walk(build):
        if "index.html" in files:
            url = "/" + os.path.relpath(root, build).replace(os.sep, "/").strip("./") + "/"
            url = re.sub("//+", "/", url)
            with open(os.path.join(root, "index.html"), encoding="utf8") as f:
                pages[url] = f.read()

    def norm(u, base):
        u = html.unescape(u).split("#")[0]
        if u.startswith(("http://", "https://")):
            if not re.match(r"https?://(www\.)?dedhamfoodpantry\.org", u):
                return None
            u = re.sub(r"https?://(www\.)?dedhamfoodpantry\.org", "", u) or "/"
        if not u or u.startswith(("mailto:", "tel:")):
            return None
        if not u.startswith("/"):
            u = base + u
        if not os.path.splitext(u)[1] and not u.endswith("/"):
            u += "/"
        return u if u in pages else None

    def hrefs(h):
        return re.findall(r'<a\s[^>]*href="([^"]*)"', h)

    def region(h, pattern):
        m = re.search(pattern, h, re.S)
        return m.group(0) if m else ""

    redirect = {}
    for u, h in pages.items():
        m = re.search(r'http-equiv="?refresh"? content="0; url=([^"]+)"', h)
        if m:
            redirect[u] = html.unescape(m.group(1))

    home = pages.get("/", "")
    menu = {norm(l, "/") for l in hrefs(region(home, r'<nav class="menu".*?</nav>'))} - {None}
    footer = {norm(l, "/") for l in hrefs(region(home, r"<footer.*?</footer>"))} - {None}

    inbound, out_all = defaultdict(set), defaultdict(set)
    for u, h in pages.items():
        if u in redirect:
            continue
        for l in hrefs(region(h, r"<main.*?</main>")):
            t = norm(l, u)
            if t and t != u:
                inbound[t].add(u)
        for l in hrefs(h):
            t = norm(l, u)
            if t and t != u:
                out_all[u].add(t)

    depth, queue = {"/": 0}, deque(["/"])
    while queue:
        u = queue.popleft()
        for t in out_all[u]:
            if t not in depth:
                depth[t] = depth[u] + 1
                queue.append(t)

    sitemap_xml = ""
    if os.path.exists(os.path.join(build, "sitemap.xml")):
        sitemap_xml = open(os.path.join(build, "sitemap.xml"), encoding="utf8").read()
    sitemap = {re.sub(r"^https?://[^/]+", "", s) or "/" for s in re.findall(r"<loc>([^<]+)</loc>", sitemap_xml)}

    news_order = []
    for m in re.findall(r'href="([^"]*' + re.escape(NEWS) + r'\d{4}[^"]*)"', region(pages.get(NEWS, ""), r"<main.*?</main>")):
        p = norm(m, NEWS)
        if p and p not in news_order:
            news_order.append(p)
    n_news = len(news_order)

    listing = subprocess.run(["hugo", "list", "all"], capture_output=True, text=True).stdout
    source = {r["permalink"]: r for r in csv.DictReader(io.StringIO(listing))}

    def git_date(path):
        return subprocess.run(["git", "log", "-1", "--format=%cs", "--", path],
                              capture_output=True, text=True).stdout.strip()

    def title(h):
        m = re.search(r"<title>([^<]*)</title>", h)
        return html.unescape(m.group(1)).replace(" - Dedham Food Pantry", "").strip() if m else ""

    rows = []
    for u in sorted(pages):
        h, src = pages[u], source.get(u)
        if u in redirect:
            kind = "Redirect"
        elif re.search(r"/page/\d+/$", u):
            kind = "List page (pagination)"
        elif u.startswith(("/tags/", "/categories/")):
            kind = "Auto-generated (empty)"
        elif src and src["kind"] == "section":
            kind = "Section page"
        elif u.startswith(NEWS) and u != NEWS:
            kind = "News post"
        else:
            kind = "Page"
        path = src["path"] if src else ""
        d, n_in = depth.get(u), len(inbound.get(u, ()))
        place = " + ".join(x for x, s in (("Main menu", menu), ("Footer", footer)) if u in s)

        if kind == "Redirect":
            score, why = 1, f"Forwards to {redirect[u]}. Only reached via old links or bookmarks."
        elif kind == "Auto-generated (empty)":
            score, why = 1, "Empty page generated automatically by the site builder; nothing links to it."
        elif kind == "List page (pagination)":
            score, why = 1, "Extra page of a list; few visitors click through to it."
        elif u == "/":
            score, why = 5, "Home page: the address people type and the top Google result."
        elif u in menu:
            score, why = 5, "In the main menu on every page."
        elif u in footer:
            score, why = 4, "Linked from the footer on every page."
        elif kind == "News post" and u in news_order:
            pos = news_order.index(u) + 1
            if pos <= 5:
                score, why = 3, f"Item {pos} of {n_news} on the Pantry News page. Near the top, so easy to spot."
            elif pos <= 15:
                score, why = 2, f"Item {pos} of {n_news} on the Pantry News page. Visitors must scroll well down the list."
            else:
                score, why = 1, f"Item {pos} of {n_news} on the Pantry News page. Near the bottom; few visitors scroll this far."
        elif d is None:
            score, why = 1, "Nothing on the site links to it. Only reachable by search or a direct link."
        elif d <= 2 and n_in >= 2:
            score, why = 4, f"{d} click(s) from the home page; linked from {n_in} pages."
        elif d <= 2:
            score, why = 3, f"{d} click(s) from the home page; linked from {n_in} page."
        elif d == 3:
            score, why = 2, "Three clicks from the home page."
        else:
            score, why = 1, f"{d} clicks from the home page."

        rows.append({
            "URL": SITE + u,
            "Page title": "" if kind == "Redirect" else (title(h) or (src or {}).get("title", "")),
            "Type": kind,
            "Last updated": git_date(path) if path else "n/a (generated)",
            "First published": (src or {}).get("date", "")[:10] if src and not src["date"].startswith("0001") else "",
            "Linked from menu/footer": place or "-",
            "Clicks from home page": "Not linked" if d is None else d,
            "Pages linking to it": n_in,
            "In Google sitemap": "Yes" if u in sitemap else "No",
            "Findability (1-5)": score,
            "Findability": LABELS[score],
            "Why": why,
        })

    order = {"Page": 0, "Section page": 0, "News post": 1, "Redirect": 2,
             "List page (pagination)": 3, "Auto-generated (empty)": 4}
    rows.sort(key=lambda r: (-r["Findability (1-5)"], order[r["Type"]], r["URL"]))
    with open(out_path, "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} URLs to {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
