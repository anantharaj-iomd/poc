#!/usr/bin/env python3
"""
Shopify MCP Client v2
---------------------
Standalone Python script that discovers MCP endpoints or uses a provided one, attempts a mock order,
and always saves order_confirmation.json (even if mocked or fallback).

Usage:
    python client.py --base https://nodenorthstar.com --output ./out [--endpoint https://nodenorthstar.com/api/mcp] [--deep-discovery] [--buy-again MOCK-12345] [--fetch-products]

Requirements:
    pip install requests
"""

import argparse, requests, json, time, urllib.parse, os, sys

DEFAULT_TIMEOUT = 15

USER_DATA = {
    "email": "anantharaj_ext@iomd.info",
    "first_name": "Ananth",
    "last_name": "Test MCP",
    "address1": "900 Menlo Park Rd",
    "address2": "Suite 100",
    "city": "Helena",
    "province": "MT",
    "country": "US",
    "zip": "59602",
    "phone": "+1-418-543-8090",
    "shipping_same_as_billing": True,
    "card": {"number": "4111111111111111", "cvv": "111", "expiry": "12/33"}
}

# --- Mock Order History Database (for Buy Again flow) ---
MOCK_ORDER_HISTORY = {
    "MOCK-12345": {
        "order_id": "6484934525002",
        "status": "delivered",
        "items": [
            {"product_variant_id": "41891279732810", "quantity": 4}, 
        ],
        "customer": {
            "email": "anantharaj@nodeconnects.com",
            "first_name": "John",
            "last_name": "Doe",
            "address1": "900 Menlo Park Rd",
            "address2": "Suite 100",
            "city": "Helena",
            "province": "MT",
            "country": "US",
            "zip": "59602",
            "phone": "+1-418-543-8090",
            "card": {"number": "4111111111111111", "cvv": "111", "expiry": "12/33"}
        }
    }
}

BASIC_PATHS = [
    "/.well-known/mcp",
    "/.well-known/mcp/capabilities",
    "/mcp",
    "/mcp/capabilities",
    "/products.json",
    "/cart/add.js",
    "/cart.json",
]

DEEP_PATHS = BASIC_PATHS + [
    "/apps/mcp",
    "/api/mcp",
    "/mcp/metadata",
    "/checkout/extensions/mcp",
    "/checkout/mcp",
    "/.well-known/ai-plugin.json",
    "/.well-known/openapi.json",
    "/api/mcp/capabilities",
]

def safe_get(url):
    try:
        r = requests.get(url, timeout=DEFAULT_TIMEOUT)
        return {"status": r.status_code, "headers": dict(r.headers), "text": r.text[:20000]}
    except Exception as e:
        return {"error": str(e)}

def discover(base_url, deep=False):
    print(f"Running {'deep' if deep else 'basic'} discovery...")
    results = {}
    paths = DEEP_PATHS if deep else BASIC_PATHS
    for p in paths:
        url = urllib.parse.urljoin(base_url, p)
        print("GET", url)
        results[p] = safe_get(url)
        time.sleep(0.1)
    return results

def fetch_products(endpoint_url, limit=250):
    """
    Fetches products from the MCP server using the search_shop_catalog tool.
    Returns a dict containing the status, parsed products list (if any),
    and error information if the MCP call fails.
    """
    print(f"Fetching products via MCP endpoint: {endpoint_url}")
    
    # search_shop_catalog requires a context argument as a JSON string
    context_json = json.dumps({
        "buyer_identity": {
            "email": USER_DATA["email"]
        }
    })
    
    payload = {
        "name": "search_shop_catalog",
        "arguments": {
            "query": "",  # Empty query to get all products
            "limit": limit,
            "context": context_json
        }
    }
    
    rpc_request = {
        "jsonrpc": "2.0",
        "id": str(int(time.time() * 1000)),
        "method": "tools/call",
        "params": payload,
    }
    
    try:
        r = requests.post(endpoint_url, json=rpc_request, timeout=DEFAULT_TIMEOUT)
        if r.status_code == 200:
            try:
                body = r.json()
                if body.get("result") and not body.get("result", {}).get("isError", False):
                    # Try to parse the response text
                    content = body.get("result", {}).get("content", [])
                    if content and len(content) > 0:
                        text_content = content[0].get("text", "")
                        try:
                            # Try parsing as JSON
                            parsed = json.loads(text_content)
                            products = parsed.get("products") if isinstance(parsed, dict) else parsed
                            if products:
                                count = len(products) if isinstance(products, list) else 0
                                print(f"✅ Fetched {count} products via MCP using search_shop_catalog")
                                return {
                                    "endpoint": endpoint_url,
                                    "status": r.status_code,
                                    "tool_used": "search_shop_catalog",
                                    "products": products,
                                    "raw": body
                                }
                        except json.JSONDecodeError:
                            # If not JSON, return the text
                            print(f"✅ Received response via MCP (non-JSON)")
                            return {
                                "endpoint": endpoint_url,
                                "status": r.status_code,
                                "tool_used": "search_shop_catalog",
                                "products": None,
                                "raw": body,
                                "text": text_content[:5000]
                            }
            except Exception as e:
                print(f"Error parsing MCP response: {e}")
                return {
                    "endpoint": endpoint_url,
                    "error": str(e),
                    "raw_response": r.text[:1000] if r else None
                }
        else:
            print(f"MCP call failed with status {r.status_code}")
            return {
                "endpoint": endpoint_url,
                "status": r.status_code,
                "error": f"HTTP {r.status_code}",
                "response": r.text[:1000] if r else None
            }
    except Exception as e:
        print(f"Error calling MCP endpoint: {e}")
        return {
            "endpoint": endpoint_url,
            "error": str(e)
        }

