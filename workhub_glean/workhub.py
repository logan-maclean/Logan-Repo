from dataclasses import dataclass
from typing import Iterator

import requests

from .config import Config


@dataclass
class WorkhubDoc:
    id: str
    title: str
    url: str
    body: str
    author_email: str
    updated_at_epoch: int  # seconds since epoch
    created_at_epoch: int


def fetch_documents(cfg: Config) -> Iterator[WorkhubDoc]:
    """Stream documents from Workhub.

    Replace the request shape and field mapping below to match the actual
    Workhub API. The function is a generator so the caller can batch into
    Glean without loading everything into memory.
    """
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {cfg.workhub_api_token}"})

    url = f"{cfg.workhub_base_url}/documents"
    params: dict = {"page_size": 100}

    while url:
        resp = session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()

        for item in payload.get("results", []):
            yield WorkhubDoc(
                id=str(item["id"]),
                title=item.get("title", ""),
                url=item.get("url", ""),
                body=item.get("body", "") or item.get("content", ""),
                author_email=item.get("author", {}).get("email", ""),
                updated_at_epoch=int(item.get("updated_at_epoch", 0)),
                created_at_epoch=int(item.get("created_at_epoch", 0)),
            )

        url = payload.get("next")
        params = {}
