from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .store import store
from .utils import clean_text, dig, merchant_short_name, salutation, shorten


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
    r"\bdon'?t message\b",
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
]

PRICE_PATTERNS = [
    r"price",
    r"cost",
    r"charge",
    r"kitna",
    r"how much",
    r"amount",
]

OFFTOPIC_PATTERNS = [
    r"\bgst\b",
    r"\btax\b",
    r"\bfile\b",
    r"\baccounting\b",
]


def handle_reply(payload: Dict[str, Any]) -> Dict[str, Any]:
    conversation_id = payload.get("conversation_id") or "conv_unknown"
    merchant_id = payload.get("merchant_id")
    customer_id = payload.get("customer_id")
    msg = str(payload.get("message", "") or "").strip()
    msg_norm = _norm(msg)

    conv = store.add_inbound_turn(conversation_id, msg, merchant_id, customer_id)
    merchant = store.get("merchant", merchant_id) or {}
    category = store.get("category", merchant.get("category_slug")) or {}
    from_role = str(payload.get("from_role", "") or "").lower()
    is_customer = from_role == "customer" or bool(customer_id)

    if is_customer and not _matches(msg_norm, STOP_PATTERNS) and not _matches(msg_norm, AUTO_REPLY_PATTERNS):
        return _handle_customer_reply(conversation_id, payload, merchant, msg_norm)

    if _matches(msg_norm, STOP_PATTERNS):
        store.mark_opt_out(merchant_id)
        return {
            "action": "end",
            "rationale": "Merchant/customer explicitly opted out or used hostile stop language; ending without another promotional message.",
        }

    if _matches(msg_norm, AUTO_REPLY_PATTERNS):
        return _handle_auto_reply(conversation_id, msg_norm)

    if _matches(msg_norm, OFFTOPIC_PATTERNS):
        body = (
            "Sorry, I can’t help file GST/tax work here. I’ll stay with the growth task we were discussing — "
            "I can draft the post/message/checklist from the last Vera suggestion, and you can review it before anything goes live."
        )
        return _send(conversation_id, body, "binary_yes_no", "Off-topic request handled politely; bot stays on mission and does not claim tax capability.")

    if _matches(msg_norm, COMMIT_PATTERNS):
        return _handle_commit(conversation_id, conv, merchant, category, msg_norm)

    if _matches(msg_norm, PRICE_PATTERNS):
        return _handle_price_question(conversation_id, merchant, category)

    if "later" in msg_norm or "busy" in msg_norm or "tomorrow" in msg_norm:
        return {
            "action": "wait",
            "wait_seconds": 14400,
            "rationale": "Merchant asked for time or indicated they are busy; backing off for 4 hours.",
        }

    first = salutation(merchant.get("category_slug", ""), merchant) if merchant else "there"
    body = (
        f"Got it {first}. I’ll keep this practical: I’ll prepare one draft message + one Google post angle from the context, "
        "and you only need to approve/edit it. Reply YES to use it, or STOP to close this."
    )
    return _send(conversation_id, body, "binary_yes_no", "Ambiguous but engaged reply; bot narrows to one approval step.")


def _handle_auto_reply(conversation_id: str, msg_norm: str) -> Dict[str, Any]:
    conv = store.get_conversation(conversation_id)
    conv["auto_reply_count"] = conv.get("auto_reply_count", 0) + 1
    count = conv["auto_reply_count"]

    if count == 1:
        return {
            "action": "wait",
            "wait_seconds": 3600,
            "rationale": "Detected WhatsApp Business auto-reply; waiting instead of burning another owner-facing turn.",
        }
    return {
        "action": "end",
        "rationale": "Repeated auto-reply in this conversation; ending to avoid a loop.",
    }


