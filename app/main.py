from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .composer import compose
from .reply_handler import handle_reply
from .store import store

TEAM_NAME = "VeraEdge"
TEAM_MEMBERS = ["Yaswanth"]
CONTACT_EMAIL = "yaswanthsai1805@gmail.com"
BOT_VERSION = "1.1.0"

app = FastAPI(title="VeraEdge", version=BOT_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "service": "VeraEdge magicpin challenge bot",
        "status": "ok",
        "endpoints": ["/v1/healthz", "/v1/metadata", "/v1/context", "/v1/tick", "/v1/reply"],
    }


@app.get("/v1/healthz")
def healthz() -> Dict[str, Any]:
    return {
        "status": "ok",
        "uptime_seconds": int(datetime.now(timezone.utc).timestamp() - store.started_at),
        "contexts_loaded": store.counts(),
    }


@app.get("/v1/metadata")
def metadata() -> Dict[str, Any]:
    return {
        "team_name": TEAM_NAME,
        "team_members": TEAM_MEMBERS,
        "model": "deterministic-python-rule-engine",
        "approach": "stateful 4-context composer with trigger-specific handlers and customer/merchant reply routing",
        "contact_email": CONTACT_EMAIL,
        "version": BOT_VERSION,
        "submitted_at": _now_iso(),
    }


@app.post("/v1/context")
async def context(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_json"})

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_body"})

    if {"scope", "context_id", "payload"}.issubset(body.keys()):
        scope = str(body.get("scope") or "")
        context_id = str(body.get("context_id") or "")
        version = body.get("version", 1)
        payload = body.get("payload") or {}
        accepted, reason, current = store.put(scope, context_id, version, payload)
        status = 200 if accepted else 400
        content: Dict[str, Any] = {"accepted": accepted, "reason": reason} if not accepted else {
            "accepted": True,
            "ack_id": f"ack_{context_id}_v{version}",
            "stored_at": _now_iso(),
        }
        if not accepted:
            content["current_version"] = current
        return JSONResponse(status_code=status, content=content)

    accepted, reason, current, scope, cid = store.infer_and_put_raw(body)
    if accepted:
        return JSONResponse(status_code=200, content={"accepted": True, "ack_id": f"ack_{cid}_v1", "stored_at": _now_iso(), "scope": scope, "context_id": cid})
    return JSONResponse(status_code=400, content={"accepted": False, "reason": reason, "current_version": current})


@app.post("/v1/tick")
async def tick(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=200, content={"actions": []})

    available = body.get("available_triggers", []) if isinstance(body, dict) else []
    if not isinstance(available, list):
        available = []

    triggers: List[Dict[str, Any]] = []
    for item in available:
        if isinstance(item, dict):
            triggers.append(item)
        else:
            trigger = store.get("trigger", str(item))
            if trigger:
                triggers.append(trigger)
    triggers.sort(key=lambda t: (-int(t.get("urgency", 0) or 0), str(t.get("id") or "")))

    actions: List[Dict[str, Any]] = []
    for trigger in triggers:
        if len(actions) >= 20:
            break
        action = _action_for_trigger(trigger)
        if not action:
            continue
        store.mark_sent(str(action.get("suppression_key") or ""), str(action.get("conversation_id") or ""), action)
        actions.append(_public_action(action))

    return JSONResponse(status_code=200, content={"actions": actions})


@app.post("/v1/reply")
async def reply(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=200, content={"action": "end", "rationale": "Invalid JSON; ending safely."})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=200, content={"action": "end", "rationale": "Invalid request; ending safely."})
    return JSONResponse(status_code=200, content=handle_reply(payload))


@app.post("/v1/teardown")
def teardown() -> Dict[str, Any]:
    store.reset()
    return {"status": "cleared", "at": _now_iso()}


def _action_for_trigger(trigger: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    merchant_id = trigger.get("merchant_id") or _payload_value(trigger, "merchant_id")
    customer_id = trigger.get("customer_id") or _payload_value(trigger, "customer_id")
    if not merchant_id:
        return None
    if store.is_opted_out(str(merchant_id)):
        return None

    suppression_key = str(trigger.get("suppression_key") or f"{trigger.get('kind', 'trigger')}:{merchant_id}:{customer_id or 'merchant'}")
    if store.already_sent(suppression_key):
        return None

    merchant = store.get("merchant", str(merchant_id)) or _minimal_merchant(str(merchant_id), trigger)
    category_slug = merchant.get("category_slug") or _payload_value(trigger, "category") or ""
    category = store.get("category", str(category_slug)) or {"slug": category_slug, "offer_catalog": [], "digest": [], "voice": {}, "peer_stats": {}}
    customer = store.get("customer", str(customer_id)) if customer_id else None

    try:
        action = compose(category, merchant, trigger, customer)
    except Exception:
        action = _fallback_action(category, merchant, trigger, customer)

    if not action or not action.get("body"):
        action = _fallback_action(category, merchant, trigger, customer)
    action["suppression_key"] = action.get("suppression_key") or suppression_key
    return action


def _minimal_merchant(merchant_id: str, trigger: Dict[str, Any]) -> Dict[str, Any]:
    category_slug = _payload_value(trigger, "category") or "unknown"
    return {
        "merchant_id": merchant_id,
        "category_slug": category_slug,
        "identity": {"name": merchant_id, "owner_first_name": "there", "city": "", "locality": "", "languages": ["en"]},
        "subscription": {},
        "performance": {},
        "offers": [],
        "conversation_history": [],
        "customer_aggregate": {},
        "signals": [],
        "review_themes": [],
    }


def _fallback_action(category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merchant_id = merchant.get("merchant_id") or trigger.get("merchant_id") or "unknown_merchant"
    customer_id = (customer or {}).get("customer_id") or trigger.get("customer_id")
    trigger_id = trigger.get("id") or "unknown_trigger"
    kind = trigger.get("kind", "trigger")
    suppression_key = trigger.get("suppression_key") or f"{kind}:{merchant_id}:{customer_id or 'merchant'}"
    merchant_name = (merchant.get("identity") or {}).get("name") or "the business"
    owner = (merchant.get("identity") or {}).get("owner_first_name") or merchant_name

    if customer_id:
        customer_name = ((customer or {}).get("identity") or {}).get("name") or "there"
        body = f"Hi {str(customer_name).split('(')[0].strip()}, {merchant_name} here. We have a timely follow-up for you based on your last visit. Reply YES to continue, or STOP to opt out."
        send_as = "merchant_on_behalf"
        cta = "binary_yes_no"
    else:
        body = f"Hi {owner}, I found a timely {str(kind).replace('_', ' ')} update for {merchant_name}. Want me to prepare the exact draft/checklist for approval?"
        send_as = "vera"
        cta = "binary_yes_no"

    return {
        "conversation_id": f"conv_{merchant_id}_{customer_id or 'merchant'}_{trigger_id}",
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "send_as": send_as,
        "trigger_id": trigger_id,
        "template_name": f"vera_{kind}_fallback",
        "template_params": [body],
        "body": body,
        "cta": cta,
        "suppression_key": suppression_key,
        "rationale": "Fallback composed from available merchant, customer and trigger context so active triggers never return empty actions.",
    }


def _payload_value(trigger: Dict[str, Any], key: str) -> Optional[Any]:
    payload = trigger.get("payload")
    return payload.get(key) if isinstance(payload, dict) else None


def _public_action(action: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"conversation_id", "merchant_id", "customer_id", "send_as", "trigger_id", "template_name", "template_params", "body", "cta", "suppression_key", "rationale"}
    return {k: v for k, v in action.items() if k in allowed}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
