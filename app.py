import os
import time
import requests
from flask import Flask, render_template, jsonify, request
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# Shopify credentials
SHOPIFY_STORE_URL = os.getenv('SHOPIFY_STORE_URL')
SHOPIFY_ACCESS_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN')
SHOPIFY_API_VERSION = os.getenv('SHOPIFY_API_VERSION', "2024-07")

# --- Helper: Safe API request with retry and throttle ---
def shopify_request(method, url, headers, **kwargs):
    for attempt in range(5):
        try:
            response = requests.request(method, url, headers=headers, timeout=15, **kwargs)

            # Handle rate limit
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 2))
                time.sleep(retry_after)
                continue

            # Retry for server errors
            if 500 <= response.status_code < 600:
                time.sleep(1)
                continue

            return response

        except requests.exceptions.RequestException:
            time.sleep(1)
    return None


@app.route('/')
def index():
    return render_template('bulk_mark.html')


@app.route('/api/get_order/<order_name>')
def get_order_mark_paid(order_name):
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    shopify_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders.json"
    params = {"status": "any", "name": f"#{order_name}" if not order_name.startswith("#") else order_name}

    r = shopify_request("GET", shopify_url, headers, params=params)
    if not r:
        return jsonify({"error": "Request failed"}), 500

    if r.status_code != 200:
        return jsonify({"error": "Failed to fetch order"}), r.status_code

    data = r.json()
    orders = data.get('orders', [])
    if not orders:
        return jsonify({"error": "Order not found"}), 404

    order = orders[0]
    return jsonify({
        "order_id": order['id'],
        "order_name": order['name'],
        "payment_status": order.get('financial_status'),
        "total_price": order.get('total_price'),
        "tags": order.get('tags', "")
    })


@app.route('/check_csv_orders', methods=['POST'])
def check_csv_orders():
    data = request.get_json()
    order_numbers = data.get('orders', [])

    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    results = []
    for raw_order_number in order_numbers:
        clean_order_number = raw_order_number.lstrip("#")
        time.sleep(0.4)  # small delay per request

        shopify_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders.json"
        params = {"status": "any", "name": f"#{clean_order_number}"}
        r = shopify_request("GET", shopify_url, headers, params=params)

        if not r or r.status_code != 200:
            results.append({"order_number": clean_order_number, "status": "Error fetching order"})
            continue

        try:
            orders = r.json().get('orders', [])
        except Exception:
            results.append({"order_number": clean_order_number, "status": "Invalid JSON response"})
            continue

        if not orders:
            status = "Order Not Found"
        else:
            order = orders[0]
            tags = [t.strip().lower() for t in order.get("tags", "").split(",") if t.strip()]
            if "paid" in tags:
                status = "Already Tagged Paid"
            elif order.get("financial_status") == "paid":
                status = "Already Paid"
            else:
                status = "Valid"

        results.append({"order_number": clean_order_number, "status": status})

    return jsonify({"results": results})


@app.route('/api/mark_paid_batch', methods=['POST'])
def mark_paid_batch():
    orders = request.json.get('orders', [])
    results = []

    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    for order_id in orders:
        time.sleep(0.5)  # prevent hitting Shopify rate limit

        tx_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}/transactions.json"
        tx_res = shopify_request("GET", tx_url, headers)
        if not tx_res or tx_res.status_code != 200:
            results.append({"order_id": order_id, "status": "error", "message": "Failed to fetch transactions"})
            continue

        transactions = tx_res.json().get('transactions', [])
        auth_tx = next((t for t in transactions if t['kind'] == 'authorization'), None)

        if auth_tx:
            capture_payload = {
                "transaction": {
                    "parent_id": auth_tx["id"],
                    "amount": auth_tx["amount"],
                    "kind": "capture"
                }
            }
            capture_res = shopify_request("POST", tx_url, headers, json=capture_payload)
            if capture_res and capture_res.status_code == 201:
                results.append({"order_id": order_id, "status": "success", "message": "Captured payment"})
            else:
                msg = capture_res.text if capture_res else "Capture request failed"
                results.append({"order_id": order_id, "status": "error", "message": msg})
            continue

        # Otherwise, tag as Paid
        order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}.json"
        order_res = shopify_request("GET", order_url, headers)
        if not order_res or order_res.status_code != 200:
            results.append({"order_id": order_id, "status": "error", "message": "Failed to fetch order"})
            continue

        order = order_res.json().get('order', {})
        current_tags = order.get("tags", "")
        if "Paid" in [t.strip() for t in current_tags.split(",")]:
            results.append({"order_id": order_id, "status": "skipped", "message": "Already tagged"})
            continue

        new_tags = current_tags + ", Paid" if current_tags else "Paid"
        update_payload = {"order": {"id": order_id, "tags": new_tags}}
        update_res = shopify_request("PUT", order_url, headers, json=update_payload)

        if update_res and update_res.status_code == 200:
            results.append({"order_id": order_id, "status": "success", "message": "Tag added"})
        else:
            msg = update_res.text if update_res else "Tag request failed"
            results.append({"order_id": order_id, "status": "error", "message": msg})

    return jsonify(results), 200


@app.route('/api/tag_order', methods=['POST'])
def tag_single_order():
    order_id = request.json.get('order_id')

    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}.json"
    order_res = shopify_request("GET", order_url, headers)
    if not order_res or order_res.status_code != 200:
        return jsonify(success=False, message="Order not found")

    order = order_res.json().get('order', {})
    current_tags = order.get("tags", "")
    if "Paid" in [t.strip() for t in current_tags.split(",")]:
        return jsonify(success=False, message="Already tagged Paid")

    new_tags = current_tags + ", Paid" if current_tags else "Paid"
    update_payload = {"order": {"id": order_id, "tags": new_tags}}
    update_res = shopify_request("PUT", order_url, headers, json=update_payload)

    if update_res and update_res.status_code == 200:
        return jsonify(success=True)
    return jsonify(success=False, message="Failed to update tags")


if __name__ == '__main__':
    app.run(debug=True)
