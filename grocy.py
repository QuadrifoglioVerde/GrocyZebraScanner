#!/usr/bin/python

import signal
import sys
import json
import requests
import pprint
import time
import openfoodfacts

from zebra_scanner import CoreScanner

# Pretty printer for debugging
pp = pprint.PrettyPrinter(indent=2)
cs = CoreScanner()

# Configuration
GROCY_API = 'DOPLN_GROCY_API_KEY'
ADD_ID = '11'
INFO_ID = '22'
BASE_URL = 'http://docker.lan:9192/api'
MODE = 0

ha_url = 'http://ha.lan:8123/api/services/tts/google_cloud_say'
ha_token = 'DOPLN_HA_LONGTERM_KEY'
media_player = 'media_player.nestmini3527'


# Open Food Facts API initialization
api = openfoodfacts.API(user_agent="ZebraGrocy/1.0")

# Event handler when a new scanner is added
@cs.on_scanner_added
def on_scanner_added(scanner):
    scanner.pull_trigger()
    if scanner.GUID:
        print(f"Scanner {scanner.GUID}")
        @scanner.on_barcode
        def on_barcode(barcode):
            handle_barcode_scan(barcode.code)

# Event handler when a scanner is removed
def on_scanner_removed(scanner):
    print("Scanner removed")
    scanner.release_trigger()

# Modifikace handleru čárových kódů
def handle_barcode_scan(barcode):
    global MODE, last_scan_time, ADD_ID, INFO_ID
    print(f"Scanned: {barcode}")
    # Aktualizace času posledního skenu
    last_scan_time = time.time()

    if barcode == ADD_ID and MODE == 0:
        MODE = 1
        print("Entering ADD mode")
        if ha_token:
            ha_call("Režim nákupu")
    elif barcode == ADD_ID and (MODE == 1 or MODE == 2):
        MODE = 0
        print("Entering CONSUME mode")
        if ha_token:
            ha_call("Režim spotřeby")
    elif barcode == INFO_ID:
        MODE = 2
        print("Entering INFO mode. Scan the product to check its inventory.")
        if ha_token:
            ha_call("Režim zjištění zásob, naskenujte produkt.")
    elif MODE == 2:
        check_inventory(barcode)
        MODE = 0  # Reset INFO_MODE after checking inventory
    elif MODE == 1 and barcode != ADD_ID:
        increase_inventory(barcode)
    elif MODE == 0 and barcode != ADD_ID:
        decrease_inventory(barcode)

# Funkce pro kontrolu zásob produktu
def check_inventory(upc):
    global response_code, stock_amount
    if product_id_lookup(upc):
        print(f"{product_name} has {stock_amount} in stock.")
        if ha_token:
            ha_call(f"{product_name}, skladem máte {stock_amount}")
    else:
        print(f"Produkt s čárovým kódem {upc} nebyl nalezen v Grocy")
        if ha_token:
            ha_call(f"Produkt s čárovým kódem {upc} nebyl nalezen v Grocy")

# Function to increase inventory
def increase_inventory(upc):
    global response_code, product_name
    if product_id_lookup(upc):
        print(f"Increasing {product_name}")
        url = f"{BASE_URL}/stock/products/{product_id}/add"
        data = {'amount': purchase_amount, 'transaction_type': 'purchase'}
        grocy_api_call_post(url, data)
        if response_code == 200 and ha_token:
            ha_call(f"{product_name} navýšen o {purchase_amount}")
        else:
            print(f"Failed to increase the value of {product_name}")
    else:
        print(f"Attempting to look it up in Open Food Facts...")
        off_product_lookup(upc)

# Function to decrease inventory
def decrease_inventory(upc):
    global response_code, stock_amount
    if product_id_lookup(upc):
        if stock_amount > 0:
            print(f"Decreasing {product_name} by 1")
            url = f"{BASE_URL}/stock/products/{product_id}/consume"
            data = {'amount': 1, 'transaction_type': 'consume', 'spoiled': 'false'}
            grocy_api_call_post(url, data)
            if response_code == 400:
                print(f"Failed to decrease the value of {product_name}")
                ha_call(f"Failed to decrease {product_name}")
            if ha_token and response_code == 200:
                ha_call(f"Spotřebováno {product_name}. Zbývá {stock_amount - 1}")
        elif stock_amount == 0:
            print(f"The current stock for {product_name} is 0, nothing to decrease")
            if ha_token:
                ha_call(f"Nemáte {product_name}, nelze odebrat")
    else:
        print(f"Attempting to look it up in Open Food Facts...")
        off_product_lookup(upc)

