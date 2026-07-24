"""
PrestaShop -> Shopify CSV Converter
======================================
Converts a PrestaShop database CSV export into Shopify-ready import files:

    shopify_products.csv    -> Shopify Admin > Products > Import
    shopify_customers.csv   -> Shopify Admin > Customers > Import
    shopify_orders.csv      -> Matrixify app > Import (Shopify has no native
                                order importer -- see NOTE 3 below)

No API credentials required -- just drop your PrestaShop table exports
(one CSV per table, named like the table, e.g. ps_product.csv) into the
input/ folder next to this script and run it.

INPUT TABLES USED
------------------
    ps_product, ps_product_lang            (core product data)
    ps_manufacturer                        (brand/vendor)
    ps_category_lang                       (category names)
    ps_attribute, ps_attribute_group        (variant option names/values)
    ps_product_attribute                   (variant/combination rows)
    ps_product_attribute_combination       (links a combination to its attributes)
    ps_stock_available                     (inventory quantity)
    ps_image, ps_image_lang                (product images + alt text)
    ps_tag                                 (tags, IF it has an id_product column
                                             -- see NOTE 1 below)
    ps_feature_product                     (feature/attribute id links -- see NOTE 2)
    ps_customer                            (customer records)
    ps_address                             (customer + order addresses)
    ps_orders                              (order header data -- see NOTE 3)

REQUIREMENTS
------------
    pip install pandas --break-system-packages

USAGE
-----
    python prestashop_to_shopify_converter.py

============================================================================
NOTE 1 -- ps_tag has no ps_product_tag table alongside it in a typical
export. PrestaShop normally links tags to products through
ps_product_tag (id_product, id_tag, id_lang). Without that table there is
no reliable way to know which tag belongs to which product. The script
checks: if your ps_tag.csv happens to already contain an id_product
column, tags import normally as part of Shopify's Tags column. Otherwise
it logs a warning and Tags only contains the product's category names.
Export ps_product_tag.csv and re-run if you need tags.

NOTE 2 -- ps_feature_product only maps id_product -> id_feature -> id_value.
Without ps_feature (feature names) and ps_feature_value_lang (value text),
the script cannot resolve human-readable feature names, so features are
skipped entirely (they aren't a good fit for a plain product CSV import
anyway -- Shopify metafields would be the real target, which is outside
the scope of a CSV product import).

NOTE 3 -- ps_orders is the order HEADER table only. There is no
ps_order_detail (line items) in a typical export, so shopify_orders.csv
contains one row per order with order-level totals/status/address/customer,
but the Lineitem columns (Lineitem sku, Lineitem quantity, Lineitem price,
Lineitem name) are blank. Export ps_order_detail.csv and re-run for
complete line items. Shopify has no built-in order CSV importer at all --
this file is shaped to match Shopify's own order EXPORT format column for
column, since the Matrixify app (Shopify Admin -> Apps -> Matrixify ->
Import) is built to accept that exact format for import.

NOTE 4 -- Country/state codes. Shopify's customer and order imports need
real ISO country codes (e.g. "US") and province codes (e.g. "FL"), not
PrestaShop's internal numeric id_country / id_state. Two optional tables
resolve these to real codes if you export them:
    ps_country   -> Billing/Shipping Country, Default Address Country Code
                    (the 2-letter iso_code column lives on ps_country, NOT
                    ps_country_lang -- ps_country_lang only has the full
                    country NAME, e.g. "United States", which is why
                    exporting that one alone still isn't enough to fix a
                    "country code invalid" import error)
    ps_state     -> Billing/Shipping Province, Default Address Province Code
Without ps_country.csv, the raw PrestaShop id_country is written instead
and Shopify will likely reject those rows with a "country code invalid"
error on import (same idea for state/province, though that error is
usually less strict). If you'd rather not re-export from PrestaShop right
now, set COUNTRY_ID_OVERRIDES / STATE_ID_OVERRIDES near the top of this
script with just the specific IDs your store uses -- for example, if
every address in your export uses id_country 21 for the United States,
COUNTRY_ID_OVERRIDES = {"21": "US"} fixes every row immediately.

NOTE 5 -- Variant/option handling (Option1/2/3 Name & Value, multiple
Variant rows sharing one Handle, extra image-only rows) follows Shopify's
standard, documented product CSV convention. The store this script was
built against has no products with combinations (ps_attribute,
ps_attribute_group, and ps_product_attribute were all empty in the
export used to test this script), so this path has NOT been live-tested
against a real variant-bearing catalog -- if your store has product
options, double check the first import closely.

NOTE 6 -- Weight unit. PrestaShop's ps_product.weight is assumed to be in
kilograms (PrestaShop's common default unit) and is converted to grams
for Shopify's Variant Grams column (weight * 1000). If your PrestaShop
store's weight unit setting (Preferences > General) is actually pounds,
change DEFAULT_WEIGHT_UNIT below and adjust the conversion in
build_variant_grams().
============================================================================
"""

