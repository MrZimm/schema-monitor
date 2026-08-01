#!/usr/bin/env python3
"""
schema_check.py

Validates JSON-LD Product structured data on a list of pages.

Two things it does that a basic checker does not:
  1. Checks BOTH the raw HTML and the rendered DOM, so you can tell
     whether schema is server-side or injected by JavaScript.
  2. Diffs against the previous run, so you see NEW failures separately
     from ones you already knew about.

Run:  python3 schema_check.py urls.txt
      python3 schema_check.py urls.txt --render     (needs Playwright)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
import extruct

# Identify yourself. If you crawl someone else's site, they can see who you are
# and how to contact you. This is basic etiquette and keeps you out of trouble.
USER_AGENT = "SchemaMonitor/0.1 (learning project; contact: you@example.com)"

REPORT_PATH = "reports/latest.json"
DELAY_SECONDS = 1.0  # pause between requests so you are not hammering anyone


# ---------------------------------------------------------------------------
# RULES
# ---------------------------------------------------------------------------
# This is the part that matters. The code is plumbing. These rules are the
# domain knowledge. Edit them to match what YOU think correct looks like.

# Fields Google requires for a Product rich result.
REQUIRED_PRODUCT_FIELDS = ["name", "image", "offers"]

# Fields that are not strictly required but that AI agents and shopping
# surfaces use heavily. Missing these is a warning, not a failure.
RECOMMENDED_PRODUCT_FIELDS = ["description", "sku", "brand", "gtin13", "aggregateRating"]

# Inside offers.
REQUIRED_OFFER_FIELDS = ["price", "priceCurrency", "availability"]


def validate_product(node):
    """
    Takes one JSON-LD node that claims to be a Product.
    Returns (errors, warnings) as two lists of strings.
    """
    errors = []
    warnings = []

    for field in REQUIRED_PRODUCT_FIELDS:
        if not node.get(field):
            errors.append(f"missing required field: {field}")

    for field in RECOMMENDED_PRODUCT_FIELDS:
        if not node.get(field):
            warnings.append(f"missing recommended field: {field}")

    # @id should be a stable, absolute URI. This is what lets an AI agent or
    # a knowledge graph link this product to other entities reliably.
    node_id = node.get("@id")
    if not node_id:
        warnings.append("no @id (entity is not addressable)")
    elif not str(node_id).startswith("http"):
        errors.append(f"@id is not an absolute URI: {node_id}")

    # brand should be an object, not a bare string. A string cannot be linked
    # to your Organization node.
    brand = node.get("brand")
    if isinstance(brand, str):
        warnings.append("brand is a plain string, should be an Organization or Brand object")

    # offers can be a single object or a list. Normalise, then check.
    offers = node.get("offers")
    if offers:
        offer_list = offers if isinstance(offers, list) else [offers]
        for i, offer in enumerate(offer_list):
            if not isinstance(offer, dict):
                errors.append(f"offers[{i}] is not an object")
                continue
            for field in REQUIRED_OFFER_FIELDS:
                if not offer.get(field):
                    errors.append(f"offers[{i}] missing: {field}")

    return errors, warnings


# ---------------------------------------------------------------------------
# FETCHING
# ---------------------------------------------------------------------------

def fetch_raw(url):
    """Plain HTTP GET. This is roughly what a basic crawler sees."""
    if url.startswith("file://"):
        with open(url[7:], "r", encoding="utf-8") as f:
            return f.read()
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return resp.text


def fetch_rendered(url):
    """
    Loads the page in a headless browser and returns the rendered DOM.
    This is closer to what Googlebot sees after it executes JavaScript.
    Requires: pip install playwright && playwright install chromium
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        page.goto(url, wait_until="networkidle", timeout=30000)
        html = page.content()
        browser.close()
        return html


