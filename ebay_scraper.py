
import os
import re
import time
import uuid
import requests
import pandas as pd

from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

# ==========================================
# CONFIGURATION
# ==========================================

START_URL = (
    "https://www.ebay.com/b/"
    "Samsung-Cell-Phones-and-Smartphones/9355/bn_352130?mag=1"
)

CRAWIO_API_URL = "https://api.crawio.com/scrape"
CRAWIO_API_KEY = "CRAWIO_API_KEY"

MAX_PAGES = 3
OUTPUT_FILE = "ebay_samsung_phones.xlsx"

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_text(element):
    """Get clean text from an HTML element."""
    if element:
        return element.get_text(" ", strip=True)
    return None


def parse_rating(rating_tag):
    """
    Extract rating safely.

    Examples:
        aria-label="4.5 out of 5 stars" -> 4.5
        data-stars="4"               -> 4.0
        data-stars="4-5"             -> None if no exact label
    """

    if not rating_tag:
        return None

    label = rating_tag.get("aria-label", "")

    match = re.search(
        r"(\d+(?:\.\d+)?)\s+out of\s+5",
        label,
        flags=re.IGNORECASE
    )

    if match:
        return float(match.group(1))

    value = rating_tag.get("data-stars", "")

    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return float(value)

    return None


def parse_reviews(reviews_tag):
    """Convert review count text into an integer."""

    text = get_text(reviews_tag)

    if not text:
        return None

    match = re.search(r"([\d,]+)", text)

    if match:
        return int(match.group(1).replace(",", ""))

    return None


def get_product_id(url):
    """
    Extract eBay listing ID when available.

    Examples:
        /itm/123456789012
        /p/11071453607?iid=158334553896
    """

    parsed = urlparse(url)

    item_match = re.search(r"/itm/(?:[^/]+/)?(\d+)", parsed.path)

    if item_match:
        return item_match.group(1)

    query = parse_qs(parsed.query)
    item_ids = query.get("iid", [])

    if item_ids:
        return item_ids[0]

    return None


# ==========================================
# CRAWIO API
# ==========================================

def fetch_html(url):
    """Retrieve HTML through Crawio API."""

    if not CRAWIO_API_KEY:
        raise RuntimeError(
            "CRAWIO_API_KEY is missing. "
            "Please set your API key."
        )

    headers = {
        "x-api-key": CRAWIO_API_KEY,
        "content-type": "application/json",
        "Idempotency-Key": str(uuid.uuid4()),
    }

    payload = {
        "url": url,
        "method": "GET",
        "timeout": 180,
    }

    response = requests.post(
        CRAWIO_API_URL,
        headers=headers,
        json=payload,
        timeout=210,
    )

    response.raise_for_status()

    data = response.json()

    target_status = data.get("status")

    print(
        f"Target status: {target_status} | "
        f"Cost: {data.get('cost')} | "
        f"Request: {data.get('request_id')}"
    )

    if str(target_status) != "200":
        raise RuntimeError(
            f"Target website returned status {target_status}"
        )

    html = data.get("body", "")

    if not isinstance(html, str) or not html.strip():
        raise RuntimeError("Empty or invalid HTML response")

    return html


# ==========================================
# PRODUCT EXTRACTION
# ==========================================