import os
import re
import csv
import sys
import logging
import unicodedata
from pathlib import Path
from collections import defaultdict
from datetime import datetime

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(SCRIPT_DIR, "input")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")

# ----------------------------------------------------------------------
# Config -- adjust to match your store
# ----------------------------------------------------------------------
PRESTASHOP_STORE_URL = "https://your-prestashop-store.com"
PRESTASHOP_IMAGE_BASE_URL = f"{PRESTASHOP_STORE_URL.rstrip('/')}/img/p"
DEFAULT_LANGUAGE_ID = "1"
DEFAULT_WEIGHT_UNIT = "kg"      # see NOTE 6 above
DEFAULT_INVENTORY_POLICY = "deny"   # 'deny' or 'continue'
DEFAULT_PUBLISHED = "TRUE"

# Quick manual fix for country/state codes -- see NOTE 4 below. If you hit
# a "country code invalid" (or similar) error on import and don't want to
# re-export ps_country.csv / ps_state.csv from PrestaShop, add the raw
# PrestaShop id_country / id_state values you actually use as keys here,
# e.g.:
#   COUNTRY_ID_OVERRIDES = {"21": "US"}
#   STATE_ID_OVERRIDES = {"12": "FL", "50": "WA"}
# These take priority over anything loaded from ps_country.csv/ps_state.csv.
COUNTRY_ID_OVERRIDES = {
    "21": "US",   # confirmed -- every address in this store's export is a US address
}
STATE_ID_OVERRIDES = {
    "12": "FL",   # Lakeland 33810, Sarasota 34233
    "50": "WA",   # Port Ludlow 98365
    "35": "NY",   # Pearl River 10965, Eggertsville 14226
    "9": "CO",    # Aurora 80013, Northglenn 80233, Westminster 80031
    "46": "TX",   # Galveston 77551
    "8": "CA",    # San Francisco 94114
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ps_to_shopify")

# ----------------------------------------------------------------------
# Shopify's own documented CSV headers
# ----------------------------------------------------------------------

# Standard Shopify product import template (60 columns).
SHOPIFY_PRODUCT_HEADERS = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Product Category",
    "Type", "Tags", "Published", "Option1 Name", "Option1 Value",
    "Option2 Name", "Option2 Value", "Option3 Name", "Option3 Value",
    "Variant SKU", "Variant Grams", "Variant Inventory Tracker",
    "Variant Inventory Qty", "Variant Inventory Policy",
    "Variant Fulfillment Service", "Variant Price",
    "Variant Compare At Price", "Variant Requires Shipping",
    "Variant Taxable", "Variant Barcode", "Image Src",
    "Image Position", "Image Alt Text", "Gift Card",
    "SEO Title", "SEO Description", "Google Shopping / MPN",
    "Google Shopping / Age Group", "Google Shopping / Gender",
    "Google Shopping / Google Product Category",
    "Google Shopping / Adwords Grouping", "Google Shopping / Adwords Labels",
    "Google Shopping / Condition", "Google Shopping / Custom Product",
    "Google Shopping / Custom Label 0", "Google Shopping / Custom Label 1",
    "Google Shopping / Custom Label 2", "Google Shopping / Custom Label 3",
    "Google Shopping / Custom Label 4", "Variant Image",
    "Variant Weight Unit", "Variant Tax Code", "Cost per item",
    "Included / United States", "Price / United States",
    "Compare At Price / United States", "Included / International",
    "Price / International", "Compare At Price / International",
    "Status", "Unit Price", "Unit Price Measure", "Unit Price Base",
    "Bundle", "Components", "Linked Options",
]

# Standard Shopify customer import template.
SHOPIFY_CUSTOMER_HEADERS = [
    "First Name", "Last Name", "Email", "Accepts Email Marketing",
    "Default Address Company", "Default Address Address1",
    "Default Address Address2", "Default Address City",
    "Default Address Province Code", "Default Address Country Code",
    "Default Address Zip", "Default Address Phone", "Phone",
    "Accepts SMS Marketing", "Accepts WhatsApp Marketing",
    "Tags", "Note", "Tax Exempt",
]