def _handle_customer_reply(conversation_id: str, payload: Dict[str, Any], merchant: Dict[str, Any], msg_norm: str) -> Dict[str, Any]:
    customer_id = payload.get("customer_id")
    customer = store.get("customer", customer_id) or {}
    identity = customer.get("identity") or {}
    name = str(identity.get("name") or "there").split("(")[0].strip()
    clinic = (merchant.get("identity") or {}).get("name") or "the clinic"

    slot_text = _extract_slot(str(payload.get("message", "")))
    if _matches(msg_norm, COMMIT_PATTERNS) or slot_text:
        if slot_text:
            body = f"Done {name} — I’ve selected {slot_text} for you at {clinic}. The team will confirm the booking shortly. Reply CHANGE if you want another slot, or STOP to opt out."
        else:
            body = f"Done {name} — I’ll share this with {clinic} and keep the next step ready. Reply with your preferred time if you want a booking slot, or STOP to opt out."
        return _send(conversation_id, body, "none", "Customer reply handled as customer-facing booking/confirmation flow.")

    if "change" in msg_norm or "other" in msg_norm or "different" in msg_norm:
        body = f"Sure {name}. Please send your preferred day/time, and {clinic} will try to match it."
        return _send(conversation_id, body, "open_ended", "Customer wants an alternate slot; asking only for the missing slot preference.")

    body = f"Thanks {name}. I’ll pass this to {clinic}. Reply with a preferred slot if you want us to book, or STOP to opt out."
    return _send(conversation_id, body, "open_ended", "Generic customer reply routed with customer voice, not merchant voice.")


def _extract_slot(message: str) -> str:
    m = re.search(r"(?:for\s+)?((?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+\d{1,2}\s+[a-z]{3,9},?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm))", message, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(\d{1,2}\s+[a-z]{3,9},?\s+\d{1,2}(?::\d{2})?\s*(?:am|pm))", message, re.I)
    if m:
        return m.group(1).strip()
    return ""


def _handle_commit(conversation_id: str, conv: Dict[str, Any], merchant: Dict[str, Any], category: Dict[str, Any], msg_norm: str) -> Dict[str, Any]:
    template = str(conv.get("template_name", ""))
    cat = merchant.get("category_slug", "")
    first = salutation(cat, merchant) if merchant else "there"

    if "research" in template or "cde" in template:
        body = (
            f"Done {first}. Sending the short abstract-style summary now: 1 key point, 1 merchant action, 1 patient-facing draft. "
            "Draft: “New reminder from our clinic: shorter recall intervals can matter for high-risk patients. If it has been a while since your cleaning, reply here and we’ll suggest a slot.” "
            "Next: I’ll convert this into a Google post for approval."
        )
    elif "review_theme" in template:
        body = (
            f"Done {first}. Here is the review reply draft: “Sorry about the delay — we’re tightening prep/slot timing so this does not repeat. Please message us once and we’ll make it right.” "
            "Next: brief the team with the same wording today."
        )
    elif "active_planning" in template:
        body = (
            f"Done {first}. I’m moving this to action: campaign draft, customer WhatsApp, and Google post are ready as one approval bundle. "
            "Next step: confirm the offer/service name and I’ll keep the copy live-ready."
        )
    elif "renewal" in template or "winback" in template:
        body = (
            f"Done {first}. Here is the renewal/winback summary: current profile data + one service-at-price campaign + one customer recall/winback message. "
            "Next: approve the draft; no auto-charge or campaign launch without your confirmation."
        )
    else:
        body = (
            f"Done {first}. Here is the next step: I’ll prepare the draft now and keep it approval-only. "
            "You’ll get one Google post line, one WhatsApp line, and the reason behind it — no extra questions."
        )

    return _send(conversation_id, body, "none", "Merchant committed; bot switches from qualification to action immediately.")


def _handle_price_question(conversation_id: str, merchant: Dict[str, Any], category: Dict[str, Any]) -> Dict[str, Any]:
    offers = [o for o in merchant.get("offers", []) if str(o.get("status", "")).lower() == "active"]
    if not offers:
        offers = category.get("offer_catalog", [])[:2]

    if offers:
        offer_text = "; ".join(o.get("title", "") for o in offers[:3] if o.get("title"))
        body = f"Current usable offer options: {offer_text}. I’d lead with the clearest service-at-price offer, not a vague discount. Reply YES and I’ll draft the message around it."
    else:
        body = "I don’t have a confirmed live price in the context, so I won’t invent one. Reply with the service price and I’ll draft the campaign around it."

    return _send(conversation_id, body, "binary_yes_no", "Price question answered only with known offer/catalog data; no fabricated pricing.")


def _send(conversation_id: str, body: str, cta: str, rationale: str) -> Dict[str, Any]:
    body = shorten(clean_text(body), 900)
    conv = store.get_conversation(conversation_id)
    previous = set(conv.get("last_bodies", []))
    if body in previous:
        body = body + " I’ll keep this as the final follow-up."
    store.remember_bot_reply(conversation_id, body)
    return {
        "action": "send",
        "body": body,
        "cta": cta,
        "rationale": rationale,
    }


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text) for p in patterns)
