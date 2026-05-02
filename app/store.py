from __future__ import annotations

import json
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple


VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


class ContextStore:
    """
    Small in-memory store for the magicpin judge lifecycle.

    The official brief allows in-memory persistence as long as the server does not
    restart during the test window. This class also tracks suppressions and
    conversation state so the bot does not repeat itself.
    """

    def __init__(self) -> None:
        self.started_at = time.time()
        self._lock = RLock()

        self.data: Dict[str, Dict[str, Dict[str, Any]]] = {
            "category": {},
            "merchant": {},
            "customer": {},
            "trigger": {},
        }
        self.versions: Dict[str, Dict[str, int]] = {
            "category": {},
            "merchant": {},
            "customer": {},
            "trigger": {},
        }

        self.sent_suppression_keys: set[str] = set()
        self.sent_conversation_ids: set[str] = set()
        self.conversations: Dict[str, Dict[str, Any]] = {}
        self.auto_reply_counts: Dict[str, int] = {}
        self.opted_out_merchants: set[str] = set()


    def reset(self) -> None:
        with self._lock:
            for scope in self.data:
                self.data[scope].clear()
                self.versions[scope].clear()
            self.sent_suppression_keys.clear()
            self.sent_conversation_ids.clear()
            self.conversations.clear()
            self.auto_reply_counts.clear()
            self.opted_out_merchants.clear()
            self.started_at = time.time()

    def put(self, scope: str, context_id: str, version: int, payload: Dict[str, Any]) -> Tuple[bool, str, int]:
        """
        Returns (accepted, reason, current_version).

        Same or lower versions are treated as stale/conflict to match the API
        examples. Higher versions atomically replace older payloads.
        """
        if scope not in VALID_SCOPES:
            return False, "invalid_scope", -1

        if not context_id:
            return False, "missing_context_id", -1

        try:
            version = int(version)
        except Exception:
            return False, "invalid_version", -1

        with self._lock:
            current = self.versions[scope].get(context_id)
            if current is not None and version <= current:
                return False, "stale_version", current

            self.data[scope][context_id] = payload
            self.versions[scope][context_id] = version

            natural_id = self._natural_id(scope, payload)
            if natural_id and natural_id != context_id:
                self.data[scope][natural_id] = payload
                self.versions[scope][natural_id] = version

            return True, "accepted", version

    def infer_and_put_raw(self, payload: Dict[str, Any]) -> Tuple[bool, str, int, str, str]:
        """
        Convenience for manual curl usage where a raw category/merchant/customer/
        trigger JSON is posted directly instead of the official envelope.
        Returns accepted, reason, current_version, scope, context_id.
        """
        scope = ""
        context_id = ""

        if "slug" in payload and "offer_catalog" in payload:
            scope = "category"
            context_id = str(payload.get("slug"))
        elif "merchant_id" in payload and "category_slug" in payload and "performance" in payload:
            scope = "merchant"
            context_id = str(payload.get("merchant_id"))
        elif "customer_id" in payload and "relationship" in payload:
            scope = "customer"
            context_id = str(payload.get("customer_id"))
        elif "id" in payload and "kind" in payload and "merchant_id" in payload:
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
            return {scope: len(items) for scope, items in self.data.items()}

    def get(self, scope: str, context_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not context_id or scope not in VALID_SCOPES:
            return None
        with self._lock:
            return self.data[scope].get(context_id)

    def categories(self) -> Dict[str, Dict[str, Any]]:
        return self.data["category"]

    def merchants(self) -> Dict[str, Dict[str, Any]]:
        return self.data["merchant"]

    def customers(self) -> Dict[str, Dict[str, Any]]:
        return self.data["customer"]

    def triggers(self) -> Dict[str, Dict[str, Any]]:
        return self.data["trigger"]


    def already_sent(self, suppression_key: str) -> bool:
        if not suppression_key:
            return False
        with self._lock:
            return suppression_key in self.sent_suppression_keys

    def mark_sent(self, suppression_key: str, conversation_id: str, action: Dict[str, Any]) -> None:
        with self._lock:
            if suppression_key:
                self.sent_suppression_keys.add(suppression_key)
            if conversation_id:
                self.sent_conversation_ids.add(conversation_id)
                self.conversations.setdefault(conversation_id, {
                    "conversation_id": conversation_id,
                    "turns": [],
                    "last_bodies": [],
                    "auto_reply_count": 0,
                    "trigger_id": action.get("trigger_id"),
                    "merchant_id": action.get("merchant_id"),
                    "customer_id": action.get("customer_id"),
                    "category_slug": action.get("category_slug"),
                    "template_name": action.get("template_name"),
                })
                self.conversations[conversation_id]["turns"].append({
                    "from": "bot",
                    "body": action.get("body", ""),
                    "ts": time.time(),
                })
                self.conversations[conversation_id]["last_bodies"].append(action.get("body", ""))

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        with self._lock:
            return self.conversations.setdefault(conversation_id, {
                "conversation_id": conversation_id,
                "turns": [],
                "last_bodies": [],
                "auto_reply_count": 0,
            })

    def add_inbound_turn(self, conversation_id: str, message: str, merchant_id: Optional[str], customer_id: Optional[str]) -> Dict[str, Any]:
        with self._lock:
            conv = self.get_conversation(conversation_id)
            if merchant_id and not conv.get("merchant_id"):
                conv["merchant_id"] = merchant_id
            if customer_id and not conv.get("customer_id"):
                conv["customer_id"] = customer_id
            conv["turns"].append({"from": "merchant_or_customer", "body": message, "ts": time.time()})
            return conv

    def remember_bot_reply(self, conversation_id: str, body: str) -> None:
        with self._lock:
            conv = self.get_conversation(conversation_id)
            conv["turns"].append({"from": "bot", "body": body, "ts": time.time()})
            conv["last_bodies"].append(body)

    def mark_opt_out(self, merchant_id: Optional[str]) -> None:
        if merchant_id:
            with self._lock:
                self.opted_out_merchants.add(merchant_id)

    def is_opted_out(self, merchant_id: Optional[str]) -> bool:
        if not merchant_id:
            return False
        with self._lock:
            return merchant_id in self.opted_out_merchants


    def load_dataset_from_disk(self, dataset_dir: str | Path = "dataset") -> Dict[str, int]:
        """
        Loads local challenge dataset if present. This is useful because the
        public judge simulator pushes only a small subset of customers during
        warmup, while real evaluation pushes all contexts.
        """
        root = Path(dataset_dir)
        if not root.exists():
            return self.counts()

        cat_dir = root / "categories"
        if cat_dir.exists():
            for file in sorted(cat_dir.glob("*.json")):
                self._load_single_json_as_scope(file, "category", "slug")

        for folder, scope, key in [
            ("merchants", "merchant", "merchant_id"),
            ("customers", "customer", "customer_id"),
            ("triggers", "trigger", "id"),
        ]:
            d = root / folder
            if d.exists():
                for file in sorted(d.glob("*.json")):
                    self._load_single_json_as_scope(file, scope, key)

        for file_name, scope, list_key, item_key in [
            ("merchants_seed.json", "merchant", "merchants", "merchant_id"),
            ("customers_seed.json", "customer", "customers", "customer_id"),
            ("triggers_seed.json", "trigger", "triggers", "id"),
        ]:
            file = root / file_name
            if file.exists():
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                    for item in data.get(list_key, []):
                        cid = item.get(item_key)
                        if cid:
                            self.put(scope, cid, 0, item)
                except Exception:
                    continue

        return self.counts()

    def _load_single_json_as_scope(self, file: Path, scope: str, key: str) -> None:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            cid = data.get(key)
            if cid:
                self.put(scope, cid, 0, data)
        except Exception:
            pass


store = ContextStore()
