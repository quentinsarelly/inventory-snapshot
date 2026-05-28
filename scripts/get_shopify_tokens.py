"""
One-time script to get permanent Shopify offline access tokens via OAuth.

Before running:
  1. In the Partner Dashboard (dev.shopify.com), open your app
  2. Go to App setup → URLs → Add "http://localhost:3000/callback" to Allowed redirection URLs
  3. Save

Then run:
  python scripts/get_shopify_tokens.py us
  python scripts/get_shopify_tokens.py mx

Copy the printed token into .env and GitHub secrets.
"""
import os
import sys
import webbrowser
import secrets
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.environ["SHOPIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SHOPIFY_CLIENT_SECRET"]
REDIRECT_URI  = "http://localhost:3000/callback"
SCOPES        = "read_products,read_inventory"

store = sys.argv[1].lower() if len(sys.argv) > 1 else input("Store (us/mx): ").strip().lower()
shop_domain = os.environ[f"SHOPIFY_{store.upper()}_SHOP_DOMAIN"]
nonce = secrets.token_hex(16)

auth_url = (
    f"https://{shop_domain}.myshopify.com/admin/oauth/authorize"
    f"?client_id={CLIENT_ID}"
    f"&scope={SCOPES}"
    f"&redirect_uri={REDIRECT_URI}"
    f"&state={nonce}"
    f"&grant_options[]=offline"
)

auth_code = None

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        params = parse_qs(urlparse(self.path).query)
        returned_state = params.get("state", [None])[0]
        if returned_state != nonce:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"State mismatch - possible CSRF. Try again.")
            return
        auth_code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>Authorized! You can close this tab.</h1>")

    def log_message(self, *args):
        pass

print(f"\nOpening browser for {shop_domain}...")
print("If the browser doesn't open automatically, visit:\n")
print(f"  {auth_url}\n")
webbrowser.open(auth_url)

print("Waiting for Shopify to redirect back...")
HTTPServer(("localhost", 3000), _Handler).handle_request()

if not auth_code:
    print("ERROR: no auth code received.")
    sys.exit(1)

resp = requests.post(
    f"https://{shop_domain}.myshopify.com/admin/oauth/access_token",
    data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "code": auth_code},
    timeout=30,
)
resp.raise_for_status()
token = resp.json().get("access_token")

if not token:
    print(f"ERROR: unexpected response: {resp.json()}")
    sys.exit(1)

print(f"\nSHOPIFY_{store.upper()}_ACCESS_TOKEN={token}")
print("\nUpdate this value in:")
print("  1. .env")
print(f"  2. GitHub secret: SHOPIFY_{store.upper()}_ACCESS_TOKEN")
