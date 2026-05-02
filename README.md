# VeraEdge — magicpin AI Challenge Bot

VeraEdge is a stateful FastAPI bot built for the magicpin AI Challenge.

It receives category, merchant, customer, and trigger context from the judge, then decides whether to send a WhatsApp-style message. The response includes the message body, CTA, send identity, suppression key, and rationale.

## Tech Stack

- Python
- FastAPI
- Uvicorn
- Pydantic
- In-memory state store

The deployed bot does not require any external LLM API key.

## Required API Endpoints

```txt
GET  /v1/healthz
GET  /v1/metadata
POST /v1/context
POST /v1/tick
POST /v1/reply
```

## Approach

VeraEdge uses a deterministic rule-based composer instead of generic LLM generation.

The bot uses the pushed context to create specific, merchant-aware messages. It reads real values such as merchant name, locality, active offers, performance metrics, customer state, trigger payload, and category voice.

This keeps responses stable, avoids hallucinated facts, and makes the messages more relevant to the merchant or customer.

## Key Features

- Stateful context storage
- Idempotent context updates
- Trigger-specific message composition
- Merchant-facing and customer-facing messages
- Suppression tracking
- Auto-reply detection
- Positive intent routing
- Stop/hostile reply handling
- Pricing and busy/later reply handling
- Category-aware tone and CTA design

## Supported Trigger Types

- research_digest
- regulation_change
- recall_due
- perf_dip
- renewal_due
- festival_upcoming
- wedding_package_followup
- curious_ask_due
- winback_eligible
- ipl_match_today
- review_theme_emerged
- milestone_reached
- active_planning_intent
- seasonal_perf_dip
- customer_lapsed_soft
- customer_lapsed_hard
- appointment_tomorrow
- chronic_refill_due
- supply_alert
- category_seasonal
- gbp_unverified
- competitor_opened
- perf_spike
- dormant_with_vera

## Local Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Check health:

```bash
curl http://localhost:8080/v1/healthz
```

Check metadata:

```bash
curl http://localhost:8080/v1/metadata
```

## Deployment

The app can be deployed on Render or any Python web service platform.

Recommended Render settings:

```txt
Build Command:
pip install -r requirements.txt

Start Command:
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
