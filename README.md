# Doxium SaaS

AI-powered document management and Retrieval-Augmented Generation (RAG) system.

## Payments (Lemon Squeezy)

Required environment variables:

- `LEMON_SQUEEZY_API_KEY`
- `LEMON_SQUEEZY_WEBHOOK_SECRET`
- `LEMON_SQUEEZY_STORE_ID`
- `LEMON_SQUEEZY_VARIANT_ID`

Webhook endpoint:

- `POST /webhook` (alias: `POST /webhook/lemonsqueezy`)

Notes:

- Checkout creation is handled server-side and redirects users to a Lemon Squeezy hosted checkout.
- Webhooks confirm purchases/subscriptions and update `User.is_pilot` plus subscription fields.

Local webhook testing:

- Run `ngrok http 5000` (or your Flask port) and configure the public URL in Lemon Squeezy.
- Point the webhook at `https://<ngrok-id>.ngrok.io/webhook`.