def extract_products(html, base_url):
    """Pulls every JSON-LD block out of the HTML and returns the Product nodes."""
    data = extruct.extract(html, base_url=base_url, syntaxes=["json-ld"])
    products = []
    for block in data.get("json-ld", []):
        # Some sites wrap everything in @graph. Flatten it.
        nodes = block.get("@graph", [block]) if isinstance(block, dict) else []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type", "")
            types = node_type if isinstance(node_type, list) else [node_type]
            if "Product" in types:
                products.append(node)
    return products


# ---------------------------------------------------------------------------
# MAIN CHECK
# ---------------------------------------------------------------------------

def check_url(url, render=False):
    result = {"url": url, "status": "ok", "errors": [], "warnings": [], "rendering": None}

    try:
        raw_html = fetch_raw(url)
    except Exception as e:
        result["status"] = "fetch_failed"
        result["errors"].append(f"could not fetch: {e}")
        return result

    raw_products = extract_products(raw_html, url)

    rendered_products = None
    if render and not url.startswith("file://"):
        try:
            rendered_html = fetch_rendered(url)
            rendered_products = extract_products(rendered_html, url)
        except Exception as e:
            result["warnings"].append(f"render check failed: {e}")

    # This is the SC-430 problem in code form.
    if rendered_products is not None:
        if not raw_products and rendered_products:
            result["rendering"] = "client-side only"
            result["warnings"].append(
                "Product schema appears only after JavaScript runs. "
                "Crawlers that do not render will not see it."
            )
        elif raw_products and rendered_products:
            result["rendering"] = "server-side"
        elif raw_products and not rendered_products:
            result["rendering"] = "lost on render"
            result["errors"].append("Product schema present in HTML but gone from rendered DOM")

    products = rendered_products if rendered_products else raw_products

    if not products:
        result["status"] = "fail"
        result["errors"].append("no Product JSON-LD found")
        return result

    for i, product in enumerate(products):
        errs, warns = validate_product(product)
        label = product.get("name") or product.get("sku") or f"product[{i}]"
        result["errors"] += [f"{label}: {e}" for e in errs]
        result["warnings"] += [f"{label}: {w}" for w in warns]

    result["status"] = "fail" if result["errors"] else "ok"
    result["product_count"] = len(products)
    return result


def load_previous():
    if not os.path.exists(REPORT_PATH):
        return {}
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        old = json.load(f)
    return {r["url"]: r for r in old.get("results", [])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("urlfile", help="text file, one URL per line")
    parser.add_argument("--render", action="store_true", help="also check the rendered DOM")
    args = parser.parse_args()

    with open(args.urlfile, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    previous = load_previous()
    results = []

    for url in urls:
        print(f"checking {url} ...", flush=True)
        results.append(check_url(url, render=args.render))
        time.sleep(DELAY_SECONDS)

    # Diff against last run. New failures are what you actually act on.
    new_failures = []
    fixed = []
    for r in results:
        was = previous.get(r["url"], {}).get("status")
        if r["status"] == "fail" and was in (None, "ok"):
            new_failures.append(r["url"])
        if r["status"] == "ok" and was == "fail":
            fixed.append(r["url"])

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "checked": len(results),
        "failing": sum(1 for r in results if r["status"] != "ok"),
        "new_failures": new_failures,
        "fixed_since_last_run": fixed,
        "results": results,
    }

    os.makedirs("reports", exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # Console summary
    print("\n" + "=" * 70)
    print(f"checked {report['checked']}  |  failing {report['failing']}")
    if new_failures:
        print(f"NEW failures: {', '.join(new_failures)}")
    if fixed:
        print(f"fixed since last run: {', '.join(fixed)}")
    print("=" * 70)

    for r in results:
        if r["status"] == "ok" and not r["warnings"]:
            continue
        print(f"\n{r['url']}  [{r['status']}]")
        if r.get("rendering"):
            print(f"  rendering: {r['rendering']}")
        for e in r["errors"]:
            print(f"  ERROR   {e}")
        for w in r["warnings"]:
            print(f"  warn    {w}")

    print(f"\nfull report written to {REPORT_PATH}")
    # Non-zero exit if anything newly broke. Useful later when you schedule it.
    sys.exit(1 if new_failures else 0)


if __name__ == "__main__":
    main()
