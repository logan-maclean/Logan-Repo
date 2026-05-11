"""Glean side of the GoCodes integration: index assets, and ask questions."""
from typing import Iterable

import requests

from .config import Config
from .gocodes import Asset


class GleanIndexer:
    def __init__(self, cfg: Config):
        if not cfg.glean_indexing_token:
            raise RuntimeError("GLEAN_INDEXING_TOKEN is required for indexing")
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {cfg.glean_indexing_token}",
                "Content-Type": "application/json",
            }
        )

    def ensure_datasource(self) -> None:
        body = {
            "name": self.cfg.gocodes_datasource,
            "displayName": "GoCodes",
            "datasourceCategory": "KNOWLEDGE_HUB",
            "urlRegex": "",
            "iconUrl": "",
            "trustUrlRegexForViewActivity": True,
            "isUserReferencedByEmail": True,
            "objectDefinitions": [
                {"name": "Asset", "displayLabel": "GoCodes asset"}
            ],
        }
        resp = self.session.post(
            f"{self.cfg.indexing_base}/adddatasource", json=body, timeout=30
        )
        resp.raise_for_status()

    def index(self, assets: Iterable[Asset], batch_size: int = 50) -> int:
        batch: list[dict] = []
        total = 0
        for asset in assets:
            batch.append(self._to_glean_doc(asset))
            if len(batch) >= batch_size:
                self._flush(batch)
                total += len(batch)
                batch = []
        if batch:
            self._flush(batch)
            total += len(batch)
        return total

    def _flush(self, batch: list[dict]) -> None:
        body = {"datasource": self.cfg.gocodes_datasource, "documents": batch}
        resp = self.session.post(
            f"{self.cfg.indexing_base}/indexdocuments", json=body, timeout=60
        )
        resp.raise_for_status()

    def _to_glean_doc(self, a: Asset) -> dict:
        # Glean retrieves by text; pack the fields users ask about ("who has X?",
        # "where is X?") into the body so chat can ground answers on them.
        lines = [
            f"Asset: {a.name}",
            f"Serial number: {a.serial_number}" if a.serial_number else "",
            f"Category: {a.category}" if a.category else "",
            f"Status: {a.status}" if a.status else "",
            f"Location: {a.location}" if a.location else "Location: unassigned",
            (
                f"Assigned to: {a.assigned_to_name} <{a.assigned_to_email}>"
                if a.assigned_to_email or a.assigned_to_name
                else "Assigned to: unassigned"
            ),
            f"Notes: {a.notes}" if a.notes else "",
        ]
        body_text = "\n".join(line for line in lines if line)

        doc = {
            "id": a.id,
            "datasource": self.cfg.gocodes_datasource,
            "objectType": "Asset",
            "title": a.name or f"Asset {a.id}",
            "viewURL": self.cfg.gocodes_asset_web_url.format(id=a.id),
            "body": {"mimeType": "text/plain", "textContent": body_text},
            "updatedAt": a.updated_at_epoch,
            "createdAt": a.created_at_epoch,
            "permissions": {"allowAnonymousAccess": True},
        }
        if a.assigned_to_email:
            doc["owner"] = {"email": a.assigned_to_email}
        return doc


class GleanClient:
    """Asks Glean questions, scoped to the GoCodes datasource by default."""

    def __init__(self, cfg: Config):
        if not cfg.glean_client_token:
            raise RuntimeError("GLEAN_CLIENT_TOKEN is required for chat/search")
        self.cfg = cfg
        self.session = requests.Session()
        headers = {
            "Authorization": f"Bearer {cfg.glean_client_token}",
            "Content-Type": "application/json",
        }
        if cfg.glean_act_as_user:
            headers["X-Scio-ActAs"] = cfg.glean_act_as_user
        self.session.headers.update(headers)

    def ask(self, question: str, only_gocodes: bool = True) -> dict:
        body: dict = {
            "messages": [
                {
                    "author": "USER",
                    "messageType": "CONTENT",
                    "fragments": [{"text": question}],
                }
            ]
        }
        if only_gocodes:
            body["inclusions"] = {
                "datasourcesFilter": [self.cfg.gocodes_datasource]
            }
        resp = self.session.post(
            f"{self.cfg.client_base}/chat", json=body, timeout=60
        )
        resp.raise_for_status()
        return resp.json()

    def search(self, query: str, page_size: int = 10) -> dict:
        body = {
            "query": query,
            "pageSize": page_size,
            "requestOptions": {
                "datasourcesFilter": [self.cfg.gocodes_datasource]
            },
        }
        resp = self.session.post(
            f"{self.cfg.client_base}/search", json=body, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def extract_answer(response: dict) -> str:
        parts: list[str] = []
        for msg in response.get("messages", []):
            for frag in msg.get("fragments", []):
                if "text" in frag:
                    parts.append(frag["text"])
        return "\n".join(parts).strip()
