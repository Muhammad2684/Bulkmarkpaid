import os
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

    try:
        r = requests.get(shopify_url, headers=headers, params=params)
        r.raise_for_status()
        orders = r.json().get('orders', [])

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

    except requests.exceptions.RequestException as e:
        return jsonify({"error": "Failed to fetch order", "details": str(e)}), 500


@app.route('/api/mark_paid_batch', methods=['POST'])
def mark_paid_batch():
    orders = request.json.get('orders', [])
    results = []

    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    for order_id in orders:
        # Step 1: Try to get authorization transaction
        tx_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}/transactions.json"
        tx_res = requests.get(tx_url, headers=headers)

        if tx_res.status_code != 200:
            results.append({"order_id": order_id, "status": "error", "message": "Failed to fetch transactions"})
            continue

        transactions = tx_res.json().get('transactions', [])
        auth_tx = next((t for t in transactions if t['kind'] == 'authorization'), None)

        if auth_tx:
            # Use capture
            capture_payload = {
                "transaction": {
                    "parent_id": auth_tx["id"],
                    "amount": auth_tx["amount"],
                    "kind": "capture"
                }
            }
        else:
            # Get order info
            order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}.json"
            order_res = requests.get(order_url, headers=headers)

            if order_res.status_code != 200:
                results.append({"order_id": order_id, "status": "error", "message": "Failed to fetch order"})
                continue

            order = order_res.json().get('order', {})
            current_tags = order.get("tags", "")

            # Check if already tagged
            if "Paid" in [t.strip() for t in current_tags.split(",")]:
                results.append({"order_id": order_id, "status": "skipped", "message": "Already has 'Paid' tag"})
                continue

            # Append "Paid" tag
            new_tags = current_tags + ", Paid" if current_tags else "Paid"

            update_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}.json"
            update_payload = {"order": {"id": order_id, "tags": new_tags}}

            update_res = requests.put(update_url, headers=headers, json=update_payload)

            if update_res.status_code == 200:
                results.append({"order_id": order_id, "status": "success", "message": "Tag added"})
            else:
                msg = update_res.json().get("errors", update_res.text)
                results.append({"order_id": order_id, "status": "error", "message": str(msg)})
    return jsonify(results), 200



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

        try:
            # Call Shopify API using name filter
            shopify_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders.json"
            params = {"status": "any", "name": f"#{clean_order_number}"}
            r = requests.get(shopify_url, headers=headers, params=params)
            r.raise_for_status()
            orders = r.json().get('orders', [])

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

        except Exception as e:
            status = f"Error: {str(e)}"

        results.append({
            "order_number": clean_order_number,
            "status": status
        })

    return jsonify({"results": results})

@app.route('/api/tag_order', methods=['POST'])
def tag_single_order():
    order_id = request.json.get('order_id')

    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    try:
        order_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}.json"
        order_res = requests.get(order_url, headers=headers)

        if order_res.status_code != 200:
            return jsonify(success=False, message="Order not found")

        order = order_res.json().get('order', {})
        current_tags = order.get("tags", "")

        if "Paid" in [t.strip() for t in current_tags.split(",")]:
            return jsonify(success=False, message="Already tagged Paid")

        new_tags = current_tags + ", Paid" if current_tags else "Paid"
        update_payload = {"order": {"id": order_id, "tags": new_tags}}
        update_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}.json"
        update_res = requests.put(update_url, headers=headers, json=update_payload)

        if update_res.status_code == 200:
            return jsonify(success=True)
        else:
            return jsonify(success=False, message="Failed to update tags")

    except Exception as e:
        return jsonify(success=False, message=str(e))

    return jsonify(results)


if __name__ == '__main__':
    app.run(debug=True)