def parse_products(html, current_url):
    """Extract eBay Samsung phone listings."""

    soup = BeautifulSoup(html, "html.parser")

    products = []

    cards = soup.select(".brwrvr__item-card__body")

    print(f"Product cards detected: {len(cards)}")

    for card in cards:
        try:
            title_tag = card.select_one(
                ".bsig__title__text"
            )

            link_tag = card.select_one(
                ".bsig__title__wrapper[href]"
            )

            condition_tag = card.select_one(
                ".bsig__listingCondition.secondary"
            )

            rating_tag = card.select_one(
                ".star-rating"
            )

            reviews_tag = card.select_one(
                ".bsig__product-review__count"
            )

            price_tag = card.select_one(
                ".bsig__price--displayprice"
            )

            sold_tag = card.select_one(
                ".bsig__item-hotness"
            )

            image_tag = card.select_one(
                ".brwrvr__item-card__image"
            )

            title = get_text(title_tag)

            if not title or not link_tag:
                continue

            product_url = urljoin(
                current_url,
                link_tag.get("href", "")
            )

            if not product_url.startswith(
                ("https://", "http://")
            ):
                continue

            rating = parse_rating(rating_tag)
            reviews = parse_reviews(reviews_tag)

            image_url = None

            if image_tag:
                image_url = (
                    image_tag.get("data-src")
                    or image_tag.get("src")
                )

                if image_url:
                    image_url = urljoin(
                        current_url,
                        image_url
                    )

            product = {
                "product_id": get_product_id(product_url),
                "title": title,
                "condition": get_text(condition_tag),
                "rating": rating,
                "reviews_count": reviews,
                "price": get_text(price_tag),
                "sold": get_text(sold_tag),
                "image_url": image_url,
                "product_url": product_url,
            }

            products.append(product)

        except Exception as error:
            print(f"Skipping a product: {error}")
            continue

    # ======================================
    # PAGINATION
    # ======================================

    next_url = None

    next_tag = soup.select_one(
        "a.pagination__next[href]"
    )

    if next_tag:
        if next_tag.get("aria-disabled") != "true":
            next_url = urljoin(
                current_url,
                next_tag.get("href")
            )

    return products, next_url


# ==========================================
# MAIN SCRAPER
# ==========================================

def main():

    if not CRAWIO_API_KEY:
        print("ERROR: CRAWIO_API_KEY not found.")
        return

    all_products = []
    visited_urls = set()

    current_url = START_URL

    for page in range(1, MAX_PAGES + 1):

        if not current_url:
            print("No more pages available.")
            break

        if current_url in visited_urls:
            print("Page already visited. Stopping.")
            break

        visited_urls.add(current_url)

        print("\n" + "=" * 55)
        print(f"SCRAPING PAGE {page}")
        print(f"URL: {current_url}")
        print("=" * 55)

        try:
            html = fetch_html(current_url)

            products, next_url = parse_products(
                html,
                current_url
            )

        except (
            requests.RequestException,
            ValueError,
            RuntimeError
        ) as error:

            print(f"Page failed: {error}")
            break

        print(f"Products extracted: {len(products)}")

        if not products:
            print(
                "No products found. "
                "Stopping to avoid invalid pagination."
            )
            break

        all_products.extend(products)

        if not next_url:
            print("Last page reached.")
            break

        current_url = next_url

        if page < MAX_PAGES:
            time.sleep(2)

    # ======================================
    # EXCEL EXPORT
    # ======================================

    if not all_products:
        print("\nNo products extracted.")
        print("Excel file not created.")
        return

    df = pd.DataFrame(all_products)

    # Use listing ID where available, otherwise URL.
    df["_dedup_key"] = df.apply(
        lambda row: (
            "id:" + str(row["product_id"])
            if pd.notna(row["product_id"])
            and str(row["product_id"]).strip()
            else "url:" + str(row["product_url"])
        ),
        axis=1
    )

    before = len(df)

    df.drop_duplicates(
        subset=["_dedup_key"],
        keep="first",
        inplace=True
    )

    df.drop(
        columns=["_dedup_key"],
        inplace=True
    )

    duplicates = before - len(df)

    try:
        df.to_excel(
            OUTPUT_FILE,
            index=False,
            engine="openpyxl"
        )

    except PermissionError:
        fallback = (
            f"ebay_samsung_phones_{int(time.time())}.xlsx"
        )

        df.to_excel(
            fallback,
            index=False,
            engine="openpyxl"
        )

        print(
            f"Original Excel file is open. "
            f"Saved as {fallback}"
        )

        return

    print("\n" + "=" * 55)
    print("SCRAPING COMPLETED")
    print("=" * 55)

    print(f"Total records collected: {before}")
    print(f"Duplicates removed: {duplicates}")
    print(f"Unique products saved: {len(df)}")
    print(f"Excel file: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