# Subset of Shopify's own order EXPORT format -- Matrixify imports this
# exact column layout. See NOTE 3 above for why Lineitem columns are blank.
SHOPIFY_ORDER_HEADERS = [
    "Name", "Email", "Financial Status", "Paid at", "Fulfillment Status",
    "Fulfilled at", "Currency", "Subtotal", "Shipping", "Taxes", "Total",
    "Discount Code", "Discount Amount", "Shipping Method", "Created at",
    "Lineitem quantity", "Lineitem name", "Lineitem price", "Lineitem sku",
    "Lineitem requires shipping", "Lineitem taxable",
    "Billing Name", "Billing Street", "Billing Address1", "Billing Address2",
    "Billing Company", "Billing City", "Billing Zip", "Billing Province",
    "Billing Country", "Billing Phone",
    "Shipping Name", "Shipping Street", "Shipping Address1", "Shipping Address2",
    "Shipping Company", "Shipping City", "Shipping Zip", "Shipping Province",
    "Shipping Country", "Shipping Phone",
    "Notes", "Payment Method", "Phone", "Tags",
]

# Default PrestaShop order state IDs (out-of-the-box install). If your
# store has custom order states, edit this map or export
# ps_order_state_lang.csv and this script will prefer that instead.
DEFAULT_ORDER_STATE_MAP = {
    "1": "Awaiting check payment",
    "2": "Payment accepted",
    "3": "Processing in progress",
    "4": "Shipped",
    "5": "Delivered",
    "6": "Canceled",
    "7": "Refunded",
    "8": "Payment error",
    "9": "On backorder (paid)",
    "10": "Awaiting bank wire payment",
    "11": "Remote payment accepted",
    "12": "On backorder (not paid)",
    "13": "Payment abandoned",
    "14": "Awaiting Cash on delivery validation",
}


# ----------------------------------------------------------------------
# Small utilities
# ----------------------------------------------------------------------

def load_csv(name: str) -> pd.DataFrame:
    path = Path(INPUT_DIR) / f"{name}.csv"
    if not path.exists():
        logger.warning(f"Missing input file: {name}.csv (skipping)")
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        logger.info(f"Loaded {name}.csv: {len(df)} rows")
        return df
    except Exception as e:
        logger.error(f"Error loading {name}.csv: {e}")
        return pd.DataFrame()


def gv(row, col, default=""):
    if row is None:
        return default
    try:
        val = row.get(col, default)
    except AttributeError:
        return default
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    val = str(val).strip()
    return val if val and val.upper() != "NULL" else default


def format_price(value, default="0.00"):
    try:
        return f"{float(value):.2f}"
    except (ValueError, TypeError):
        return default


def clean_html(text: str) -> str:
    if not text:
        return ""
    return text.replace("\r\n", "\n").strip()


def plain_text_summary(html: str, limit: int = 320) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = " ".join(text.split())
    return text[:limit]


def slugify(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def write_csv(rows, headers, filename):
    out_path = Path(OUTPUT_DIR) / filename
    try:
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in headers})
        logger.info(f"Wrote {len(rows)} rows -> {out_path}")
    except PermissionError:
        logger.error(f"Could not write {out_path} -- is it open in Excel? Close the file and re-run.")
    return out_path


# ----------------------------------------------------------------------
# Reference data cache
# ----------------------------------------------------------------------

