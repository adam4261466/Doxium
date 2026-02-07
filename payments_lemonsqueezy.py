from flask import Blueprint, request, jsonify, current_app
import os
import requests
import hmac
import hashlib
import json

payments_ls = Blueprint("payments_ls", __name__)

LS_API_BASE = "https://api.lemonsqueezy.com/v1"

def _get_ls_api_key():
    return os.environ.get("LEMON_SQUEEZY_API_KEY")

@payments_ls.route("/create-checkout", methods=["POST"])
def create_checkout():
    """
    Expects JSON: { "variant_id": "<variant id>", "redirect_url": "<optional redirect>" }
    Returns JSON: { "checkout_url": "<hosted checkout url>" }
    """
    data = request.get_json() or {}
    variant_id = data.get("variant_id")
    redirect_url = data.get("redirect_url") or data.get("success_url") or request.host_url

    if not variant_id:
        return jsonify({"error": "variant_id required"}), 400

    payload = {
        "checkout": {
            # adapt fields per your Lemon Squeezy product/variant setup
            "variant_id": variant_id,
            "redirect_url": redirect_url,
            # optional: "metadata": { "user_id": "..." }
        }
    }

    headers = {
        "Authorization": f"Bearer {_get_ls_api_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    resp = requests.post(f"{LS_API_BASE}/checkouts", headers=headers, json=payload, timeout=15)
    if resp.status_code not in (200, 201):
        current_app.logger.error("Lemon Squeezy checkout creation failed: %s", resp.text)
        return jsonify({"error": "failed to create checkout"}), 502

    resp_json = resp.json()
    # response shape may vary; attempt to extract a hosted checkout URL
    checkout_url = None
    if isinstance(resp_json, dict):
        # typical shape: { "data": { "id": "...", "attributes": { "url": "https://..." } } }
        checkout_url = (resp_json.get("data") or {}).get("attributes", {}).get("url")
        # fallback: top-level url
        if not checkout_url:
            checkout_url = resp_json.get("url") or resp_json.get("checkout_url")

    if not checkout_url:
        current_app.logger.error("Unexpected Lemon Squeezy response: %s", resp_json)
        return jsonify({"error": "no checkout url returned"}), 502

    return jsonify({"checkout_url": checkout_url})

@payments_ls.route("/webhook/lemonsqueezy", methods=["POST"])
def webhook_lemonsqueezy():
    """
    Verify HMAC SHA256 signature and map events to your app logic.
    Expected header(s): try common header names; adjust if your Lemon Squeezy setup uses a different header.
    """
    raw_body = request.get_data()
    secret = os.environ.get("LEMON_SQUEEZY_WEBHOOK_SECRET")
    if not secret:
        current_app.logger.error("LEMON_SQUEEZY_WEBHOOK_SECRET not set")
        return "", 500

    # Try common header names (adjust to actual header used by Lemon Squeezy)
    sig_header = request.headers.get("X-Signature") or request.headers.get("X-Lemon-Signature") or request.headers.get("X-LS-Signature")
    if not sig_header:
        current_app.logger.warning("No signature header present")
        return "", 400

    computed = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()

    # If header may include prefix like "sha256=", strip it
    sig_value = sig_header.split("=", 1)[-1] if "=" in sig_header else sig_header

    if not hmac.compare_digest(computed, sig_value):
        current_app.logger.warning("Invalid Lemon Squeezy webhook signature")
        return "", 400

    try:
        event = request.get_json(force=True)
    except Exception:
        current_app.logger.exception("Invalid JSON in webhook")
        return "", 400

    # Map Lemon Squeezy events to app logic.
    # Event shape varies; common event keys: event, type, or data.attributes
    # Examples to handle: purchase.created, subscription.created, subscription.updated, subscription.cancelled
    event_type = (event.get("event") or event.get("type") or event.get("event_name") or "")
    current_app.logger.info("Lemon Squeezy webhook received: %s", event_type)

    # Example mapping (replace with your actual business logic)
    if "purchase" in event_type:
        # handle purchase.created / purchase.updated
        # ...existing code...
        # e.g., create local order, grant access, send email
        pass
    elif "subscription" in event_type:
        if "created" in event_type:
            # ...existing code...
            pass
        elif "updated" in event_type:
            # ...existing code...
            pass
        elif "cancelled" in event_type or "canceled" in event_type:
            # ...existing code...
            pass

    # Acknowledge receipt
    return "", 200