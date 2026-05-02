from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


def dig(data: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
        if cur is None:
            return default
    return cur


def safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def clean_text(text: str) -> str:
    """Remove URLs and excessive whitespace. Meta templates dislike raw URLs."""
    text = re.sub(r"https?://\S+|www\.\S+", "", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def pct(value: Any, signed: bool = False) -> str:
    try:
        v = float(value)
    except Exception:
        return ""
    if abs(v) <= 1.5:
        v *= 100
    sign = "+" if signed and v > 0 else ""
    return f"{sign}{v:.0f}%"


def ctr(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return ""


def rupee(value: Any) -> str:
    if value is None or value == "":
        return ""
    s = str(value)
    if s.startswith("₹"):
        return s
    return f"₹{s}"


def first_name_from_merchant(merchant: Dict[str, Any]) -> str:
    owner = dig(merchant, "identity.owner_first_name")
    if owner:
        owner = str(owner).replace("Dr.", "Dr. ").replace("Dr.  ", "Dr. ").strip()
        return owner

    name = dig(merchant, "identity.name", "there")
    tokens = re.findall(r"[A-Za-z]+", str(name))
    if not tokens:
        return "there"
    if tokens[0].lower() == "dr" and len(tokens) > 1:
        return tokens[1]
    return tokens[0]


def salutation(category_slug: str, merchant: Dict[str, Any]) -> str:
    first = first_name_from_merchant(merchant)
    if category_slug == "dentists":
        if first.lower().startswith("dr"):
            return first
        return f"Dr. {first}"
    if category_slug == "gyms":
        return f"Coach {first}" if first.lower() not in {"coach", "team"} else first
    return first


def merchant_short_name(merchant: Dict[str, Any]) -> str:
    return dig(merchant, "identity.name", "your business")


def locality_city(merchant: Dict[str, Any]) -> str:
    locality = dig(merchant, "identity.locality", "")
    city = dig(merchant, "identity.city", "")
    if locality and city:
        return f"{locality}, {city}"
    return locality or city or "your area"


def active_offers(merchant: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [o for o in merchant.get("offers", []) if str(o.get("status", "")).lower() == "active"]


def first_active_offer(merchant: Dict[str, Any], category: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    offers = active_offers(merchant)
    if offers:
        return offers[0]
    if category:
        catalog = category.get("offer_catalog", [])
        if catalog:
            return catalog[0]
    return None


def find_offer(merchant: Dict[str, Any], category: Dict[str, Any], keywords: Iterable[str]) -> Optional[Dict[str, Any]]:
    kws = [k.lower() for k in keywords]
    all_offers = active_offers(merchant) + category.get("offer_catalog", [])
    for offer in all_offers:
        title = str(offer.get("title", "")).lower()
        if any(k in title for k in kws):
            return offer
    return first_active_offer(merchant, category)


def lookup_digest(category: Dict[str, Any], *ids: Optional[str]) -> Optional[Dict[str, Any]]:
    wanted = {i for i in ids if i}
    for item in category.get("digest", []):
        if item.get("id") in wanted:
            return item
    wanted_text = " ".join(wanted).lower()
    if wanted_text:
        for item in category.get("digest", []):
            hay = " ".join(str(item.get(k, "")) for k in ("id", "title", "summary", "kind")).lower()
            if any(token and token in hay for token in re.split(r"[_:\-\s]+", wanted_text)):
                return item
    return None


def find_digest_by_kind_or_word(category: Dict[str, Any], *words: str) -> Optional[Dict[str, Any]]:
    lower_words = [w.lower() for w in words if w]
    for item in category.get("digest", []):
        hay = " ".join(str(item.get(k, "")) for k in ("id", "kind", "title", "summary", "actionable")).lower()
        if all(w in hay for w in lower_words):
            return item
    for item in category.get("digest", []):
        hay = " ".join(str(item.get(k, "")) for k in ("id", "kind", "title", "summary", "actionable")).lower()
        if any(w in hay for w in lower_words):
            return item
    return None


def customer_name(customer: Optional[Dict[str, Any]]) -> str:
    if not customer:
        return "there"
    raw = dig(customer, "identity.name", "there")
    return str(raw).split("(")[0].strip() or "there"


def language_pref(customer: Optional[Dict[str, Any]], merchant: Optional[Dict[str, Any]] = None) -> str:
    if customer:
        return str(dig(customer, "identity.language_pref", "")).lower()
    if merchant:
        langs = dig(merchant, "identity.languages", [])
        if isinstance(langs, list) and "hi" in langs:
            return "hi-en mix"
    return "english"


def slot_labels(trigger: Dict[str, Any]) -> List[str]:
    slots = dig(trigger, "payload.available_slots", []) or dig(trigger, "payload.next_session_options", [])
    labels = []
    if isinstance(slots, list):
        for s in slots:
            if isinstance(s, dict):
                labels.append(s.get("label") or s.get("iso") or "")
            else:
                labels.append(str(s))
    return [x for x in labels if x]


def iso_to_short_date(value: Any) -> str:
    if not value:
        return ""
    s = str(value)
    try:
        clean = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.strftime("%d %b %Y")
    except Exception:
        return s[:10]


def metric_snapshot(merchant: Dict[str, Any]) -> str:
    perf = merchant.get("performance", {})
    parts = []
    if perf.get("views") is not None:
        parts.append(f"{perf.get('views')} views")
    if perf.get("calls") is not None:
        parts.append(f"{perf.get('calls')} calls")
    if perf.get("directions") is not None:
        parts.append(f"{perf.get('directions')} direction requests")
    if perf.get("ctr") is not None:
        parts.append(f"{ctr(perf.get('ctr'))} CTR")
    return ", ".join(parts)


def peer_ctr(category: Dict[str, Any]) -> str:
    return ctr(dig(category, "peer_stats.avg_ctr"))


def shorten(text: str, limit: int = 720) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].strip()
    return cut + "…"


def conversation_id_for(merchant_id: str, trigger_id: str, customer_id: Optional[str] = None) -> str:
    raw = f"conv_{merchant_id}_{customer_id or 'merchant'}_{trigger_id}"
    raw = re.sub(r"[^A-Za-z0-9_]+", "_", raw)
    return raw[:180]


def template_name_for(kind: str, scope: str) -> str:
    return f"vera_{scope}_{kind}_v1".replace("__", "_")