def attempt_buy(endpoint_url, items=None, customer=None, is_buy_again=False, original_order_id=None):
    """
    Attempts to create a mock order at the MCP endpoint.
    - If `items` is provided, it uses them; otherwise it uses a default test SKU.
    - If `customer` is provided, it uses their details; otherwise it uses default USER_DATA.
    """
    if customer is None:
        customer = USER_DATA
        print(f"Attempting to create order for {customer['email']} at MCP endpoint: {endpoint_url}")
    else:
        print(f"Attempting to create 'Buy Again' order for {customer['email']} at MCP endpoint: {endpoint_url}")

    if items is None:
        items = [
                {
                    "product_variant_id": "gid://shopify/ProductVariant/42419705905226", 
                    "quantity": 2
                }
            ]
        print("Using default test item.")

    payload = {
        "name": "update_cart",
        "arguments": {
            "add_items": items,
            "delivery_addresses_to_add": [
                {
                    "selected": True,
                    "delivery_address": {
                        "first_name": USER_DATA["first_name"],
                        "last_name": USER_DATA["last_name"],
                        "phone": USER_DATA["phone"],
                        "address1": USER_DATA["address1"],
                        "address2": USER_DATA["address2"],
                        "city": USER_DATA["city"],
                        "province_code": USER_DATA["province"],
                        "zip": USER_DATA["zip"],
                        "country_code": USER_DATA["country"]
                    }
                }
            ],
            "buyer_identity": {
                "email": USER_DATA["email"],
            },
        }
    }
    
    rpc_request = {
        "jsonrpc": "2.0",
        "id": str(int(time.time() * 1000)),
        "method": "tools/call",
        "params": payload,
    }

    try:
        r = requests.post(endpoint_url, json=rpc_request, timeout=DEFAULT_TIMEOUT)
        try:
            body = r.json()
            response = body.get("result").get("content")[0].get("text")
            response = json.loads(response)
            checkout_url = response.get("cart").get("checkout_url") 
            print("Checkout URL:", checkout_url)
        except Exception:
            body = {"text": r.text[:5000]}
        print("MCP POST status:", r.status_code)
        return {"endpoint": endpoint_url, "status": r.status_code, "response": body}
    except Exception as e:
        print("Error calling endpoint:", e)
        return {"endpoint": endpoint_url, "error": str(e)}