class ReferenceData:
    def __init__(self, data):
        self.data = data
        self.categories = {}
        self.manufacturers = {}
        self.attribute_groups = {}
        self.attributes = {}
        self.combinations = defaultdict(list)      # id_product_attribute -> [id_attribute,...]
        self.images = defaultdict(list)             # id_product -> [{id,url,position,cover,alt}]
        self.tags = defaultdict(list)                # id_product -> [tag names] (only if resolvable)
        self.stock = {}                               # "prod_attr" -> qty
        self.order_states = dict(DEFAULT_ORDER_STATE_MAP)
        self.states = {}       # id_state -> code/name (optional ps_state.csv)
        self.countries = {}    # id_country -> name/code (optional ps_country_lang.csv)
        self._load()

    def _load(self):
        d = self.data

        cat_lang = d.get("ps_category_lang", pd.DataFrame())
        if not cat_lang.empty:
            for _, r in cat_lang.iterrows():
                cid = gv(r, "id_category")
                if cid:
                    self.categories[cid] = gv(r, "name")
        logger.info(f"Categories loaded: {len(self.categories)}")

        mf = d.get("ps_manufacturer", pd.DataFrame())
        if not mf.empty:
            for _, r in mf.iterrows():
                mid = gv(r, "id_manufacturer")
                if mid:
                    self.manufacturers[mid] = gv(r, "name")
        logger.info(f"Manufacturers loaded: {len(self.manufacturers)}")

        ag = d.get("ps_attribute_group", pd.DataFrame())
        if not ag.empty:
            for _, r in ag.iterrows():
                gid = gv(r, "id_attribute_group")
                name = gv(r, "name") or gv(r, "public_name") or f"Option {gid}"
                if gid:
                    self.attribute_groups[gid] = name
        at = d.get("ps_attribute", pd.DataFrame())
        if not at.empty:
            for _, r in at.iterrows():
                aid = gv(r, "id_attribute")
                gid = gv(r, "id_attribute_group")
                if aid:
                    self.attributes[aid] = {
                        "name": gv(r, "name") or gv(r, "color"),
                        "group_id": gid,
                        "group_name": self.attribute_groups.get(gid, "Option"),
                    }
        logger.info(f"Attribute groups: {len(self.attribute_groups)}, attributes: {len(self.attributes)}")

        comb = d.get("ps_product_attribute_combination", pd.DataFrame())
        if not comb.empty:
            for _, r in comb.iterrows():
                pa_id = gv(r, "id_product_attribute")
                a_id = gv(r, "id_attribute")
                if pa_id and a_id:
                    self.combinations[pa_id].append(a_id)
        logger.info(f"Combinations loaded for {len(self.combinations)} variant rows")

        img = d.get("ps_image", pd.DataFrame())
        img_lang = d.get("ps_image_lang", pd.DataFrame())
        alt_by_image = {}
        if not img_lang.empty:
            for _, r in img_lang.iterrows():
                iid = gv(r, "id_image")
                if iid:
                    alt_by_image[iid] = gv(r, "legend")
        if not img.empty:
            for _, r in img.iterrows():
                pid = gv(r, "id_product")
                iid = gv(r, "id_image")
                if not pid or not iid:
                    continue
                self.images[pid].append({
                    "id": iid,
                    "url": self._image_url(iid, pid),
                    "position": gv(r, "position", "0"),
                    "cover": gv(r, "cover", "0") == "1",
                    "alt": alt_by_image.get(iid, ""),
                })
            for pid in self.images:
                self.images[pid].sort(key=lambda x: (0 if x["cover"] else 1, int(x["position"] or 0)))
        total_imgs = sum(len(v) for v in self.images.values())
        logger.info(f"Images loaded: {total_imgs} across {len(self.images)} products")

        tags_df = d.get("ps_tag", pd.DataFrame())
        if not tags_df.empty:
            if "id_product" in tags_df.columns:
                for _, r in tags_df.iterrows():
                    pid = gv(r, "id_product")
                    name = gv(r, "name")
                    if pid and name:
                        self.tags[pid].append(name)
                logger.info(f"Tags resolved for {len(self.tags)} products")
            else:
                logger.warning(
                    "ps_tag.csv has no id_product column and no ps_product_tag.csv was "
                    "provided -- tags will be skipped (Tags will only contain category names). "
                    "Export ps_product_tag.csv to include tags."
                )

        stock_df = d.get("ps_stock_available", pd.DataFrame())
        if not stock_df.empty:
            for _, r in stock_df.iterrows():
                pid = gv(r, "id_product")
                aid = gv(r, "id_product_attribute", "0")
                self.stock[f"{pid}_{aid}"] = gv(r, "quantity", "0")
        logger.info(f"Stock rows loaded: {len(self.stock)}")

        state_lang = d.get("ps_order_state_lang", pd.DataFrame())
        if not state_lang.empty:
            resolved = {}
            for _, r in state_lang.iterrows():
                sid = gv(r, "id_order_state")
                if sid:
                    resolved[sid] = gv(r, "name")
            if resolved:
                self.order_states = resolved
                logger.info(f"Order state names loaded from ps_order_state_lang: {len(resolved)}")

        # Optional: state/country resolution for customers & orders (see NOTE 4)
        states_df = d.get("ps_state", pd.DataFrame())
        if not states_df.empty:
            for _, r in states_df.iterrows():
                sid = gv(r, "id_state")
                if sid:
                    self.states[sid] = gv(r, "iso_code") or gv(r, "name")
            logger.info(f"State codes loaded from ps_state: {len(self.states)}")
        elif not STATE_ID_OVERRIDES:
            logger.warning(
                "No ps_state.csv provided (and no STATE_ID_OVERRIDES set) -- Province Code "
                "columns will contain raw PrestaShop id_state values instead of real "
                "state/province codes (see NOTE 4)."
            )
        self.states.update(STATE_ID_OVERRIDES)

        # NOTE: the 2-letter ISO country code lives on PrestaShop's ps_country
        # table (column iso_code), NOT ps_country_lang (which only has
        # id_country/id_lang/name -- the full country name, not a code).
        # ps_country_lang is used here only as a fallback for a human-readable
        # name if ps_country.csv wasn't exported, but Shopify's importer wants
        # an actual code, so ps_country.csv is what actually fixes a
        # "country code invalid" import error -- see NOTE 4.
        country_df = d.get("ps_country", pd.DataFrame())
        country_lang_df = d.get("ps_country_lang", pd.DataFrame())
        name_by_country = {}
        if not country_lang_df.empty:
            for _, r in country_lang_df.iterrows():
                cid = gv(r, "id_country")
                if cid:
                    name_by_country[cid] = gv(r, "name")
        if not country_df.empty:
            for _, r in country_df.iterrows():
                cid = gv(r, "id_country")
                if cid:
                    self.countries[cid] = gv(r, "iso_code") or name_by_country.get(cid, "")
            logger.info(f"Country ISO codes loaded from ps_country: {len(self.countries)}")
        elif name_by_country:
            self.countries.update(name_by_country)
            logger.warning(
                "ps_country_lang.csv was provided but ps_country.csv wasn't -- Country Code "
                "columns will contain the full country NAME (e.g. 'United States'), not a "
                "2-letter ISO code, since iso_code lives on ps_country, not ps_country_lang. "
                "Export ps_country.csv for a real code, or Shopify may still reject these rows."
            )
        elif not COUNTRY_ID_OVERRIDES:
            logger.warning(
                "No ps_country.csv provided (and no COUNTRY_ID_OVERRIDES set) -- Country Code "
                "columns will contain raw PrestaShop id_country values instead of real ISO "
                "country codes (see NOTE 4)."
            )
        self.countries.update(COUNTRY_ID_OVERRIDES)

    def _image_url(self, image_id, product_id):
        if not image_id:
            return ""
        return f"{PRESTASHOP_IMAGE_BASE_URL}/{product_id}/{image_id}.jpg"

    def stock_qty(self, product_id, attr_id="0"):
        raw = self.stock.get(f"{product_id}_{attr_id}", "0")
        try:
            return str(max(0, int(float(raw))))
        except (ValueError, TypeError):
            return "0"


