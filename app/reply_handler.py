from __future__ import annotations

import re
from typing import Any, Dict

from .store import store
from .utils import clean_text, salutation, shorten

AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"thanks for contacting",
    r"our team will respond",
    r"we will respond shortly",
    r"business hours",
    r"automatic reply",
    r"auto.?reply",
    r"away message",
    r"currently unavailable",
]

STOP_PATTERNS = [
    r"\bstop\b",
    r"\bunsubscribe\b",
    r"\bnot interested\b",
    r"don'?t message",
    r"\bremove\b",
    r"\bspam\b",
    r"\buseless\b",
]

COMMIT_PATTERNS = [
    r"\byes\b",
    r"\bok\b",
    r"\bokay\b",
    r"let'?s do",
    r"lets do",
    r"go ahead",
    r"send",
    r"draft",
    r"proceed",
    r"what'?s next",
    r"interested",
    r"start",
    r"do it",
    r"book",
]

PRICE_PATTERNS = [r"price", r"cost", r"charge", r"kitna", r"how much", r"amount"]
OFFTOPIC_PATTERNS = [r"\bgst\b", r"\btax\b", r"\bfile\b", r"\baccounting\b"]

AUTO_REPLY_COUNTS: Dict[str, int] = {}


def handle_reply(payload: Dict[str, Any]) -> Dict[str, Any]:
    conversation_id = str(payload.get("conversation_id") or "conv_unknown")
    merchant_id = payload.get("merchant_id")
    customer_id = payload.get("customer_id")
    from_role = str(payload.get("from_role") or "").lower()
    msg = str(payload.get("message") or "").strip()
    msg_norm = _norm(msg)

    conv = store.add_inbound_turn(conversation_id, msg, merchant_id, customer_id, from_role)
    merchant = store.get("merchant", str(merchant_id)) or {}
    is_customer = from_role == "customer" or bool(customer_id)

    if is_customer:
        if _matches(msg_norm, STOP_PATTERNS):
            return {
                "action": "end",
                "rationale": "Customer opted out; ending customer-facing flow without opting the merchant out.",
            }
        if _matches(msg_norm, AUTO_REPLY_PATTERNS):
            return _handle_auto_reply(conversation_id, merchant_id, customer_id, from_role, msg_norm)
        return _handle_customer_reply(conversation_id, payload, merchant, msg, msg_norm)

    if _matches(msg_norm, STOP_PATTERNS):
        store.mark_opt_out(str(merchant_id) if merchant_id else None)
        return {
            "action": "end",
            "rationale": "Merchant explicitly opted out or used hostile stop language; ending cleanly.",
        }

    if _matches(msg_norm, AUTO_REPLY_PATTERNS):
        return _handle_auto_reply(conversation_id, merchant_id, customer_id, from_role, msg_norm)

    if _matches(msg_norm, OFFTOPIC_PATTERNS):
        body = (
            "I can’t handle GST/tax filing here. I’ll stay with the Vera growth task: "
            "I can prepare the post/message/checklist from the last suggestion and keep it approval-only."
        )
        return _send(
            conversation_id,
            body,
            "binary_yes_no",
            "Off-topic request handled politely while keeping the conversation on the original task.",
        )

    if _matches(msg_norm, COMMIT_PATTERNS):
        return _handle_merchant_commit(conversation_id, conv, merchant, msg_norm)

    if _matches(msg_norm, PRICE_PATTERNS):
        return _handle_price_question(conversation_id, merchant)

    if any(x in msg_norm for x in ["later", "busy", "tomorrow", "baad", "free nahi"]):
        return {
            "action": "wait",
            "wait_seconds": 14400,
            "rationale": "Merchant asked for time; backing off for 4 hours.",
        }

    first = salutation(merchant.get("category_slug", ""), merchant) if merchant else "there"
    body = (
        f"Got it {first}. I’ll keep this to one approval step: one draft message, "
        "one Google post line, and why it fits your current context. Reply YES to use it, or STOP to close."
    )
    return _send(
        conversation_id,
        body,
        "binary_yes_no",
        "Ambiguous but engaged merchant reply; narrowed to a single approval step.",
    )


def _handle_auto_reply(
    conversation_id: str,
    merchant_id: Any,
    customer_id: Any,
    from_role: str,
    msg_norm: str,
) -> Dict[str, Any]:
    key_owner = str(customer_id or merchant_id or "unknown")
    msg_key = re.sub(r"[^a-z0-9]+", " ", msg_norm).strip()[:120]
    key = f"{from_role or 'unknown'}:{key_owner}:{msg_key}"

    AUTO_REPLY_COUNTS[key] = AUTO_REPLY_COUNTS.get(key, 0) + 1
    count = AUTO_REPLY_COUNTS[key]

    conv = store.get_conversation(conversation_id)
    conv["auto_reply_count"] = int(conv.get("auto_reply_count", 0) or 0) + 1

    if count == 1:
        body = (
            "Looks like a WhatsApp Business auto-reply 😊 "
            "When the owner sees this, just reply YES and I’ll continue with the draft/checklist."
        )
        return _send(
            conversation_id,
            body,
            "binary_yes_no",
            "Detected first auto-reply; sending one owner-visible prompt instead of continuing a normal flow.",
        )

    if count == 2:
        return {
            "action": "wait",
            "wait_seconds": 3600,
            "rationale": "Same WhatsApp Business auto-reply repeated; waiting instead of burning another turn.",
        }

    return {
        "action": "end",
        "rationale": "Repeated WhatsApp Business auto-reply detected across attempts; ending to avoid an auto-reply loop.",
    }


