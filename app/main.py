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
TEAM_MEMBERS = ["Yaswanth Sai Reddy"]
BOT_VERSION = "1.0.0"


app = FastAPI(
    title="VeraEdge — magicpin AI Challenge Bot",
    version=BOT_VERSION,
    description="Stateful deterministic merchant-engagement bot for the magicpin Vera challenge.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_load_optional_dataset() -> None:
    store.load_dataset_from_disk("dataset")


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
        "approach": "stateful 4-context composer with trigger-specific handlers, no external payload sharing, deterministic reply routing",
        "contact_email": "yaswanthsai1805@gmail.com",
        "version": BOT_VERSION,
        "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


@app.post("/v1/context")
async def context(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_json", "details": "Request body must be JSON"})

    if isinstance(body, dict) and "scope" in body and "context_id" in body and "payload" in body:
        scope = str(body.get("scope", ""))
        context_id = str(body.get("context_id", ""))
        version = body.get("version", 1)
        payload = body.get("payload") or {}
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_payload", "details": "payload must be an object"})

        accepted, reason, current = store.put(scope, context_id, int(version), payload)
        if accepted:
            return JSONResponse(status_code=200, content={
                "accepted": True,
                "ack_id": f"ack_{context_id}_v{version}",
                "stored_at": _now_iso(),
            })

        status = 409 if reason == "stale_version" else 400
        return JSONResponse(status_code=status, content={
            "accepted": False,
            "reason": reason,
            "current_version": current,
        })

    if isinstance(body, dict):
        accepted, reason, current, scope, cid = store.infer_and_put_raw(body)
        if accepted:
            return JSONResponse(status_code=200, content={
                "accepted": True,
                "ack_id": f"ack_{cid}_v1",
                "stored_at": _now_iso(),
                "scope": scope,
                "context_id": cid,
            })
        return JSONResponse(status_code=400 if reason != "stale_version" else 409, content={
            "accepted": False,
            "reason": reason,
            "current_version": current,
        })

    return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_body"})


@app.post("/v1/tick")
async def tick(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"actions": [], "reason": "invalid_json"})

    available = body.get("available_triggers", []) if isinstance(body, dict) else []
    if not isinstance(available, list):
        available = []

    actions: List[Dict[str, Any]] = []
    trigger_objects = []
    for tid in available:
        trigger = store.get("trigger", str(tid))
        if trigger:
            trigger_objects.append(trigger)
    trigger_objects.sort(key=lambda t: (-int(t.get("urgency", 0) or 0), str(t.get("id", ""))))

    for trigger in trigger_objects:
        if len(actions) >= 20:
            break

        merchant_id = trigger.get("merchant_id") or _payload_value(trigger, "merchant_id")
        customer_id = trigger.get("customer_id") or _payload_value(trigger, "customer_id")
        if not merchant_id:
            continue

        if store.is_opted_out(merchant_id):
            continue

        suppression_key = trigger.get("suppression_key") or f"{trigger.get('kind')}:{merchant_id}:{customer_id or 'merchant'}"
        if store.already_sent(suppression_key):
            continue

        merchant = store.get("merchant", merchant_id) or {}
        if not merchant:
            continue

        category_slug = merchant.get("category_slug") or _payload_value(trigger, "category")
        category = store.get("category", category_slug) or {}
        customer = store.get("customer", customer_id) if customer_id else None

        try:
            action = compose(category, merchant, trigger, customer)
        except Exception as exc:
            continue

        if not action.get("body"):
            continue

        store.mark_sent(action.get("suppression_key", ""), action.get("conversation_id", ""), action)
        actions.append(_public_action(action))

    return JSONResponse(status_code=200, content={"actions": actions})


@app.post("/v1/reply")
async def reply(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"action": "end", "rationale": "Invalid JSON; ending safely."})

    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"action": "end", "rationale": "Invalid request body; ending safely."})

    try:
        result = handle_reply(payload)
        return JSONResponse(status_code=200, content=result)
    except Exception as exc:
        return JSONResponse(status_code=200, content={
            "action": "end",
            "rationale": "Reply handler failed safely; ending conversation to avoid malformed output.",
        })


@app.post("/v1/teardown")
def teardown() -> Dict[str, Any]:
    store.reset()
    return {"status": "cleared", "at": _now_iso()}


def _public_action(action: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "conversation_id",
        "merchant_id",
        "customer_id",
        "send_as",
        "trigger_id",
        "template_name",
        "template_params",
        "body",
        "cta",
        "suppression_key",
        "rationale",
    }
    return {k: v for k, v in action.items() if k in allowed}


def _payload_value(trigger: Dict[str, Any], key: str) -> Optional[Any]:
    payload = trigger.get("payload", {})
    if isinstance(payload, dict):
        return payload.get(key)
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
