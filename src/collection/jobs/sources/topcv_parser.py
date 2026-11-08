"""Offline TopCV extraction. Unknown values remain unknown, never inferred as zero."""

import re
import unicodedata
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

SCHEMA_VERSION = 7
FIELDS = [
    "job_id", "source", "source_url", "collected_at", "job_title",
    "company", "location", "experience", "salary_text", "posted_text",
]


def folded(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.lower())
                   if unicodedata.category(c) != "Mn").replace("đ", "d")


def job_url(url, base="https://www.topcv.vn"):
    parts = urlsplit(urljoin(base, url))
    if parts.scheme != "https" or parts.hostname not in {"www.topcv.vn", "topcv.vn"}:
        return None
    if not (re.fullmatch(r"/viec-lam/[^/]+/\d+\.html", parts.path)
            or re.fullmatch(r"/brand/[^/]+/tuyen-dung/[^/]+-j\d+\.html", parts.path)):
        return None
    return urlunsplit(("https", "www.topcv.vn", parts.path, "", ""))


def parse_search(html, search_url, collected_at):
    """
    Only visible card fields are extracted; detail URLs are recorded, never opened.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows, seen = [], set()
    for card in soup.select('.job-list-search-result .job-item-search-result'):
        title = card.select_one('h3.title a[href]')
        if not title or not (url := job_url(title["href"], search_url)) or url in seen:
            continue
        seen.add(url)

        def text(selector):
            element = card.select_one(selector)
            return element.get_text(" ", strip=True) if element else ""

        row = dict.fromkeys(FIELDS, "")
        row.update(job_id=re.search(r"(?:/|-j)(\d+)\.html$", urlsplit(url).path).group(1),
                   source="topcv", source_url=url, collected_at=collected_at,
                   job_title=title.get_text(" ", strip=True), company=text('a.company'),
                   location=text('.label-content .address'),
                   experience=text('.label-content .exp'),
                   salary_text=text('.title-salary') or text('.label-content .salary'),
                   posted_text=text('.label-update'))
        if row["job_title"]:
            rows.append(row)
    return rows
