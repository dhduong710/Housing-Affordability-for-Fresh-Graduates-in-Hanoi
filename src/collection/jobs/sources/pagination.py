"""Expected page URLs and source end detection; browser advancement uses Next clicks."""

import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from collection.jobs.sources.topcv_parser import folded


def page_url(url, number):
    if not isinstance(number, int) or number < 1:
        raise ValueError('Page number must be a positive integer')
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname != 'www.topcv.vn':
        raise ValueError('Expected a public HTTPS TopCV URL')
    query = parse_qs(parts.query, keep_blank_values=True)
    query['page'] = [str(number)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ''))


def list_position(html):
    soup = BeautifulSoup(html, 'html.parser')
    counter = soup.select_one('#job-listing-paginate-text')
    match = re.search(r'(\d+)\s*/\s*(\d+)\s*(?:trang|pages?)',
                      counter.get_text(' ', strip=True) if counter else '', re.I)
    current, total = (int(match[1]), int(match[2])) if match else (None, None)
    root = soup.select_one('.job-list-search-result')
    cards = soup.select('.job-list-search-result .job-item-search-result h3.title a[href]')
    text = folded(root.get_text(' ', strip=True)) if root else ''
    empty = bool(root is not None and not cards and re.search(
        r'khong tim thay (?:viec lam|tin tuyen dung|ket qua)|khong co (?:viec lam|tin tuyen dung)(?: nao)?|no (?:jobs|results) found', text))
    return {'current_page': current, 'total_pages': total, 'explicit_empty': empty}


def next_numeric_page(html, url, number):
    position = list_position(html)
    if position['current_page'] is not None and position['current_page'] != number:
        raise ValueError(f'Wrong list page: expected {number}, observed {position["current_page"]}')
    if position['explicit_empty']:
        return None, {**position, 'end_reason': 'explicit_empty'}
    # The counter is informational: native Next determines whether we advance.
    soup = BeautifulSoup(html, 'html.parser')
    for control in soup.select('a[rel~="next"][data-href], a[rel~="next"][href]'):
        if not (control.get('data-href') or control.get('href') or '').strip():
            continue
        disabled = False
        for element in [control, *control.parents]:
            style = re.sub(r'\s+', '', element.get('style', '')).lower()
            if (element.has_attr('disabled') or element.has_attr('hidden')
                    or element.get('aria-disabled', '').lower() == 'true'
                    or 'disabled' in element.get('class', [])
                    or 'display:none' in style or 'visibility:hidden' in style):
                disabled = True
                break
        if not disabled:
            return page_url(url, number + 1), {**position, 'end_reason': None}
    return None, {**position, 'end_reason': 'no_enabled_next'}


def same_listing_page(first, second):
    """Compare public listing destinations without allowing silent filter changes."""
    def key(url):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.hostname != 'www.topcv.vn' or parts.username or parts.password or parts.port not in {None, 443}:
            raise ValueError('Not a public TopCV URL')
        query = parse_qs(parts.query, keep_blank_values=True)
        query.setdefault('page', ['1'])
        # Native AJAX pagination adds these defaults and a session tracking ID.
        # Non-default values still participate in scope comparison.
        for name, default in {'type_keyword': '1', 'disable_auto_detect_type_keyword': '1',
                              'saturday_status': '0'}.items():
            if query.get(name) == [default]:
                query.pop(name)
        query.pop('u_sr_id', None)
        page = query['page']
        if len(page) != 1 or not page[0].isdigit() or int(page[0]) < 1:
            raise ValueError('Invalid page parameter')
        query['page'] = [str(int(page[0]))]
        return parts.path, {name: sorted(values) for name, values in query.items()}
    try:
        return key(first) == key(second)
    except (ValueError, TypeError):
        return False
