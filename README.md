We have created a free tool to convert PrestaShop data into Shopify-compatible format.
You can use this tool to convert your product, customer, and order data into files that are ready to import into Shopify.
Once converted, you can simply upload the new data files to Shopify.

Please see the detailed instructions at : **https://firstwireapp.com/blog/prestashop-to-shopify-migration-free-tool/**

See the code and guide below.


**Step 1 — Install Python (one-time setup)**

Python is the free program that runs the script. If you already have Python installed, skip to Step 2.
1. Go to python.org/downloads in your web browser.
2. Click the yellow "Download Python" button.
3. Open the downloaded file and run the installer.

**Important**

On the first install screen, tick the box that says 
"Add Python to PATH" before clicking Install.

4. Click Install Now and wait for it to finish.

To check it worked, open your terminal (Command Prompt on Windows, Terminal on Mac) and type:
 python --version

If you see a version number like "Python 3.12.0", you are ready for Step 2.


**Step 2 — Install the Required Add-ons**

The script needs one free add-on package to read and write CSV files. Open your terminal and type this single line: pip install pandas --break-system-packages

Press Enter and wait a few seconds for it to finish. You only need to do this once.

**Step 3 — Save Your Files in One Folder**

Create a new folder on your Desktop (for example, "prestashop-to-shopify"). 
Inside it, create another folder called "input" — this is where all your PrestaShop table exports will go. 
Your folder structure should look like this: prestashop-to-shopify/
 prestashop_to_shopify_converter.py 
 input/ 
  ps_product.csv 
  ps_product_lang.csv 
  ps_manufacturer.csv 
  ps_category_lang.csv 
  ps_attribute.csv 
  ps_attribute_group.csv 
  ps_product_attribute.csv 
  ps_product_attribute_combination.csv 
  ps_stock_available.csv ps_image.csv 
  ps_image_lang.csv ps_tag.csv 
  ps_product_tag.csv 
  ps_feature_product.csv 
  ps_customer.csv 
  ps_address.csv 
  ps_orders.csv 
  ps_order_detail.csv 
  ps_state.csv 
  ps_country.csv 
  ps_country_lang.csv 
  ps_currency.csv 
  ps_carrier.csv 
  ps_order_state_lang.csv
 output/
   
Place the script file directly inside "prestashop-to-shopify", and place your PrestaShop CSV exports (one CSV per database table, named exactly like the table) inside the "input" folder:

You do not need every file. At minimum:

• input/ps_product.csv + ps_product_lang.csv (if migrating products)

• input/ps_customer.csv (if migrating customers)

• input/ps_address.csv (if migrating addresses)

• input/ps_orders.csv (if migrating orders)

The rest are optional and each unlock one extra piece of detail — brand names, categories, variants, stock, images, tags, real country/state codes, order line items, and your store's real order status names. If a file is missing, the script simply skips that piece and logs a warning; it will not stop the conversion.

**Step 4 — Configure the Script (Optional)**

Unlike a config file, this script keeps its settings inside the script itself. If you need to customize anything, open prestashop_to_shopify_converter.py in a text editor and look near the top for:

PRESTASHOP_STORE_URL = "https://your-prestashop-store.com" (change this to your real store address — it's used to build the web address for each product image)

DEFAULT_LANGUAGE_ID = "1" (change this if your PrestaShop catalog's main language isn't language ID 1)

DEFAULT_WEIGHT_UNIT = "kg" (change this if your PrestaShop store's weight unit setting is actually pounds)

DEFAULT_INVENTORY_POLICY = "deny" (change to "continue" if you want Shopify to keep selling a variant once it's out of stock)

DEFAULT_ORDER_STATE_MAP (a list of PrestaShop's default order status names — only edit this, or export ps_order_state_lang.csv instead, if your store uses custom order statuses)

COUNTRY_ID_OVERRIDES and STATE_ID_OVERRIDES (empty by default — the fastest fix if you get a "country code invalid" error on import; add your store's specific PrestaShop id_country/id_state values here, e.g. COUNTRY_ID_OVERRIDES = {"21": "US"}, instead of re-exporting ps_country.csv/ps_state.csv)

**Step 5 — Run the Script**

5. Open your terminal.
6. Navigate to the folder you created. For example: cd Desktop/prestashop-to-shopify
7. Run the script by typing: 

python prestashop_to_shopify_converter.py

Unlike some tools, this script doesn't ask you any questions while it runs — it automatically reads whichever files you placed in the input folder and converts whatever it finds.

**Step 6 — Find Your Converted Files**

Once the script finishes, it creates a new folder called "output" inside your project folder. Open it to find:
File Name What It Contains
shopify_products.csv Your products (and variant rows, if any), ready for Shopify

shopify_customers.csv Your customers, ready for Shopify

shopify_orders.csv Your orders, ready for the Matrixify app

**Step 7 — Import Into Shopify**

Products

8. In Shopify Admin, go to Products.
9. Click the Import button (top right).
10. Choose the file shopify_products.csv and click Upload.
11. Review the preview, then click Import products.

Note: if you did not set PRESTASHOP_STORE_URL near the top of the script before running it, your image web addresses may not be correct yet — see Troubleshooting below.

Customers

12. In Shopify Admin, go to Customers.
13. Click Import customers.
14. Choose the file shopify_customers.csv and upload it.
15. Review the preview, then click Import customers.

Note: if you see a "country code invalid" error, PrestaShop's raw numeric country/state IDs made it into the file uncorrected — see Troubleshooting below for the fastest fix.

Orders (needs one extra free app)

Shopify does not allow orders to be imported directly. You need the free Matrixify app first:

16. In Shopify Admin, go to Apps → Shopify App Store.
17. Search for "Matrixify" and install it (free plan available).
18. Open Matrixify → click Import → Add file → choose shopify_orders.csv.
19. Review the mapping and click Import.

Note: if you didn't include ps_order_detail.csv, this import brings in order-level details only — Lineitem sku/name/price/quantity will be blank on every row.

**Troubleshooting — Common Questions**

Problem - Solution

"python is not recognized" Reinstall Python and make sure to tick "Add Python to PATH"

"No module named pandas" Run: pip install pandas --break-system-packages

File not found / missing table warnings Make sure each CSV is in the input folder and named exactly like its PrestaShop table (e.g. ps_product.csv). Warnings for optional tables are expected if you didn't export them.

Tags aren't showing up Export ps_product_tag.csv, or make sure ps_tag.csv itself has an id_product column

Product features aren't included This isn't supported yet regardless of which tables you export

Some products missing after import Check for a duplicate Handle or SKU — Shopify silently rejects these

"Country code invalid" on customer or order import This means ps_country.csv (and/or ps_state.csv) wasn't exported, so the raw PrestaShop id_country/id_state ended up in the file. Fastest fix: open the script and add your store's IDs to COUNTRY_ID_OVERRIDES and STATE_ID_OVERRIDES near the top (e.g. COUNTRY_ID_OVERRIDES = {"21": "US"}), then re-run. The complete fix is exporting ps_country.csv (not ps_country_lang.csv — the real 2-letter code lives on ps_country) and ps_state.csv.

Lineitem columns are blank on orders Export ps_order_detail.csv to include line items

Order Status/Financial Status looks wrong Export ps_order_state_lang.csv for your store's real order status names, or edit DEFAULT_ORDER_STATE_MAP in the script

Some images are missing in Shopify Set PRESTASHOP_STORE_URL near the top of the script to your real store address, then re-run — this is used to build each image's web address

Product options/variants look wrong This script's variant handling follows Shopify's standard CSV format but may not have been tested against your specific catalog — double check the first import closely if your store has product options

Order import fails Make sure you are using the Matrixify app, not Shopify's built-in import — Shopify cannot import orders directly

Quick Reference — Every Time You Run It

Open terminal in your project folder
Type: cd Desktop/prestashop-to-shopify
Type: python prestashop_to_shopify_converter.py
Find your results in the output folder

That's it — no coding required. If you run into any issue not listed above, check that your CSV files were exported correctly from PrestaShop and try again.

At FirstWire, we can do the complete migration and make sure that your new Shopify store is setup properly and optimized for Design, User Experience, Performance, SEO and CRO.

Please Contact Us for a custom proposal at https://firstwireapp.com/get-a-quotation/

You can also check our other Shopify Services at https://firstwireapp.com/e-commerce/shopify/