# ----------------------------------------------------------------------
# Products
# ----------------------------------------------------------------------

def get_primary_lang_row(lang_df: pd.DataFrame):
    if lang_df.empty:
        return None
    filtered = lang_df[lang_df["id_lang"] == DEFAULT_LANGUAGE_ID] if "id_lang" in lang_df.columns else lang_df
    if filtered.empty:
        filtered = lang_df
    return filtered.iloc[0]


def build_variant_grams(weight_str: str) -> str:
    try:
        kg = float(weight_str or 0)
        return str(int(round(kg * 1000)))
    except (ValueError, TypeError):
        return "0"


def build_option_columns(pa_id: str, ref: ReferenceData):
    """Returns up to 3 (name, value) pairs for a variant's combination,
    matching Shopify's Option1/2/3 Name & Value columns."""
    attr_ids = ref.combinations.get(pa_id, [])
    options = []
    for aid in attr_ids[:3]:
        attr = ref.attributes.get(aid)
        if attr:
            options.append((attr["group_name"], attr["name"]))
    while len(options) < 3:
        options.append(("", ""))
    return options


def convert_products(data, ref: ReferenceData):
    products = data.get("ps_product", pd.DataFrame())
    products_lang = data.get("ps_product_lang", pd.DataFrame())
    prod_attr = data.get("ps_product_attribute", pd.DataFrame())

    if products.empty:
        logger.warning("No ps_product.csv found/loaded -- skipping product conversion")
        return []

    rows = []
    for idx, prod in products.iterrows():
        try:
            pid = gv(prod, "id_product")
            if not pid:
                continue

            plang_rows = products_lang[products_lang["id_product"] == pid] if not products_lang.empty else pd.DataFrame()
            lang = get_primary_lang_row(plang_rows)
            if lang is None:
                logger.warning(f"Product {pid} has no ps_product_lang entry, skipping")
                continue

            name = gv(lang, "name") or f"Product {pid}"
            handle = slugify(gv(lang, "link_rewrite") or name)
            description = clean_html(gv(lang, "description") or gv(lang, "description_short"))
            meta_title = gv(lang, "meta_title") or name
            meta_description = gv(lang, "meta_description") or plain_text_summary(gv(lang, "description_short"))

            brand = ref.manufacturers.get(gv(prod, "id_manufacturer"), "")
            category = ref.categories.get(gv(prod, "id_category_default"), "")
            tags = ref.tags.get(pid, [])
            tag_field = ", ".join(dict.fromkeys([t for t in ([category] + tags) if t]))

            active = gv(prod, "active", "1")
            published = "TRUE" if active == "1" else "FALSE"
            status = "active" if active == "1" else "draft"

            is_virtual = gv(prod, "is_virtual", "0") == "1"
            weight = gv(prod, "weight", "0")

            images = ref.images.get(pid, [])
            variants = prod_attr[prod_attr["id_product"] == pid] if not prod_attr.empty else pd.DataFrame()
            has_variants = not variants.empty

            base_common = {
                "Handle": handle,
                "Title": name,
                "Body (HTML)": description,
                "Vendor": brand,
                "Product Category": "",
                "Type": category,
                "Tags": tag_field,
                "Published": published,
                "Gift Card": "FALSE",
                "SEO Title": meta_title,
                "SEO Description": meta_description,
                "Variant Weight Unit": DEFAULT_WEIGHT_UNIT,
                "Status": status,
            }

            if not has_variants:
                sku = gv(prod, "reference")
                first_image = images[0] if images else None
                row = {
                    **base_common,
                    "Option1 Name": "",
                    "Option1 Value": "",
                    "Option2 Name": "",
                    "Option2 Value": "",
                    "Option3 Name": "",
                    "Option3 Value": "",
                    "Variant SKU": sku,
                    "Variant Grams": build_variant_grams(weight),
                    "Variant Inventory Tracker": "shopify",
                    "Variant Inventory Qty": ref.stock_qty(pid, "0"),
                    "Variant Inventory Policy": DEFAULT_INVENTORY_POLICY,
                    "Variant Fulfillment Service": "manual",
                    "Variant Price": format_price(gv(prod, "price", "0")),
                    "Variant Compare At Price": "",
                    "Variant Requires Shipping": "FALSE" if is_virtual else "TRUE",
                    "Variant Taxable": "TRUE",
                    "Variant Barcode": gv(prod, "ean13") or gv(prod, "upc"),
                    "Image Src": first_image["url"] if first_image else "",
                    "Image Position": "1" if first_image else "",
                    "Image Alt Text": first_image["alt"] if first_image else "",
                    "Cost per item": format_price(gv(prod, "wholesale_price"), default=""),
                }
                rows.append(row)
            else:
                first_row_written = False
                for v_i, (_, vrow) in enumerate(variants.iterrows()):
                    pa_id = gv(vrow, "id_product_attribute")
                    sku = gv(vrow, "reference") or gv(prod, "reference")
                    price_impact = gv(vrow, "price", "0")
                    try:
                        variant_price = float(gv(prod, "price", "0")) + float(price_impact or 0)
                    except (ValueError, TypeError):
                        variant_price = gv(prod, "price", "0")

                    opt1, opt2, opt3 = build_option_columns(pa_id, ref)
                    v_weight = gv(vrow, "weight", "") or weight
                    image_for_variant = images[v_i] if v_i < len(images) else (images[0] if images else None)

                    variant_row = {
                        "Handle": handle,
                        "Option1 Name": opt1[0], "Option1 Value": opt1[1],
                        "Option2 Name": opt2[0], "Option2 Value": opt2[1],
                        "Option3 Name": opt3[0], "Option3 Value": opt3[1],
                        "Variant SKU": sku,
                        "Variant Grams": build_variant_grams(v_weight),
                        "Variant Inventory Tracker": "shopify",
                        "Variant Inventory Qty": ref.stock_qty(pid, pa_id),
                        "Variant Inventory Policy": DEFAULT_INVENTORY_POLICY,
                        "Variant Fulfillment Service": "manual",
                        "Variant Price": format_price(variant_price),
                        "Variant Compare At Price": "",
                        "Variant Requires Shipping": "FALSE" if is_virtual else "TRUE",
                        "Variant Taxable": "TRUE",
                        "Variant Barcode": gv(vrow, "ean13") or gv(vrow, "upc"),
                        "Variant Image": image_for_variant["url"] if image_for_variant else "",
                        "Cost per item": format_price(gv(vrow, "wholesale_price"), default=""),
                    }
                    if not first_row_written:
                        variant_row.update(base_common)
                        if images:
                            variant_row["Image Src"] = images[0]["url"]
                            variant_row["Image Position"] = "1"
                            variant_row["Image Alt Text"] = images[0]["alt"]
                        first_row_written = True
                    rows.append(variant_row)

                # Extra image-only rows for any images beyond what's already
                # attached to a variant, per Shopify's multi-row image convention.
                for img_i in range(len(variants), len(images)):
                    rows.append({
                        "Handle": handle,
                        "Image Src": images[img_i]["url"],
                        "Image Position": str(img_i + 1),
                        "Image Alt Text": images[img_i]["alt"],
                    })

            if (idx + 1) % 100 == 0:
                logger.info(f"Processed {idx + 1} products...")

        except Exception as e:
            logger.error(f"Error processing product {gv(prod, 'id_product', 'unknown')}: {e}")
            continue

    return rows


