from __future__ import annotations

import time
from threading import RLock
from typing import Any, Dict, Optional, Tuple

VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


class ContextStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", RLock()):
            self.started_at = time.time()
            self.data: Dict[str, Dict[str, Dict[str, Any]]] = {s: {} for s in VALID_SCOPES}
            self.versions: Dict[str, Dict[str, int]] = {s: {} for s in VALID_SCOPES}
            self.aliases: Dict[str, Dict[str, str]] = {s: {} for s in VALID_SCOPES}
            self.sent_suppression_keys: set[str] = set()
            self.sent_conversation_ids: set[str] = set()
            self.conversations: Dict[str, Dict[str, Any]] = {}
            self.opted_out_merchants: set[str] = set()

    def put(self, scope: str, context_id: str, version: int, payload: Dict[str, Any]) -> Tuple[bool, str, int]:
        if scope not in VALID_SCOPES:
            return False, "invalid_scope", -1
        if not context_id:
            return False, "missing_context_id", -1
        if not isinstance(payload, dict):
            return False, "invalid_payload", -1
        try:
            version = int(version)
        except Exception:
            return False, "invalid_version", -1

        with self._lock:
            current = self.versions[scope].get(context_id, -1)
            natural_id = self._natural_id(scope, payload)

            self.data[scope][context_id] = payload
            self.versions[scope][context_id] = version
            if natural_id:
                self.aliases[scope][natural_id] = context_id

            if scope == "trigger":
                sk = payload.get("suppression_key")
                if sk:
                    self.sent_suppression_keys.discard(str(sk))
                trg_id = payload.get("id") or context_id
                self.aliases["trigger"][str(trg_id)] = context_id

            if scope == "merchant":
                mid = payload.get("merchant_id")
                if mid:
                    self.aliases["merchant"][str(mid)] = context_id
            elif scope == "customer":
                cid = payload.get("customer_id")
                if cid:
                    self.aliases["customer"][str(cid)] = context_id
            elif scope == "category":
                slug = payload.get("slug")
                if slug:
                    self.aliases["category"][str(slug)] = context_id

            return True, "accepted", max(current, version)

    def infer_and_put_raw(self, payload: Dict[str, Any]) -> Tuple[bool, str, int, str, str]:
        scope = ""
        context_id = ""
        if "slug" in payload and "offer_catalog" in payload:
            scope = "category"
            context_id = str(payload.get("slug"))
        elif "merchant_id" in payload and "category_slug" in payload:
            scope = "merchant"
            context_id = str(payload.get("merchant_id"))
        elif "customer_id" in payload and "relationship" in payload:
            scope = "customer"
            context_id = str(payload.get("customer_id"))
        elif "id" in payload and "kind" in payload:
            scope = "trigger"
            context_id = str(payload.get("id"))
        else:
            return False, "could_not_infer_context_type", -1, "", ""
        accepted, reason, current = self.put(scope, context_id, 1, payload)
        return accepted, reason, current, scope, context_id

    def _natural_id(self, scope: str, payload: Dict[str, Any]) -> Optional[str]:
        if scope == "category":
            return payload.get("slug")
        if scope == "merchant":
            return payload.get("merchant_id")
        if scope == "customer":
            return payload.get("customer_id")
        if scope == "trigger":
            return payload.get("id")
        return None

    def counts(self) -> Dict[str, int]:
        with self._lock:
            return {scope: len(self.data[scope]) for scope in ["category", "merchant", "customer", "trigger"]}

    def get(self, scope: str, context_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not context_id or scope not in VALID_SCOPES:
            return None
        key = str(context_id)
        with self._lock:
            if key in self.data[scope]:
                return self.data[scope][key]
            alias = self.aliases[scope].get(key)
            if alias:
                return self.data[scope].get(alias)
            return None

    def all_items(self, scope: str) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self.data.get(scope, {}))

    def already_sent(self, suppression_key: str) -> bool:
        if not suppression_key:
            return False
        with self._lock:
            return suppression_key in self.sent_suppression_keys

    def mark_sent(self, suppression_key: str, conversation_id: str, action: Dict[str, Any]) -> None:
        with self._lock:
            if suppression_key:
                self.sent_suppression_keys.add(str(suppression_key))
            if conversation_id:
                self.sent_conversation_ids.add(str(conversation_id))
                conv = self.conversations.setdefault(str(conversation_id), {
                    "conversation_id": str(conversation_id),
                    "turns": [],
                    "last_bodies": [],
                    "auto_reply_count": 0,
                    "trigger_id": action.get("trigger_id"),
                    "merchant_id": action.get("merchant_id"),
                    "customer_id": action.get("customer_id"),
                    "category_slug": action.get("category_slug"),
                    "template_name": action.get("template_name"),
                })
                conv["trigger_id"] = conv.get("trigger_id") or action.get("trigger_id")
                conv["merchant_id"] = conv.get("merchant_id") or action.get("merchant_id")
                conv["customer_id"] = conv.get("customer_id") or action.get("customer_id")
                conv["template_name"] = conv.get("template_name") or action.get("template_name")
                body = action.get("body", "")
                conv["turns"].append({"from": "bot", "body": body, "ts": time.time()})
                if body:
                    conv["last_bodies"].append(body)

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        with self._lock:
            return self.conversations.setdefault(str(conversation_id), {
                "conversation_id": str(conversation_id),
                "turns": [],
                "last_bodies": [],
                "auto_reply_count": 0,
            })

    def add_inbound_turn(self, conversation_id: str, message: str, merchant_id: Optional[str], customer_id: Optional[str], from_role: str = "") -> Dict[str, Any]:
        with self._lock:
            conv = self.get_conversation(conversation_id)
            if merchant_id and not conv.get("merchant_id"):
                conv["merchant_id"] = merchant_id
            if customer_id and not conv.get("customer_id"):
                conv["customer_id"] = customer_id
            conv["turns"].append({"from": from_role or "inbound", "body": message, "ts": time.time()})
            return conv

    def remember_bot_reply(self, conversation_id: str, body: str) -> None:
        with self._lock:
            conv = self.get_conversation(conversation_id)
            conv["turns"].append({"from": "bot", "body": body, "ts": time.time()})
            if body:
                conv["last_bodies"].append(body)

    def mark_opt_out(self, merchant_id: Optional[str]) -> None:
        if merchant_id:
            with self._lock:
                self.opted_out_merchants.add(str(merchant_id))

    def is_opted_out(self, merchant_id: Optional[str]) -> bool:
        if not merchant_id:
            return False
        with self._lock:
            return str(merchant_id) in self.opted_out_merchants


store = ContextStore()