# Lookup the product in Open Food Facts if not found in Grocy
def off_product_lookup(upc):
    try:
        product = api.product.get(upc, fields=["code", "product_name"])

        if product:
            product_name = product.get('product_name', 'Unknown product')
            if product_name != "" and product_name != "Unknown product":
                print(f"Found in Open Food Facts: {product_name}")
                if ha_token:
                    ha_call(f"Nalezeno na Open Food Facts jako {product_name}")
                # Add product to Grocy system if found in Open Food Facts
                add_to_system(upc, product_name, "Automatically added from Open Food Facts")
            else:
                print(f"Broken Open Food Facts record")
                if ha_token:
                    ha_call(f"Poškozený záznam na Open Food Facts, přidej ručně")
        else:
            print(f"Not found in Open Food Facts for UPC: {upc}")
            if ha_token:
                ha_call(f"Produkt nebyl nikde nalezen, přidej ručně")
    except:
        print(f"Error in receiving OFF data")
        if ha_token:
            ha_call(f"Chyba v získávání dat, zkuste to později")

# Function to add a new product to the system
def add_to_system(upc, name, description):
    global response_code
    url = f"{BASE_URL}/objects/products"
    data = {
        "name": name,
        "description": description,
        "location_id": 1,
        "qu_id_purchase": 2,
        "qu_id_stock": 2,
        "default_best_before_days": -1,
        "min_stock_amount": 0,
        "treat_opened_as_out_of_stock": 0
    }
    response = grocy_api_call_post(url, data)
    if response and response.get('created_object_id'):
        product_id = response['created_object_id']
        print(f"Just added {name} to the system with ID {product_id}")
        # Add the barcode to the newly added product
        add_barcode_to_product(upc, product_id)
    else:
        print(f"Adding the product with {upc} failed")

# Function to add a barcode to a product
def add_barcode_to_product(upc, product_id):
    global response_code
    url = f"{BASE_URL}/objects/product_barcodes"
    data = {
        "barcode": upc,
        "product_id": product_id,
        "amount": "1.0",
        "shopping_location_id": 1
    }
    response = grocy_api_call_post(url, data)
    if response and response.get('created_object_id'):
        print(f"Successfully added barcode {upc} to product ID {product_id}")
    else:
        print(f"Failed to add barcode {upc} to product ID {product_id}")

# Lookup the product ID by UPC (only in Grocy)
def product_id_lookup(upc):
    global product_id, purchase_amount, product_name, stock_amount, response_code
    print("Looking up the product in Grocy")
    url = f"{BASE_URL}/stock/products/by-barcode/{upc}"
    headers = {'cache-control': "no-cache", 'GROCY-API-KEY': GROCY_API}
    try:
        r = requests.get(url, headers=headers)
        response_code = r.status_code
        if response_code == 400:
            print("Product not found in Grocy")
            return False
        else:
            j = r.json()
            product_id = j['product']['id']
            product_name = j['product']['name']
            stock_amount = j['stock_amount']
            purchase_amount = j['qu_conversion_factor_purchase_to_stock']
            print(f"Our product is {product_id}")
            return True
    except requests.RequestException as e:
        print(e)
        return False

# Function to make a POST call to the Grocy API
def grocy_api_call_post(url, data):
    global response_code
    headers = {'cache-control': "no-cache", 'GROCY-API-KEY': GROCY_API}
    try:
        r = requests.post(url=url, json=data, headers=headers)
        response_code = r.status_code
        if response_code in [200, 204]:
            return r.json()
        else:
            print(f"Error {response_code}: {r.text}")
            return None
    except requests.RequestException as e:
        print(e)
        response_code = 500  # Internal error code in case of exception
        return None

# Modify the ha_call function to use declined message
def ha_call(message_text):
    headers = {
        'Authorization': f'Bearer {ha_token}',
        'Content-Type': 'application/json'
    }
    data = {
        "entity_id": media_player,
        "message": message_text,
    }
    r = requests.post(url=ha_url, json=data, headers=headers)
    if r.status_code != 200:
        print(f"HA call failed with status code {r.status_code}")
    else:
        print(f"HA call: {message_text}")

# Initialize the last scan time
last_scan_time = time.time()

# Keep the program running and handle barcode scans
while True:
    # Reset ADD mode if 5 minutes have passed since the last scan
    if time.time() - last_scan_time > 300 and MODE != 0:
        MODE = 0
        print("Reverting MODE to 0 due to timeout")
        if ha_token:
            ha_call("Vypršel časový limit, nastavuji režim spotřeby.")

    time.sleep(0.10)