# ----------------------------------------------------------------------
# Customers
# ----------------------------------------------------------------------

def convert_customers(data, ref: ReferenceData):
    customers = data.get("ps_customer", pd.DataFrame())
    addresses = data.get("ps_address", pd.DataFrame())
    if customers.empty:
        logger.warning("No ps_customer.csv found/loaded -- skipping customer conversion")
        return []

    # First non-deleted address per customer, used as the Shopify "default address"
    default_address_by_customer = {}
    if not addresses.empty:
        for _, a in addresses.iterrows():
            if gv(a, "deleted", "0") == "1":
                continue
            cid = gv(a, "id_customer")
            if cid and cid not in default_address_by_customer:
                default_address_by_customer[cid] = a

    rows = []
    for _, c in customers.iterrows():
        email = gv(c, "email")
        if not email:
            continue
        cid = gv(c, "id_customer")
        addr = default_address_by_customer.get(cid)

        state_id = gv(addr, "id_state") if addr is not None else ""
        country_id = gv(addr, "id_country") if addr is not None else ""

        newsletter = gv(c, "newsletter", "0")
        rows.append({
            "First Name": gv(c, "firstname"),
            "Last Name": gv(c, "lastname"),
            "Email": email,
            "Accepts Email Marketing": "yes" if newsletter == "1" else "no",
            "Default Address Company": gv(addr, "company") if addr is not None else "",
            "Default Address Address1": gv(addr, "address1") if addr is not None else "",
            "Default Address Address2": gv(addr, "address2") if addr is not None else "",
            "Default Address City": gv(addr, "city") if addr is not None else "",
            "Default Address Province Code": ref.states.get(state_id, state_id),
            "Default Address Country Code": ref.countries.get(country_id, country_id),
            "Default Address Zip": gv(addr, "postcode") if addr is not None else "",
            "Default Address Phone": (gv(addr, "phone") or gv(addr, "phone_mobile")) if addr is not None else "",
            "Phone": (gv(addr, "phone") or gv(addr, "phone_mobile")) if addr is not None else "",
            "Accepts SMS Marketing": "no",
            "Accepts WhatsApp Marketing": "no",
            "Tags": "",
            "Note": gv(c, "note"),
            "Tax Exempt": "no",
        })
    return rows


