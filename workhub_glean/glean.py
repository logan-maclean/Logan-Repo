from typing import Iterable

import requests

from .config import Config
from .workhub import WorkhubDoc


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
        """Register the Workhub datasource in Glean (idempotent)."""
        body = {
            "name": self.cfg.workhub_datasource,
            "displayName": "Workhub",
            "datasourceCategory": "KNOWLEDGE_HUB",
            "urlRegex": "",
            "iconUrl": "",
            "trustUrlRegexForViewActivity": True,
            "isUserReferencedByEmail": True,
            "objectDefinitions": [
                {
                    "name": "Document",
                    "displayLabel": "Workhub document",
                }
            ],
        }
        resp = self.session.post(
            f"{self.cfg.indexing_base}/adddatasource", json=body, timeout=30
        )
        resp.raise_for_status()

    def index(self, docs: Iterable[WorkhubDoc], batch_size: int = 50) -> int:
        """Bulk-index Workhub docs into Glean. Returns count indexed."""
        batch: list[dict] = []
        total = 0
        for doc in docs:
            batch.append(self._to_glean_doc(doc))
            if len(batch) >= batch_size:
                self._flush(batch)
                total += len(batch)
                batch = []
        if batch:
            self._flush(batch)
            total += len(batch)
        return total

    def _flush(self, batch: list[dict]) -> None:
        body = {"datasource": self.cfg.workhub_datasource, "documents": batch}
        resp = self.session.post(
            f"{self.cfg.indexing_base}/indexdocuments", json=body, timeout=60
        )
        resp.raise_for_status()

    def _to_glean_doc(self, d: WorkhubDoc) -> dict:
        return {
            "id": d.id,
            "datasource": self.cfg.workhub_datasource,
            "objectType": "Document",
            "title": d.title,
            "viewURL": d.url,
            "body": {"mimeType": "text/plain", "textContent": d.body},
            "owner": {"email": d.author_email} if d.author_email else None,
            "updatedAt": d.updated_at_epoch,
            "createdAt": d.created_at_epoch,
            # Default ACL: visible to anyone in the org. Tighten this if
            # Workhub has per-doc permissions you want Glean to respect.
            "permissions": {"allowAnonymousAccess": True},
        }


class GleanClient:
    """Asks Glean questions. Glean handles retrieval over indexed Workhub data."""

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

    def ask(self, question: str, only_workhub: bool = True) -> dict:
        """Ask Glean a question. By default restricts retrieval to the
        Workhub datasource so answers are grounded in Workhub content."""
        body: dict = {
            "messages": [
                {
                    "author": "USER",
                    "messageType": "CONTENT",
                    "fragments": [{"text": question}],
                }
            ]
        }
        if only_workhub:
            body["inclusions"] = {
                "datasourcesFilter": [self.cfg.workhub_datasource]
            }
        resp = self.session.post(
            f"{self.cfg.client_base}/chat", json=body, timeout=60
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
