from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
import urllib.request
import urllib.robotparser
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag


BASE = "https://batdongsan.com.vn"
LIST_URL = BASE + "/ban-can-ho-chung-cu-ha-noi/p{page}"
ROBOTS_URL = BASE + "/robots.txt"
USER_AGENT = "HousingDataCollection/1.0"
CARD_SELECTOR = "[data-product-id]"


def format_duration(seconds: float) -> str:
    total = max(0, round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes}m {secs}s" if hours else f"{minutes}m {secs}s"


class AccessBlocked(RuntimeError):
    pass


@dataclass
class Listing:
    source_site: str
    listing_id: str
    source_url: str
    page_url: str
    collected_at_utc: str
    title: str | None
    price_text: str | None
    area_text: str | None
    price_per_m2_text: str | None
    bedrooms_text: str | None
    bathrooms_text: str | None
    location_text: str | None
    description_text: str | None
    posted_date_text: str | None
    price_vnd: int | None
    area_m2: float | None
    price_per_m2_million: float | None
    bedrooms: int | None
    bathrooms: int | None
    district: str | None
    ward: str | None
    project_name: str | None
    floor_text: str | None
    furniture_text: str | None
    legal_status_text: str | None
    house_direction_text: str | None
    balcony_direction_text: str | None
    detail_price_text: str | None
    detail_area_text: str | None
    detail_bedrooms_text: str | None
    detail_bathrooms_text: str | None
    detail_text: str | None
    quality_flags: str


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value.replace("\u200e", " ")).strip(" \u00b7")
    return value or None


def number(value: str) -> float:
    value = value.replace(" ", "")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif value.count(".") == 1 and len(value.split(".")[1]) == 3:
        value = value.replace(".", "")
    else:
        value = value.replace(".", "") if value.count(".") > 1 else value
    return float(value)


def parse_price(value: str | None) -> int | None:
    if not value:
        return None
    lower = value.lower()
    if "/m" in lower or "thỏa thuận" in lower or "thoả thuận" in lower:
        return None
    pieces = re.findall(r"(\d[\d.,]*)\s*(tỷ|ty|triệu|tr\b)", lower)
    if not pieces:
        return None
    total = sum(number(n) * (1_000_000_000 if unit in ("tỷ", "ty") else 1_000_000) for n, unit in pieces)
    return round(total) if total > 0 else None


def parse_area(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"(\d[\d.,]*)\s*(?:m²|m2)", value, re.I)
    return number(match.group(1)) if match else None


def parse_rooms(value: str | None) -> int | None:
    match = re.search(r"\d+", value or "")
    return int(match.group()) if match else None


def parse_location(value: str | None) -> tuple[str | None, str | None]:
    value = clean(value)
    if not value:
        return None, None
    match = re.match(r"([^()]+?)(?:\s*\(([^()]+)\))?$", value)
    if not match:
        return value, None
    return clean(match.group(1)), clean(match.group(2))


def text_at(card: Tag, selector: str) -> str | None:
    node = card.select_one(selector)
    return clean(node.get_text(" ", strip=True)) if node else None


def quality_flags(item: Listing) -> str:
    flags: list[str] = []
    if item.price_vnd is None:
        flags.append("missing_or_ambiguous_price")
    if item.area_m2 is None:
        flags.append("missing_area")
    if not item.district:
        flags.append("missing_district")
    if item.bedrooms is None:
        flags.append("missing_bedrooms")
    if item.price_vnd and item.area_m2:
        unit_price = item.price_vnd / item.area_m2 / 1_000_000
        if unit_price < 10 or unit_price > 500:
            flags.append("review_price_per_m2")
    title = (item.title or "").lower()
    if re.search(r"\b(?:từ|chỉ từ)\s*\d[\d.,]*\s*(?:tỷ|triệu)", title):
        flags.append("starting_price")
    title_prices = re.findall(r"\d[\d.,]*\s*(?:tỷ|triệu|tr\b)", title)
    title_areas = re.findall(r"\d[\d.,]*\s*(?:m²|m2)", title)
    if (len(title_prices) > 1 or len(title_areas) > 1 or
            re.search(r"\b\d+\s*căn\b|bảng giá|giỏ hàng|quỹ căn|quỹ hàng", title)):
        flags.append("possible_multi_unit")
    if item.source_url and "ban-can-ho-chung-cu" not in urlparse(item.source_url).path:
        flags.append("review_property_type")
    return ";".join(flags)