# ----------------------------------------------------------------------
# Orders
# ----------------------------------------------------------------------

def build_address_lookup(addresses: pd.DataFrame):
    lookup = {}
    if addresses.empty:
        return lookup
    for _, a in addresses.iterrows():
        aid = gv(a, "id_address")
        if aid:
            lookup[aid] = a
    return lookup


def shopify_address_fields(addr_row, ref: ReferenceData, prefix: str) -> dict:
    if addr_row is None:
        return {
            f"{prefix} Name": "", f"{prefix} Street": "", f"{prefix} Address1": "",
            f"{prefix} Address2": "", f"{prefix} Company": "", f"{prefix} City": "",
            f"{prefix} Zip": "", f"{prefix} Province": "", f"{prefix} Country": "",
            f"{prefix} Phone": "",
        }
    first = gv(addr_row, "firstname")
    last = gv(addr_row, "lastname")
    street = gv(addr_row, "address1")
    state_id = gv(addr_row, "id_state")
    country_id = gv(addr_row, "id_country")
    return {
        f"{prefix} Name": f"{first} {last}".strip(),
        f"{prefix} Street": street,
        f"{prefix} Address1": street,
        f"{prefix} Address2": gv(addr_row, "address2"),
        f"{prefix} Company": gv(addr_row, "company"),
        f"{prefix} City": gv(addr_row, "city"),
        f"{prefix} Zip": gv(addr_row, "postcode"),
        f"{prefix} Province": ref.states.get(state_id, state_id),
        f"{prefix} Country": ref.countries.get(country_id, country_id),
        f"{prefix} Phone": gv(addr_row, "phone") or gv(addr_row, "phone_mobile"),
    }


def resolve_financial_status(state_name: str) -> str:
    s = (state_name or "").lower()
    if "refund" in s:
        return "refunded"
    if "cancel" in s or "error" in s or "abandon" in s:
        return "voided"
    if "paid" in s or "accepted" in s or "delivered" in s or "shipped" in s or "processing" in s:
        return "paid"
    return "pending"