def _handle_customer_reply(
    conversation_id: str,
    payload: Dict[str, Any],
    merchant: Dict[str, Any],
    raw_msg: str,
    msg_norm: str,
) -> Dict[str, Any]:
    customer_id = payload.get("customer_id")
    customer = store.get("customer", str(customer_id)) if customer_id else None
    customer_name = ((customer or {}).get("identity") or {}).get("name") or "there"
    customer_name = str(customer_name).split("(")[0].strip() or "there"
    merchant_name = (merchant.get("identity") or {}).get("name") or "the clinic"
    slot_text = _extract_slot(raw_msg)

    if slot_text or _matches(msg_norm, COMMIT_PATTERNS):
        if slot_text:
            body = (
                f"Done {customer_name} — I’ve selected {slot_text} for you at {merchant_name}. "
                "The team will confirm shortly. Reply CHANGE for another slot, or STOP to opt out."
            )
        else:
            body = (
                f"Done {customer_name} — I’ll share this with {merchant_name}. "
                "Please send your preferred slot if you want us to book it, or STOP to opt out."
            )
        return _send(
            conversation_id,
            body,
            "none",
            "Customer slot/positive reply handled in customer voice, not merchant voice.",
        )

    if any(x in msg_norm for x in ["change", "other", "different", "another"]):
        body = f"Sure {customer_name}. Please send your preferred day/time, and {merchant_name} will try to match it."
        return _send(
            conversation_id,
            body,
            "open_ended",
            "Customer asked for a different slot; requesting only the missing preference.",
        )

    body = (
        f"Thanks {customer_name}. I’ll pass this to {merchant_name}. "
        "Reply with a preferred slot if you want a booking, or STOP to opt out."
    )
    return _send(conversation_id, body, "open_ended", "Generic customer reply kept customer-facing and concise.")


def _handle_merchant_commit(
    conversation_id: str,
    conv: Dict[str, Any],
    merchant: Dict[str, Any],
    msg_norm: str,
) -> Dict[str, Any]:
    template = str(conv.get("template_name") or "")
    first = salutation(merchant.get("category_slug", ""), merchant) if merchant else "there"

    if "regulation" in template or "compliance" in template:
        body = (
            f"Done {first}. For the old D-speed unit, next step is an audit checklist: "
            "1) confirm film/sensor type, 2) note current exposure setting, "
            "3) mark whether RVG/E-speed is available, 4) keep SOP proof ready before the deadline. "
            "I’ll format this as a 4-point reception checklist for approval."
        )
    elif "research" in template or "cde" in template:
        body = (
            f"Done {first}. I’ll prepare: 1 key finding, 1 practice action, "
            "and 1 patient-friendly WhatsApp draft. Nothing goes live without your approval."
        )
    elif "review_theme" in template:
        body = (
            f"Done {first}. I’ll make one public review reply and one internal team note "
            "so the same complaint does not repeat. You only need to approve/edit."
        )
    else:
        body = (
            f"Done {first}. I’ll prepare the approval bundle now: one WhatsApp line, "
            "one Google post line, and the reason it fits your current trigger. No extra questions."
        )

    return _send(conversation_id, body, "none", "Merchant committed; bot switches from qualification to action immediately.")


def _handle_price_question(conversation_id: str, merchant: Dict[str, Any]) -> Dict[str, Any]:
    active = [
        o.get("title")
        for o in merchant.get("offers", [])
        if str(o.get("status", "")).lower() == "active" and o.get("title")
    ]

    if active:
        body = (
            f"The live offer I can safely use is: {', '.join(active[:3])}. "
            "I’d lead with this service-at-price hook instead of a vague discount. Reply YES and I’ll draft it."
        )
    else:
        body = (
            "I don’t have a confirmed live price in the context, so I won’t invent one. "
            "Send the service price once and I’ll draft the campaign around it."
        )

    return _send(
        conversation_id,
        body,
        "binary_yes_no",
        "Price reply uses only known merchant offer data and avoids fabricated pricing.",
    )


def _extract_slot(message: str) -> str:
    patterns = [
        r"((?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+\d{1,2}\s+[a-z]{3,9},?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm))",
        r"(\d{1,2}\s+[a-z]{3,9},?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    ]

    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if match:
            return match.group(1).strip()

    return ""


def _send(conversation_id: str, body: str, cta: str, rationale: str) -> Dict[str, Any]:
    body = shorten(clean_text(body), 900)
    conv = store.get_conversation(conversation_id)

    if body in set(conv.get("last_bodies", [])):
        body = body + " I’ll keep this as the final follow-up."

    store.remember_bot_reply(conversation_id, body)
    return {"action": "send", "body": body, "cta": cta, "rationale": rationale}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)
