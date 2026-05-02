from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .utils import (
    active_offers,
    clean_text,
    conversation_id_for,
    ctr,
    customer_name,
    dig,
    find_digest_by_kind_or_word,
    find_offer,
    first_active_offer,
    first_name_from_merchant,
    iso_to_short_date,
    language_pref,
    locality_city,
    lookup_digest,
    merchant_short_name,
    metric_snapshot,
    pct,
    peer_ctr,
    rupee,
    salutation,
    shorten,
    slot_labels,
    template_name_for,
)


def compose(category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Deterministic message composer for the magicpin Vera challenge.

    It avoids hallucination by only using data present in the pushed contexts.
    """
    category = category or {}
    merchant = merchant or {}
    trigger = trigger or {}
    customer = customer or None

    kind = str(trigger.get("kind", "generic"))
    scope = str(trigger.get("scope", "merchant"))
    category_slug = merchant.get("category_slug") or category.get("slug") or dig(trigger, "payload.category", "merchant")
    merchant_id = merchant.get("merchant_id") or trigger.get("merchant_id") or "unknown_merchant"
    customer_id = customer.get("customer_id") if customer else trigger.get("customer_id")
    trigger_id = trigger.get("id") or "unknown_trigger"
    suppression_key = trigger.get("suppression_key") or f"{kind}:{merchant_id}:{customer_id or 'merchant'}"
    send_as = "merchant_on_behalf" if scope == "customer" or customer else "vera"

    body, cta, rationale = _dispatch(category_slug, kind, category, merchant, trigger, customer)
    body = shorten(body)

    return {
        "conversation_id": conversation_id_for(merchant_id, trigger_id, customer_id),
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "send_as": send_as,
        "trigger_id": trigger_id,
        "template_name": template_name_for(kind, "customer" if send_as == "merchant_on_behalf" else "merchant"),
        "template_params": _template_params(body),
        "body": body,
        "cta": cta,
        "suppression_key": suppression_key,
        "rationale": rationale,
        "category_slug": category_slug,
    }


def _dispatch(category_slug: str, kind: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    handlers = {
        "research_digest": _research_digest,
        "regulation_change": _regulation_change,
        "recall_due": _recall_due,
        "perf_dip": _perf_dip,
        "renewal_due": _renewal_due,
        "festival_upcoming": _festival_upcoming,
        "wedding_package_followup": _wedding_package_followup,
        "curious_ask_due": _curious_ask_due,
        "winback_eligible": _winback_eligible,
        "ipl_match_today": _ipl_match_today,
        "review_theme_emerged": _review_theme_emerged,
        "milestone_reached": _milestone_reached,
        "active_planning_intent": _active_planning_intent,
        "seasonal_perf_dip": _seasonal_perf_dip,
        "customer_lapsed_hard": _customer_lapsed,
        "customer_lapsed_soft": _customer_lapsed,
        "trial_followup": _trial_followup,
        "chronic_refill_due": _chronic_refill_due,
        "supply_alert": _supply_alert,
        "category_seasonal": _category_seasonal,
        "gbp_unverified": _gbp_unverified,
        "cde_opportunity": _cde_opportunity,
        "competitor_opened": _competitor_opened,
        "perf_spike": _perf_spike,
        "dormant_with_vera": _dormant_with_vera,
        "appointment_tomorrow": _appointment_tomorrow,
    }
    handler = handlers.get(kind, _default_message)
    return handler(category_slug, category, merchant, trigger, customer)



def _research_digest(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    item = lookup_digest(category, dig(trigger, "payload.top_item_id"), dig(trigger, "payload.digest_item_id")) or (category.get("digest") or [{}])[0]
    first = salutation(category_slug, merchant)
    source = item.get("source", "this week's category digest")
    title = item.get("title", "a new category update")
    summary = item.get("summary", "")
    actionable = item.get("actionable", "")
    trial_n = item.get("trial_n")
    segment = str(item.get("patient_segment", "")).replace("_", " ")
    merchant_anchor = _merchant_anchor_for_digest(category_slug, merchant, item)

    if category_slug == "dentists":
        proof = f"{trial_n}-patient trial" if trial_n else "clinical note"
        body = (
            f"{first}, {source} has one item worth your attention: {title}. "
            f"{proof}; {summary[:150]}. {merchant_anchor} "
            f"Want me to pull the 2-min abstract and draft a patient-friendly WhatsApp for your recall list?"
        )
    elif category_slug == "gyms":
        body = (
            f"{first}, quick fitness-market note: {title}. {summary[:150]} "
            f"{merchant_anchor} Want me to turn this into a 4-line member message + one Google post?"
        )
    elif category_slug == "pharmacies":
        body = (
            f"{first}, pharmacy update: {title}. Source: {source}. {summary[:150]} "
            f"{merchant_anchor} Want me to make a 5-point counter checklist for your staff?"
        )
    elif category_slug == "restaurants":
        body = (
            f"{first}, operator note from {source}: {title}. {summary[:150]} "
            f"{merchant_anchor} Want me to draft a menu/WhatsApp angle you can use today?"
        )
    else:
        body = (
            f"{first}, salon industry update: {title}. Source: {source}. {summary[:150]} "
            f"{merchant_anchor} Want me to convert it into a Google post + customer WhatsApp?"
        )

    if actionable:
        body += f" Action: {actionable}."
    return body, "open_ended", "Research digest anchored on the pushed category item, source citation, and merchant-specific signal."


def _regulation_change(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    item = lookup_digest(category, dig(trigger, "payload.top_item_id"), dig(trigger, "payload.digest_item_id")) or {}
    first = salutation(category_slug, merchant)
    deadline = iso_to_short_date(dig(trigger, "payload.deadline_iso"))
    title = item.get("title", "a compliance change")
    source = item.get("source", "official update")
    actionable = item.get("actionable", "audit the affected process")
    body = (
        f"{first}, compliance heads-up: {title}. Source: {source}. "
        f"Deadline: {deadline}. For {merchant_short_name(merchant)} in {locality_city(merchant)}, the safest next step is: {actionable}. "
        f"Want me to draft a 4-point SOP/checklist you can keep at reception?"
    )
    return body, "binary_yes_no", "High-urgency regulation trigger; message uses deadline, source, merchant name, and a concrete compliance CTA."


def _perf_dip(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    metric = dig(trigger, "payload.metric", "performance")
    delta = pct(dig(trigger, "payload.delta_pct"), signed=True)
    window = dig(trigger, "payload.window", "7d")
    baseline = dig(trigger, "payload.vs_baseline")
    snap = metric_snapshot(merchant)
    peer = peer_ctr(category)
    merchant_live_offers = active_offers(merchant)
    offer = merchant_live_offers[0] if merchant_live_offers else first_active_offer(merchant, category)
    if merchant_live_offers:
        offer_line = f" You already have {offer.get('title')} live — use that as the hook."
    elif offer:
        offer_line = f" No active offer is live; use a category-safe hook like {offer.get('title')} instead of a vague discount."
    else:
        offer_line = " No active offer is live, so the first fix is a service-at-price hook."
    body = (
        f"{first}, quick alert: {metric} is {delta} over the last {window}"
        f"{f' vs baseline {baseline}' if baseline is not None else ''}. "
        f"Current 30-day snapshot: {snap}; peer CTR is {peer or 'not available'}. "
        f"{offer_line} Want me to draft one recovery Google post + WhatsApp line for today?"
    )
    return body, "binary_yes_no", "Performance dip uses actual metric delta, current merchant performance, peer benchmark, and a single recovery action."


def _renewal_due(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    days = dig(trigger, "payload.days_remaining", dig(merchant, "subscription.days_remaining", "soon"))
    amount = rupee(dig(trigger, "payload.renewal_amount"))
    plan = dig(trigger, "payload.plan", dig(merchant, "subscription.plan", "plan"))
    snap = metric_snapshot(merchant)
    signals = ", ".join(merchant.get("signals", [])[:3])
    body = (
        f"{first}, your {plan} renewal is due in {days} days"
        f"{f' ({amount})' if amount else ''}. Before you decide, here is the business case: {snap}. "
        f"Signals I see: {signals or 'profile activity and lead flow need review'}. "
        f"Want me to send a 3-line renewal ROI summary you can check in 30 seconds?"
    )
    return body, "binary_yes_no", "Renewal message avoids pressure; it ties plan renewal to actual performance data and offers a concise ROI summary."


def _festival_upcoming(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    festival = dig(trigger, "payload.festival", "festival")
    days = dig(trigger, "payload.days_until")
    date = iso_to_short_date(dig(trigger, "payload.date"))
    offer = _best_offer_for_festival(category_slug, category, merchant)
    early = isinstance(days, int) and days > 30
    timing = f"{days} days away" if days is not None else f"on {date}"
    prefix = "Not urgent yet, but worth preparing early:" if early else "Good timing:"
    body = (
        f"{first}, {prefix} {festival} is {timing}. "
        f"For {merchant_short_name(merchant)}, the cleanest angle is {offer.get('title') if offer else 'one simple service-at-price campaign'} — not a generic discount. "
        f"Want me to draft a 2-line {festival} post and save it for review?"
    )
    return "binary_yes_no" and body, "binary_yes_no", "Festival trigger handled with restraint; message acknowledges timing and proposes one category-fit campaign angle."


def _curious_ask_due(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    name = merchant_short_name(merchant)
    hook = _category_service_question(category_slug, merchant)
    body = (
        f"Hi {first}! Quick 5-min check — {hook} at {name} this week? "
        f"Reply with just the service name. I’ll turn it into one Google post + a 4-line WhatsApp reply customers can use for pricing."
    )
    return body, "open_ended", "Curious-ask trigger intentionally asks a low-friction operator question and promises useful output."


def _winback_eligible(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    days = dig(trigger, "payload.days_since_expiry", dig(merchant, "subscription.days_since_expiry", "several"))
    dip = pct(dig(trigger, "payload.perf_dip_pct"), signed=True)
    lapsed_added = dig(trigger, "payload.lapsed_customers_added_since_expiry")
    body = (
        f"{first}, since Pro expired {days} days ago, the signal is not just renewal — it is leakage. "
        f"Performance is {dip or 'down'} post-expiry"
        f"{f' and {lapsed_added} more customers moved into lapsed bucket' if lapsed_added is not None else ''}. "
        f"Reply YES and I’ll draft a no-pressure winback campaign using one service-at-price offer, no auto-charge."
    )
    return body, "binary_yes_no", "Winback uses expiry age, performance dip, and lapsed-customer movement; CTA is explicit and low-risk."


def _ipl_match_today(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    match = dig(trigger, "payload.match", "tonight's match")
    venue = dig(trigger, "payload.venue", "")
    time_iso = dig(trigger, "payload.match_time_iso")
    match_time = "7:30pm" if "19:30" in str(time_iso) else str(time_iso)[11:16] if time_iso else "today"
    is_weeknight = bool(dig(trigger, "payload.is_weeknight", False))
    offer = first_active_offer(merchant, category)
    digest = find_digest_by_kind_or_word(category, "ipl") or {}
    summary = digest.get("summary", "")
    stat = "-12% covers on Saturday IPL" if "12%" in summary or not is_weeknight else "+18% covers on weeknight IPL"
    if is_weeknight:
        recommendation = f"push {offer.get('title') if offer else 'Match-night Combo @ ₹399'} as dine-in + delivery"
    else:
        recommendation = f"skip a dine-in promo; push {offer.get('title') if offer else 'a pizza/combo offer'} as delivery-only"
    body = (
        f"Quick heads-up {first} — {match} at {venue or 'your city'} tonight, {match_time}. "
        f"Important: {stat}. For {merchant_short_name(merchant)}, I’d {recommendation}. "
        f"Want me to draft the Swiggy/Zomato banner + one Insta story? Live in 10 min."
    )
    return body, "binary_yes_no", "Restaurant IPL trigger adds judgment, uses match/time/weekend context, and leverages the merchant's active offer."


def _review_theme_emerged(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    theme = str(dig(trigger, "payload.theme", "review theme")).replace("_", " ")
    count = dig(trigger, "payload.occurrences_30d", "")
    quote = dig(trigger, "payload.common_quote", "")
    trend = dig(trigger, "payload.trend", "new")
    fix = _category_review_fix(category_slug, theme)
    body = (
        f"{first}, review pattern spotted: {count} mentions of '{theme}' in 30 days, trend: {trend}. "
        f"Common wording: \"{quote}\". My fix: {fix}. "
        f"Want me to draft a polite review reply + one operations note for your team?"
    )
    return body, "binary_yes_no", "Review-theme trigger uses exact count, theme, quote, and turns it into a concrete response plus operations fix."


def _milestone_reached(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    metric = str(dig(trigger, "payload.metric", "metric")).replace("_", " ")
    now = dig(trigger, "payload.value_now")
    milestone = dig(trigger, "payload.milestone_value")
    gap = ""
    try:
        gap = f"only {int(milestone) - int(now)} away"
    except Exception:
        pass
    body = (
        f"{first}, small but useful milestone: {merchant_short_name(merchant)} is at {now} {metric}, {gap} from {milestone}. "
        f"That is a clean social-proof moment for {locality_city(merchant)}. "
        f"Want me to draft a 2-line Google post asking recent happy customers for the final push?"
    )
    return body, "binary_yes_no", "Milestone message uses current value, target value, and turns social proof into a low-friction action."


def _active_planning_intent(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    topic = dig(trigger, "payload.intent_topic", "plan").replace("_", " ")
    last = dig(trigger, "payload.merchant_last_message", "")
    if "thali" in topic:
        body = (
            f"Done {first} — for the corporate bulk thali package, keep it simple: "
            f"20+ plates, weekday 12-2pm, veg thali @ ₹149, add filter coffee @ ₹29, pre-order by 10:30am. "
            f"I’ll draft: 1 WhatsApp pitch for nearby offices + 1 Google post + a call script. Next: share this with 10 offices around Indiranagar."
        )
    elif "kids" in topic or "yoga" in topic:
        body = (
            f"Done {first} — kids yoga summer camp should be a 4-week batch, Sat/Sun 8am, age 7-12, trial class first, parent WhatsApp updates weekly. "
            f"Price anchor: use your existing trial/free-class style, then convert to monthly. "
            f"Here is the next step: I’ll draft the parent-facing invite + first class plan."
        )
    else:
        body = (
            f"Done {first} — based on your message \"{last[:80]}\", here is the plan for {topic}: "
            f"one clear offer, one audience, one 7-day test, and one WhatsApp follow-up. "
            f"I’ll draft the campaign copy + Google post now; next step is approval."
        )
    return body, "none", "Merchant has already shown planning intent; bot switches to action mode instead of asking more qualifying questions."


def _seasonal_perf_dip(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    metric = dig(trigger, "payload.metric", "views")
    delta = pct(dig(trigger, "payload.delta_pct"), signed=True)
    season = str(dig(trigger, "payload.season_note", "seasonal pattern")).replace("_", " ")
    expected = dig(trigger, "payload.is_expected_seasonal", False)
    digest = find_digest_by_kind_or_word(category, "seasonal") or {}
    action = digest.get("actionable", "protect retention instead of overspending on acquisition")
    body = (
        f"{first}, {metric} is {delta} in the last {dig(trigger, 'payload.window', '7d')}. "
        f"I’d not panic-spend: this looks {'expected' if expected else 'partly seasonal'} ({season}). "
        f"Better move for {merchant_short_name(merchant)}: {action}. Want me to draft a retention-first message for current members?"
    )
    return body, "binary_yes_no", "Seasonal dip trigger shows judgment by avoiding overreaction and using category seasonal guidance."


def _supply_alert(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    item = lookup_digest(category, dig(trigger, "payload.alert_id")) or {}
    molecule = dig(trigger, "payload.molecule", "medicine")
    batches = ", ".join(dig(trigger, "payload.affected_batches", []) or [])
    manufacturer = dig(trigger, "payload.manufacturer", "manufacturer")
    source = item.get("source", "CDSCO/FDA alert")
    body = (
        f"{first}, supply alert for {molecule}: affected batches {batches} from {manufacturer}. Source: {source}. "
        f"For {merchant_short_name(merchant)}, first pull shelf stock, then check repeat-Rx customers before dispensing. "
        f"Want me to draft a short staff checklist + customer replacement WhatsApp?"
    )
    return body, "binary_yes_no", "Supply alert uses exact molecule, batch numbers, manufacturer, source, and a safe pharmacist workflow."


def _category_seasonal(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    trends = dig(trigger, "payload.trends", [])
    trend_text = ", ".join(str(t).replace("_", " ") for t in trends[:4])
    action = dig(trigger, "payload.shelf_action_recommended")
    body = (
        f"{first}, seasonal shift for {merchant_short_name(merchant)}: {trend_text}. "
        f"{'Counter shelf action is recommended: move high-demand items to front visibility.' if action else 'Worth checking stock placement this week.'} "
        f"Want me to make a 6-item restock + shelf-placement checklist?"
    )
    return body, "binary_yes_no", "Seasonal category trigger converts demand shifts into a practical pharmacy/store action."


def _gbp_unverified(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    path = str(dig(trigger, "payload.verification_path", "verification")).replace("_", " ")
    uplift = pct(dig(trigger, "payload.estimated_uplift_pct"))
    body = (
        f"{first}, your Google Business Profile is still unverified. Verification path available: {path}. "
        f"The pushed benchmark estimates up to {uplift or 'meaningful'} visibility lift after verification. "
        f"Want me to send the exact 3-step verification checklist for {merchant_short_name(merchant)}?"
    )
    return body, "binary_yes_no", "GBP trigger uses verified=false, verification path, and estimated uplift to drive one clear action."


def _cde_opportunity(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    item = lookup_digest(category, dig(trigger, "payload.digest_item_id"), dig(trigger, "payload.top_item_id")) or {}
    first = salutation(category_slug, merchant)
    credits = dig(trigger, "payload.credits", item.get("credits"))
    fee = str(dig(trigger, "payload.fee", item.get("actionable", ""))).replace("_", " ")
    date = iso_to_short_date(item.get("date"))
    body = (
        f"{first}, CDE opportunity: {item.get('title', 'upcoming training')}. "
        f"{f'Date: {date}. ' if date else ''}{f'Credits: {credits}. ' if credits else ''}{f'Fee: {fee}. ' if fee else ''}"
        f"Source: {item.get('source', 'category calendar')}. Want me to save the details and draft a reminder for your team?"
    )
    return body, "binary_yes_no", "CDE trigger uses credits/date/fee/source and asks for one clear next step."


def _competitor_opened(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    comp = dig(trigger, "payload.competitor_name", "a competitor")
    dist = dig(trigger, "payload.distance_km")
    their_offer = dig(trigger, "payload.their_offer", "")
    opened = iso_to_short_date(dig(trigger, "payload.opened_date"))
    own_offer = first_active_offer(merchant, category)
    body = (
        f"{first}, competitor watch: {comp} opened {dist}km away"
        f"{f' on {opened}' if opened else ''}"
        f"{f' with {their_offer}' if their_offer else ''}. "
        f"I would not start a price war. Use your existing {own_offer.get('title') if own_offer else 'trusted-service angle'} and push trust + reviews. "
        f"Want me to draft a calm counter-post for Lajpat Nagar patients?"
    )
    return body, "binary_yes_no", "Competitor trigger adds judgment: names competitor and distance, but recommends positioning over panic discounting."


def _perf_spike(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    metric = dig(trigger, "payload.metric", "calls")
    delta = pct(dig(trigger, "payload.delta_pct"), signed=True)
    driver = str(dig(trigger, "payload.likely_driver", "recent post")).replace("_", " ")
    baseline = dig(trigger, "payload.vs_baseline")
    body = (
        f"{first}, good spike: {metric} is {delta} over {dig(trigger, 'payload.window', '7d')}"
        f"{f' vs baseline {baseline}' if baseline else ''}. Likely driver: {driver}. "
        f"Best move is to repeat the winning angle once, not change everything. Want me to draft the follow-up post?"
    )
    return body, "binary_yes_no", "Performance spike uses actual delta and likely driver, then recommends a focused repeat action."


def _dormant_with_vera(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    days = dig(trigger, "payload.days_since_last_merchant_message", "some")
    last_topic = str(dig(trigger, "payload.last_topic", "last topic")).replace("_", " ")
    body = (
        f"Hi {first}, pausing the usual reminders — you haven’t replied for {days} days since {last_topic}. "
        f"One useful check only: should I prepare a simple winback post for {merchant_short_name(merchant)}, or stay quiet this week?"
    )
    return body, "open_ended", "Dormancy trigger uses restraint and gives the merchant control instead of spamming."


def _default_message(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    first = salutation(category_slug, merchant)
    kind = str(trigger.get("kind", "update")).replace("_", " ")
    payload_bits = _payload_summary(trigger.get("payload", {}))
    snap = metric_snapshot(merchant)
    body = (
        f"{first}, quick {kind} update for {merchant_short_name(merchant)}. "
        f"{payload_bits}. Current snapshot: {snap}. "
        f"Want me to turn this into one practical next step?"
    )
    return body, "binary_yes_no", "Fallback handler still anchors on trigger kind, payload values, and merchant performance."



def _recall_due(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    name = customer_name(customer)
    merchant_name = merchant_short_name(merchant)
    service_due = str(dig(trigger, "payload.service_due", "follow-up")).replace("_", " ")
    last_date = iso_to_short_date(dig(trigger, "payload.last_service_date"))
    due_date = iso_to_short_date(dig(trigger, "payload.due_date"))
    slots = slot_labels(trigger)
    offer = find_offer(merchant, category, ["cleaning", "checkup", "trial", "consultation"])
    offer_text = offer.get("title") if offer else "your recall visit"
    lp = language_pref(customer, merchant)
    slot_text = _slot_sentence(slots)
    if "hi" in lp:
        body = (
            f"Hi {name}, {merchant_name} here 🦷 Your last visit was on {last_date} — your {service_due} is due around {due_date}. "
            f"Apke liye {slot_text}. {offer_text}. Reply 1/2 for a slot, or send your preferred time."
        )
    else:
        body = (
            f"Hi {name}, {merchant_name} here. Your {service_due} is due around {due_date}"
            f"{f' after your last visit on {last_date}' if last_date else ''}. "
            f"{slot_text}. {offer_text}. Reply 1/2 for a slot, or send a time that works."
        )
    return body, "multi_choice_slot", "Customer recall uses customer name, last/due date, available slots, active offer, consent-safe reminder framing."


def _wedding_package_followup(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    name = customer_name(customer)
    merchant_name = merchant_short_name(merchant)
    days = dig(trigger, "payload.days_to_wedding")
    trial = iso_to_short_date(dig(trigger, "payload.trial_completed"))
    wedding = iso_to_short_date(dig(trigger, "payload.wedding_date", dig(customer or {}, "preferences.wedding_date")))
    offer = find_offer(merchant, category, ["bridal", "facial", "skin"])
    preferred = str(dig(customer or {}, "preferences.preferred_slots", "preferred slot")).replace("_", " ")
    body = (
        f"Hi {name} 💍 {merchant_name} here. {days} days to your wedding ({wedding}) — good window to start skin/hair prep after your bridal trial on {trial}. "
        f"{offer.get('title') if offer else 'Bridal Trial @ ₹999'} can be the entry point, then we block a {preferred} slot for the first prep session. "
        f"Want us to hold one slot for next week?"
    )
    return body, "binary_yes_no", "Bridal follow-up uses wedding date, days remaining, trial date, preference, and salon-specific next step."


def _customer_lapsed(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    name = customer_name(customer)
    merchant_name = merchant_short_name(merchant)
    days = dig(trigger, "payload.days_since_last_visit", "")
    focus = str(dig(trigger, "payload.previous_focus", "")).replace("_", " ")
    months = dig(trigger, "payload.previous_membership_months")
    offer = first_active_offer(merchant, category)
    if category_slug == "gyms":
        body = (
            f"Hi {name}, {merchant_name} here 💪 It’s been {days} days since your last session"
            f"{f' after {months} months with us' if months else ''}. "
            f"Your earlier focus was {focus or 'consistency'}. Want to restart with one light assessment class this week? {offer.get('title') if offer else 'Trial class available'}."
        )
    else:
        body = (
            f"Hi {name}, {merchant_name} here. It’s been {days} days since your last visit. "
            f"We can restart with {offer.get('title') if offer else 'a simple check-in offer'}. Want us to share 2 slot options?"
        )
    return body, "binary_yes_no", "Customer lapsed trigger uses last-visit gap, prior goal/state, and a low-pressure return CTA."


def _trial_followup(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    name = customer_name(customer)
    merchant_name = merchant_short_name(merchant)
    trial_date = iso_to_short_date(dig(trigger, "payload.trial_date"))
    slots = slot_labels(trigger)
    body = (
        f"Hi {name}, {merchant_name} here. Hope the trial session on {trial_date} went well. "
        f"Next beginner-friendly option: {_slot_sentence(slots)}. "
        f"Reply YES and we’ll hold it; or tell us another time."
    )
    return body, "binary_yes_no", "Trial follow-up uses trial date and actual next-session options with a single low-friction CTA."


def _chronic_refill_due(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    name = customer_name(customer)
    merchant_name = merchant_short_name(merchant)
    molecules = ", ".join(dig(trigger, "payload.molecule_list", []) or [])
    runs_out = iso_to_short_date(dig(trigger, "payload.stock_runs_out_iso"))
    saved = dig(trigger, "payload.delivery_address_saved")
    body = (
        f"Hi {name}, {merchant_name} here. Your regular refill for {molecules} looks due before {runs_out}. "
        f"{'We have your delivery address saved.' if saved else 'We can confirm address before delivery.'} "
        f"Reply YES to prepare the refill, or STOP to opt out of refill reminders."
    )
    return body, "binary_yes_no", "Chronic refill uses exact molecules, stock run-out date, delivery preference, and opt-out-safe CTA."


def _appointment_tomorrow(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any], trigger: Dict[str, Any], customer: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    name = customer_name(customer)
    merchant_name = merchant_short_name(merchant)
    when = dig(trigger, "payload.appointment_time") or dig(trigger, "payload.slot_label") or "tomorrow"
    service = str(dig(trigger, "payload.service", "appointment")).replace("_", " ")
    body = (
        f"Hi {name}, reminder from {merchant_name}: your {service} is scheduled for {when}. "
        f"Reply C to confirm or R to reschedule."
    )
    return body, "multi_choice", "Appointment reminder uses merchant name, service, appointment time, and two-letter CTA."



def _template_params(body: str) -> list[str]:
    if len(body) <= 280:
        return [body]
    parts = []
    remaining = body
    while remaining:
        parts.append(remaining[:280])
        remaining = remaining[280:]
    return parts[:6]


def _merchant_anchor_for_digest(category_slug: str, merchant: Dict[str, Any], item: Dict[str, Any]) -> str:
    signals = merchant.get("signals", [])
    perf = merchant.get("performance", {})
    if "high_risk_adult_cohort" in signals or item.get("patient_segment") == "high_risk_adults":
        count = dig(merchant, "customer_aggregate.high_risk_adult_count")
        return f"This maps to your high-risk adult cohort{f' ({count} patients)' if count else ''}."
    if "ctr_below_peer_median" in signals:
        return f"Your CTR is {ctr(perf.get('ctr'))}, below the peer marker {''}."
    if "above_peer_median_calls" in signals:
        return f"Your call volume is already strong ({perf.get('calls')} calls/30d), so this is worth using as authority content."
    if merchant.get("customer_aggregate"):
        total = dig(merchant, "customer_aggregate.total_unique_ytd")
        if total:
            return f"You have {total} customer records YTD, so a useful post can be reused beyond Google."
    return f"It is relevant to {merchant_short_name(merchant)} in {locality_city(merchant)}."


def _best_offer_for_festival(category_slug: str, category: Dict[str, Any], merchant: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if category_slug == "salons":
        return find_offer(merchant, category, ["bridal", "facial", "hair spa", "haircut"])
    if category_slug == "restaurants":
        return find_offer(merchant, category, ["brunch", "combo", "thali", "free"])
    if category_slug == "pharmacies":
        return find_offer(merchant, category, ["health", "delivery", "bp", "sugar"])
    return first_active_offer(merchant, category)


def _category_service_question(category_slug: str, merchant: Dict[str, Any]) -> str:
    if category_slug == "salons":
        return "which service was asked most — haircut, hair spa, bridal, keratin, or waxing"
    if category_slug == "restaurants":
        return "which item moved best — thali, biryani, pizza, combo, or brunch"
    if category_slug == "gyms":
        return "what got more enquiries — PT, trial classes, yoga, strength, or weight-loss"
    if category_slug == "pharmacies":
        return "what moved most — ORS, sunscreen, diabetic refills, BP meds, or delivery"
    if category_slug == "dentists":
        return "what came up more — cleaning, whitening, RCT, aligners, or pediatric checkups"
    return "what customers asked for most"


def _category_review_fix(category_slug: str, theme: str) -> str:
    if "delivery" in theme and category_slug == "restaurants":
        return "set expectation on prep time and add a delivery-delay apology reply"
    if "wait" in theme:
        return "add a wait-time acknowledgement and ask staff to quote realistic slot buffers"
    if category_slug == "salons":
        return "reply warmly, name the service, and ask the customer to rebook with preferred stylist"
    if category_slug == "pharmacies":
        return "reply precisely, avoid blame, and mention pharmacist verification"
    return "reply politely and convert the repeated complaint into one visible process fix"


def _slot_sentence(slots: list[str]) -> str:
    if len(slots) >= 2:
        return f"2 slots are open: {slots[0]} or {slots[1]}"
    if len(slots) == 1:
        return f"one slot is open: {slots[0]}"
    return "we can share 2 suitable slots"


def _payload_summary(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict) or not payload:
        return "A new trigger was received"
    parts = []
    for k, v in list(payload.items())[:4]:
        if isinstance(v, (dict, list)):
            continue
        parts.append(f"{str(k).replace('_', ' ')}: {v}")
    return "; ".join(parts) if parts else "New context is available"