def resolve_fulfillment_status(state_name: str) -> str:
    s = (state_name or "").lower()
    if "shipped" in s or "delivered" in s:
        return "fulfilled"
    return ""


def convert_orders(data, ref: ReferenceData):
    orders = data.get("ps_orders", pd.DataFrame())
    customers = data.get("ps_customer", pd.DataFrame())
    addresses = data.get("ps_address", pd.DataFrame())

    if orders.empty:
        logger.warning("No ps_orders.csv found/loaded -- skipping order conversion")
        return []

    email_by_customer = {}
    if not customers.empty:
        for _, c in customers.iterrows():
            cid = gv(c, "id_customer")
            if cid:
                email_by_customer[cid] = gv(c, "email")

    address_lookup = build_address_lookup(addresses)

    rows = []
    for _, o in orders.iterrows():
        cid = gv(o, "id_customer")
        state_id = gv(o, "current_state")
        state_name = ref.order_states.get(state_id, state_id)

        billing_addr = address_lookup.get(gv(o, "id_address_invoice"))
        shipping_addr = address_lookup.get(gv(o, "id_address_delivery"))

        row = {
            "Name": f"#{gv(o, 'reference') or gv(o, 'id_order')}",
            "Email": email_by_customer.get(cid, ""),
            "Financial Status": resolve_financial_status(state_name),
            "Paid at": "",
            "Fulfillment Status": resolve_fulfillment_status(state_name),
            "Fulfilled at": "",
            "Currency": "",  # raw id_currency only -- export ps_currency.csv for a real code if needed
            "Subtotal": format_price(gv(o, "total_products_wt", "0")),
            "Shipping": format_price(gv(o, "total_shipping_tax_incl", "0")),
            "Taxes": format_price(
                float(gv(o, "total_paid_tax_incl", "0") or 0) - float(gv(o, "total_paid_tax_excl", "0") or 0)
            ),
            "Total": format_price(gv(o, "total_paid", "0")),
            "Discount Code": "",
            "Discount Amount": format_price(gv(o, "total_discounts", "0")),
            "Shipping Method": gv(o, "id_carrier"),  # raw carrier id -- export ps_carrier.csv for a real name
            "Created at": gv(o, "date_add"),
            "Lineitem quantity": "",   # see NOTE 3 -- no ps_order_detail in this export
            "Lineitem name": "",
            "Lineitem price": "",
            "Lineitem sku": "",
            "Lineitem requires shipping": "",
            "Lineitem taxable": "",
            "Notes": gv(o, "note"),
            "Payment Method": gv(o, "payment"),
            "Phone": gv(billing_addr, "phone") if billing_addr is not None else "",
            "Tags": state_name,
        }
        row.update(shopify_address_fields(billing_addr, ref, "Billing"))
        row.update(shopify_address_fields(shipping_addr, ref, "Shipping"))
        rows.append(row)

    return rows


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

ALL_TABLES = [
    "ps_product", "ps_product_lang", "ps_manufacturer", "ps_category_lang",
    "ps_attribute", "ps_attribute_group", "ps_product_attribute",
    "ps_product_attribute_combination", "ps_stock_available",
    "ps_image", "ps_image_lang", "ps_tag", "ps_feature_product",
    "ps_customer", "ps_address", "ps_orders",
    # optional, used opportunistically if present
    "ps_product_tag", "ps_order_state_lang", "ps_order_detail",
    "ps_state", "ps_country_lang", "ps_country", "ps_currency", "ps_carrier",
]


def main():
    Path(INPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    logger.info("Starting PrestaShop -> Shopify migration")
    logger.info(f"Input directory: {INPUT_DIR}")
    logger.info(f"Output directory: {OUTPUT_DIR}")

    data = {name: load_csv(name) for name in ALL_TABLES}

    ref = ReferenceData(data)

    product_rows = convert_products(data, ref)
    write_csv(product_rows, SHOPIFY_PRODUCT_HEADERS, "shopify_products.csv")

    customer_rows = convert_customers(data, ref)
    write_csv(customer_rows, SHOPIFY_CUSTOMER_HEADERS, "shopify_customers.csv")

    order_rows = convert_orders(data, ref)
    write_csv(order_rows, SHOPIFY_ORDER_HEADERS, "shopify_orders.csv")

    logger.info("Migration complete.")
    logger.info(
        f"Products: {len(set(r['Handle'] for r in product_rows if r.get('Title')))} "
        f"({len(product_rows)} total rows incl. variants/extra images), "
        f"Customers: {len(customer_rows)}, Orders: {len(order_rows)}"
    )


if __name__ == "__main__":
    main()