def parse_cards(html: str, page_url: str, collected_at_utc: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[Listing] = []
    for card in soup.select(CARD_SELECTOR):
        if not isinstance(card, Tag):
            continue
        listing_id = clean(card.get("data-product-id"))
        href = clean(card.get("href"))
        if not listing_id or not href or not listing_id.isdigit():
            continue
        source_url = urljoin(BASE, href)
        if urlparse(source_url).netloc != "batdongsan.com.vn":
            continue
        price_text = text_at(card, ".re__card-config-price")
        area_text = text_at(card, ".re__card-config-area")
        price_vnd = parse_price(price_text)
        area_m2 = parse_area(area_text)
        location_text = text_at(card, ".re__card-location")
        district, ward = parse_location(location_text)
        bedrooms_text = text_at(card, ".re__card-config-bedroom")
        bathrooms_text = text_at(card, ".re__card-config-toilet")
        item = Listing(
            source_site="batdongsan.com.vn", listing_id=listing_id,
            source_url=source_url, page_url=page_url,
            collected_at_utc=collected_at_utc,
            title=text_at(card, ".re__card-title") or clean(card.get("title")),
            price_text=price_text, area_text=area_text,
            price_per_m2_text=text_at(card, ".re__card-config-price_per_m2"),
            bedrooms_text=bedrooms_text, bathrooms_text=bathrooms_text,
            location_text=location_text,
            description_text=text_at(card, ".re__card-description"),
            posted_date_text=text_at(card, ".re__card-published-info-published-at"),
            price_vnd=price_vnd, area_m2=area_m2,
            price_per_m2_million=round(price_vnd / area_m2 / 1_000_000, 2)
            if price_vnd is not None and area_m2 else None,
            bedrooms=parse_rooms(bedrooms_text), bathrooms=parse_rooms(bathrooms_text),
            district=district, ward=ward,
            project_name=None, floor_text=None, furniture_text=None,
            legal_status_text=None, house_direction_text=None,
            balcony_direction_text=None, detail_price_text=None,
            detail_area_text=None, detail_bedrooms_text=None,
            detail_bathrooms_text=None,
            detail_text=None, quality_flags="",
        )
        item.quality_flags = quality_flags(item)
        rows.append(item)
    return rows


def parse_detail(html: str) -> dict[str, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    specs: dict[str, str] = {}
    for item in soup.select(".re__pr-specs-content-item"):
        label = text_at(item, ".re__pr-specs-content-item-title")
        value = text_at(item, ".re__pr-specs-content-item-value")
        if label and value:
            specs[label] = value
    return {
        "project_name": text_at(soup, ".re__project-title"),
        "floor_text": specs.get("Tầng"),
        "furniture_text": specs.get("Nội thất"),
        "legal_status_text": specs.get("Pháp lý"),
        "house_direction_text": specs.get("Hướng nhà"),
        "balcony_direction_text": specs.get("Hướng ban công"),
        "detail_price_text": specs.get("Khoảng giá"),
        "detail_area_text": specs.get("Diện tích"),
        "detail_bedrooms_text": specs.get("Số phòng ngủ"),
        "detail_bathrooms_text": specs.get("Số phòng tắm, vệ sinh"),
        "detail_text": text_at(soup, ".re__pr-description"),
    }


def attach_detail(item: Listing, html: str) -> None:
    detail = parse_detail(html)
    for key, value in detail.items():
        setattr(item, key, value)
    flags = set(filter(None, item.quality_flags.split(";")))
    detail_price = parse_price(item.detail_price_text)
    detail_area = parse_area(item.detail_area_text)
    if item.price_vnd and detail_price and abs(item.price_vnd - detail_price) / detail_price > 0.01:
        flags.add("card_detail_price_mismatch")
    if item.area_m2 and detail_area and abs(item.area_m2 - detail_area) / detail_area > 0.02:
        flags.add("card_detail_area_mismatch")
    item.quality_flags = ";".join(sorted(flags))


def read_robots() -> urllib.robotparser.RobotFileParser:
    request = urllib.request.Request(ROBOTS_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"robots.txt returned HTTP {response.status}")
        content = response.read().decode("utf-8", errors="replace")
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(content.splitlines())
    return parser


def is_access_block(page, status: int | None) -> bool:
    if status in (401, 403, 429):
        return True
    title = (page.title() or "").lower()
    return any(marker in title for marker in ("just a moment", "chờ một chút", "attention required", "access denied"))


def fetch_page(page, url: str, timeout_ms: int) -> tuple[str, int | None]:
    response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    status = response.status if response else None
    if is_access_block(page, status):
        raise AccessBlocked(f"Access blocked (HTTP {status}, title={page.title()!r}) at {url}")
    if status and status >= 400:
        raise RuntimeError(f"HTTP {status} at {url}")
    return page.content(), status


def write_outputs(run_dir: Path, listings: list[Listing], log: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    records = [asdict(row) for row in listings]
    columns = [field.name for field in fields(Listing)]
    with (run_dir / "housing_raw.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)
    with (run_dir / "housing_raw.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    (run_dir / "collection_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    flag_counts = Counter(flag for item in listings for flag in item.quality_flags.split(";") if flag)
    district_counts = Counter(item.district or "(missing)" for item in listings)
    report = [
        "# Housing collection report", "",
        f"Run: {log['run_id']}", f"Status: {log['status']}",
        f"Unique listing IDs: {len(listings)}", "",
        "## Missing fields", "",
    ]
    for key in ("price_vnd", "area_m2", "district", "bedrooms", "bathrooms", "posted_date_text", "project_name"):
        missing = sum(getattr(item, key) is None for item in listings)
        report.append(f"- {key}: {missing}/{len(listings)}")
    report.extend(["", "## Review flags", ""])
    report.extend(f"- {key}: {value}" for key, value in flag_counts.most_common())
    report.extend(["", "## District coverage", ""])
    report.extend(f"- {key}: {value}" for key, value in district_counts.most_common())
    report.extend(["", "The flags are review prompts, not automatic exclusions.", ""])
    (run_dir / "quality_report.md").write_text("\n".join(report), encoding="utf-8")
    sample = random.Random(42).sample(listings, min(50, len(listings)))
    qa_columns = ("listing_id", "source_url", "title", "price_text", "area_text",
                  "quality_flags", "price_verified", "area_verified",
                  "single_apartment_verified", "property_type_verified", "notes")
    with (run_dir / "manual_qa.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=qa_columns)
        writer.writeheader()
        for item in sample:
            row = {key: getattr(item, key, "") for key in qa_columns}
            writer.writerow(row)


def discover_last_page(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    numbers = []
    for node in soup.select(".re__pagination-number"):
        label = clean(node.get_text(" ", strip=True))
        if label and label.isdigit():
            numbers.append(int(label))
    if not numbers:
        raise RuntimeError("Could not find the last page in the pagination control")
    return max(numbers)


def save_log(run_dir: Path, log: dict) -> None:
    path = run_dir / "collection_log.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def save_page(run_dir: Path, page_number: int, rows: list[Listing]) -> None:
    pages_dir = run_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    path = pages_dir / f"p{page_number}.jsonl"
    temporary = path.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
    temporary.replace(path)


def load_saved_pages(run_dir: Path) -> tuple[dict[str, Listing], set[int]]:
    listings: dict[str, Listing] = {}
    completed: set[int] = set()
    for path in sorted((run_dir / "pages").glob("p*.jsonl"), key=lambda p: int(p.stem[1:])):
        page_number = int(path.stem[1:])
        with path.open(encoding="utf-8") as handle:
            rows = [Listing(**json.loads(line)) for line in handle if line.strip()]
        if not rows:
            continue
        completed.add(page_number)
        for item in rows:
            listings.setdefault(item.listing_id, item)
    return listings, completed


def crawl(args: argparse.Namespace) -> tuple[Path, list[Listing], dict]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    if args.resume:
        run_dir = args.resume.resolve()
        log = json.loads((run_dir / "collection_log.json").read_text(encoding="utf-8"))
        if log.get("schema_version") != "0.3" or log.get("transport") not in (None, "playwright.chromium"):
            raise RuntimeError("Only Playwright runs with a compatible schema can be resumed")
        listings, completed = load_saved_pages(run_dir)
        if log["end_page"] is None and 1 in completed:
            raise RuntimeError("Page 1 was saved before pagination discovery; start a new run")
        if "active_elapsed_seconds" not in log and log.get("finished_at_utc"):
            earlier = datetime.fromisoformat(log["started_at_utc"])
            previous_end = datetime.fromisoformat(log["finished_at_utc"])
            log["active_elapsed_seconds"] = max(0.0, (previous_end - earlier).total_seconds())
            log["active_elapsed_source"] = "estimated_from_prior_timestamps"
        log["status"] = "running"
        log.pop("finished_at_utc", None)
    else:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        run_dir = args.output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        log = {
            "run_id": run_id, "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "schema_version": "0.3", "source": BASE,
            "transport": "playwright.chromium", "active_elapsed_seconds": 0.0,
            "start_page": args.start_page,
            "end_page": None if args.all else args.start_page + args.pages - 1,
            "auto_discover_pages": args.all,
            "delay_seconds": args.delay, "detail_limit": args.detail_limit,
            "robots_url": ROBOTS_URL, "detail_pages_fetched": 0,
            "pages": [], "errors": [], "status": "running",
        }
        listings, completed = {}, set()
        save_log(run_dir, log)

    try:
        robots = read_robots()
        log["robots_checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_log(run_dir, log)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=args.headless)
            pages_this_invocation = 0
            page_number = log["start_page"]
            while log["end_page"] is None or page_number <= log["end_page"]:
                if args.max_pages is not None and pages_this_invocation >= args.max_pages:
                    break
                if page_number in completed:
                    page_number += 1
                    continue
                url = LIST_URL.format(page=page_number)
                if not robots.can_fetch(USER_AGENT, url):
                    raise AccessBlocked(f"robots.txt disallows {url}")
                context = browser.new_context(locale="vi-VN", viewport={"width": 1366, "height": 900})
                page = context.new_page()
                try:
                    for attempt in range(1, 3):
                        try:
                            fetch_started = time.monotonic()
                            html, status = fetch_page(page, url, args.timeout * 1000)
                            fetch_seconds = round(time.monotonic() - fetch_started, 3)
                            break
                        except AccessBlocked:
                            raise
                        except (PlaywrightError, RuntimeError) as exc:
                            if attempt == 2:
                                raise
                            log["errors"].append(f"Transient page error at {url}: {exc}")
                            time.sleep(args.delay)
                    if log["end_page"] is None:
                        log["end_page"] = discover_last_page(html)
                        save_log(run_dir, log)
                        print(f"Discovered {log['end_page']} total pages", flush=True)
                    timestamp = datetime.now(timezone.utc).isoformat()
                    rows = parse_cards(html, url, timestamp)
                    if not rows:
                        raise RuntimeError(f"No listing cards found at {url}; site markup may have changed")
                    save_page(run_dir, page_number, rows)
                    completed.add(page_number)
                    pages_this_invocation += 1
                    for item in rows:
                        listings.setdefault(item.listing_id, item)
                    log["pages"].append({"number": page_number, "url": url,
                                         "http_status": status, "cards": len(rows),
                                         "fetch_seconds": fetch_seconds})
                    log["unique_listings"] = len(listings)
                    save_log(run_dir, log)
                    print(f"Page {page_number}/{log['end_page']}: {len(rows)} cards, {len(listings)} unique IDs", flush=True)
                    if args.detail_limit:
                        for item in rows[:args.detail_limit]:
                            if item.detail_text or not robots.can_fetch(USER_AGENT, item.source_url):
                                continue
                            time.sleep(args.delay)
                            try:
                                detail_html, _ = fetch_page(page, item.source_url, args.timeout * 1000)
                                attach_detail(item, detail_html)
                                attach_detail(listings[item.listing_id], detail_html)
                                save_page(run_dir, page_number, rows)
                                log["detail_pages_fetched"] += 1
                            except AccessBlocked:
                                raise
                            except (PlaywrightError, RuntimeError) as exc:
                                log["errors"].append(f"Detail error at {item.source_url}: {exc}")
                finally:
                    context.close()
                page_number += 1
                if page_number <= log["end_page"] and (args.max_pages is None or pages_this_invocation < args.max_pages):
                    time.sleep(args.delay)
            log["status"] = "complete" if page_number > log["end_page"] else "partial"
            browser.close()
    except KeyboardInterrupt:
        log["status"] = "interrupted"
        print("Collection interrupted; resume with --resume", file=sys.stderr, flush=True)
    except Exception as exc:
        log["status"] = "blocked" if isinstance(exc, AccessBlocked) else "failed"
        log["errors"].append(str(exc))
        print(f"Collection stopped: {exc}", file=sys.stderr, flush=True)
    log["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    log["unique_listings"] = len(listings)
    save_log(run_dir, log)
    return run_dir, list(listings.values()), log


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Hanoi apartment asking-price listings.")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--all", action="store_true", help="Discover and collect every results page")
    parser.add_argument("--max-pages", type=int, help="Stop after this many new pages; resume later")
    parser.add_argument("--resume", type=Path, help="Continue a run from its output directory")
    parser.add_argument("--delay", type=float, default=8.0, help="Minimum seconds between page requests")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--detail-limit", type=int, default=0, help="Detail pages per results page")
    browser_mode = parser.add_mutually_exclusive_group()
    browser_mode.add_argument("--headed", dest="headless", action="store_false")
    browser_mode.add_argument("--headless", dest="headless", action="store_true")
    parser.set_defaults(headless=False)
    parser.add_argument("--output-root", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    if args.start_page < 1 or args.pages < 1 or args.delay < 2 or args.detail_limit < 0 or (args.max_pages is not None and args.max_pages < 1):
        parser.error("start-page/pages must be positive; delay >= 2; detail-limit >= 0")
    if args.all and args.start_page != 1 and not args.resume:
        parser.error("--all starts at page 1 to discover the final page")
    invocation_started_at = datetime.now(timezone.utc).isoformat()
    invocation_started = time.monotonic()
    try:
        run_dir, listings, log = crawl(args)
    except Exception as exc:
        print(f"Could not start collection: {exc}", file=sys.stderr)
        return 1
    write_outputs(run_dir, listings, log)
    last_elapsed = round(time.monotonic() - invocation_started, 3)
    log["last_invocation_elapsed_seconds"] = last_elapsed
    log["active_elapsed_seconds"] = round(log.get("active_elapsed_seconds", 0.0) + last_elapsed, 3)
    log["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    log["wall_elapsed_seconds"] = round(
        (datetime.fromisoformat(log["finished_at_utc"]) -
         datetime.fromisoformat(log["started_at_utc"])).total_seconds(), 3
    )
    log.setdefault("invocations", []).append({
        "started_at_utc": invocation_started_at,
        "finished_at_utc": log["finished_at_utc"],
        "elapsed_seconds": last_elapsed,
    })
    save_log(run_dir, log)
    print(f"Run status: {log['status']}; listings: {len(listings)}; output: {run_dir}")
    total_label = ("estimated total active time" if log.get("active_elapsed_source")
                   else "total active time")
    print(f"Elapsed this invocation: {format_duration(last_elapsed)}; "
          f"{total_label}: {format_duration(log['active_elapsed_seconds'])}")
    return 0 if log["status"] in ("complete", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