def fallback_order_mock(base_url):
    print("Falling back to Shopify cart mock order creation...")
    order = {
        "order_id": "MOCK-" + str(int(time.time())),
        "base_url": base_url,
        "items": [{"sku": "FAKE-SKU-0001", "quantity": 1}],
        "status": "mock-confirmation",
        "note": "This is a simulated order confirmation for testing purposes only."
    }
    return order

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="Base store URL, e.g. https://nodenorthstar.com/")
    parser.add_argument("--output", default="./out", help="Output directory")
    parser.add_argument("--endpoint", help="Optional MCP endpoint URL to skip discovery")
    parser.add_argument("--deep-discovery", action="store_true", help="Perform extended endpoint scanning")
    parser.add_argument("--buy-again", help="Order ID to 'buy again' from MOCK_ORDER_HISTORY (requires --endpoint)")
    parser.add_argument("--fetch-products", action="store_true", help="Fetch products via MCP server using search_shop_catalog (requires --endpoint)")
    parser.add_argument(
        "--variant-id",
        action="append",
        help="Product variant entry (<id> or <id>:<qty>). "
             "IDs can be numeric or gid://...; quantity defaults to 1 if omitted. "
             "Provide multiple --variant-id flags for multiple products.")
    args = parser.parse_args()

    base = args.base if args.base.endswith("/") else args.base + "/"
    os.makedirs(args.output, exist_ok=True)

    result = None

    # Prepare custom item selection if requested
    custom_items = None
    if args.variant_id:
        custom_items = []
        for raw_variant in args.variant_id:
            variant_spec = raw_variant.strip()
            if ":" in variant_spec:
                variant_id_part, qty_part = variant_spec.split(":", 1)
                variant_id = variant_id_part.strip()
                try:
                    qty = int(qty_part.strip())
                except ValueError:
                    print(f"Error: quantity '{qty_part}' in '{variant_spec}' is not a valid integer.")
                    sys.exit(1)
            else:
                variant_id = variant_spec
                qty = 1
            if qty <= 0:
                print(f"Error: quantity in '{variant_spec}' must be greater than zero.")
                sys.exit(1)
            if not variant_id.startswith("gid://"):
                variant_id = f"gid://shopify/ProductVariant/{variant_id}"
            custom_items.append({"product_variant_id": variant_id, "quantity": qty})
            print(f"Using custom variant {variant_id} (quantity {qty}) for purchase.")

    # --- Buy Again Flow ---
    if args.buy_again:
        if not args.endpoint:
            print("Error: --buy-again requires --endpoint to be set. Skipping discovery.")
            sys.exit(1)

        print(f"Looking for order '{args.buy_again}' in mock history...")
        order_to_buy_again = MOCK_ORDER_HISTORY.get(args.buy_again)

        if order_to_buy_again:
            items_to_buy = order_to_buy_again.get("items")
            customer_for_order = order_to_buy_again.get("customer")
            original_order_id = order_to_buy_again.get("order_id")

            if items_to_buy and customer_for_order:
                print(f"Found items: {items_to_buy}")
                print(f"Found customer: {customer_for_order['email']}")
                normalized_items = []
                for item in items_to_buy:
                    variant_id = item.get("product_variant_id")
                    if not variant_id:
                        print("Error: One of the buy-again items is missing 'product_variant_id'.")
                        sys.exit(1)
                    if not str(variant_id).startswith("gid://"):
                        variant_id = f"gid://shopify/ProductVariant/{variant_id}"
                    normalized_items.append({
                        "product_variant_id": variant_id,
                        "quantity": item.get("quantity", 1)
                    })
                result = attempt_buy(args.endpoint, normalized_items, customer_for_order, is_buy_again=True, original_order_id=original_order_id)
            else:
                print(f"Error: Order '{args.buy_again}' is missing 'items' or 'customer' data.")
                sys.exit(1)
        else:
            print(f"Error: Order ID '{args.buy_again}' not found in MOCK_ORDER_HISTORY.")
            sys.exit(1)

    # --- Optional Products Fetch via MCP ---
    if args.fetch_products:
        if not args.endpoint:
            print("Error: --fetch-products requires --endpoint to be set.")
            sys.exit(1)
        products = fetch_products(args.endpoint)
        product_path = os.path.join(args.output, "products.json")
        with open(product_path, "w") as f:
            json.dump(products, f, indent=2)
        print(f"✅ Saved {product_path}")
        sys.exit(0)

    # --- Standard Discovery/Test Flow ---
    if result is None:
        if args.endpoint:
            print(f"Using provided endpoint: {args.endpoint}")
            result = attempt_buy(args.endpoint, items=custom_items if custom_items else None)
        else:
            discovery = discover(base, deep=args.deep_discovery)
            with open(os.path.join(args.output, "discovery.json"), "w") as f:
                json.dump(discovery, f, indent=2)
            found_endpoint = None
            for path, resp in discovery.items():
                if isinstance(resp, dict) and "mcp" in json.dumps(resp).lower():
                    found_endpoint = urllib.parse.urljoin(base, path)
                    break
            if found_endpoint:
                result = attempt_buy(found_endpoint, items=custom_items if custom_items else None)
            else:
                print("No MCP endpoint found; generating mock confirmation.")
                result = {"endpoint": None, "response": fallback_order_mock(base)}

    if result:
        order_conf_path = os.path.join(args.output, "order_confirmation.json")
        with open(order_conf_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"✅ Saved {order_conf_path}")
    else:
        print("No result was generated.")

if __name__ == "__main__":
    main()