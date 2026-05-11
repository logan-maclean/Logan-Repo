"""Thin GoCodes REST client.

The exact endpoint paths and JSON field names below are placeholders modeled
on the GoCodes Postman collection (https://documenter.getpostman.com/view/15366524/TzJpgeEe).
That collection is rendered client-side, so verify the path/field names below
against the live docs and adjust if your tenant differs.

Auth: GoCodes uses an API key. We send it both as the ``api_key`` query param
and in an ``Authorization`` header to cover both styles their docs show.
"""
from dataclasses import dataclass
from typing import Iterator, Optional

import requests

from .config import Config


@dataclass
class Asset:
    id: str
    name: str
    serial_number: str
    category: str
    status: str
    location: str
    assigned_to_name: str
    assigned_to_email: str
    notes: str
    updated_at_epoch: int
    created_at_epoch: int
    raw: dict  # original record, kept for debugging / reindex


class GoCodesClient:
    def __init__(self, cfg: Config):
        if not cfg.gocodes_api_key:
            raise RuntimeError("GOCODES_API_KEY is required")
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {cfg.gocodes_api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.cfg.gocodes_base_url.rstrip('/')}/{path.lstrip('/')}"

    def _auth_params(self, extra: Optional[dict] = None) -> dict:
        params = {"api_key": self.cfg.gocodes_api_key}
        if extra:
            params.update(extra)
        return params

    def list_assets(self, page_size: int = 100) -> Iterator[Asset]:
        """Stream every asset, paginating until the API stops returning a next page."""
        page = 1
        while True:
            resp = self.session.get(
                self._url("/assets"),
                params=self._auth_params({"page": page, "per_page": page_size}),
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
            items = payload.get("assets") or payload.get("data") or payload.get("results") or []
            if not items:
                return
            for item in items:
                yield _to_asset(item)
            if len(items) < page_size:
                return
            page += 1

    def get_asset(self, asset_id: str) -> Asset:
        resp = self.session.get(
            self._url(f"/assets/{asset_id}"),
            params=self._auth_params(),
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        return _to_asset(payload.get("asset") or payload)

    def find_asset(self, query: str) -> Optional[Asset]:
        """Best-effort lookup by name or serial number."""
        resp = self.session.get(
            self._url("/assets"),
            params=self._auth_params({"q": query, "per_page": 5}),
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("assets") or resp.json().get("data") or []
        return _to_asset(items[0]) if items else None

    def assign_asset(
        self,
        asset_id: str,
        assignee_email: str,
        assignee_name: str = "",
        location: str = "",
        note: str = "",
    ) -> Asset:
        """Assign an asset to a person and optionally update its location."""
        body: dict = {
            "assigned_to": {
                "email": assignee_email,
                "name": assignee_name or assignee_email,
            }
        }
        if location:
            body["location"] = location
        if note:
            body["note"] = note

        resp = self.session.patch(
            self._url(f"/assets/{asset_id}"),
            params=self._auth_params(),
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        return _to_asset(payload.get("asset") or payload)

    def update_location(self, asset_id: str, location: str) -> Asset:
        resp = self.session.patch(
            self._url(f"/assets/{asset_id}"),
            params=self._auth_params(),
            json={"location": location},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        return _to_asset(payload.get("asset") or payload)


def _to_asset(item: dict) -> Asset:
    assigned = item.get("assigned_to") or item.get("assignee") or {}
    if isinstance(assigned, str):
        assigned = {"name": assigned, "email": ""}
    location = item.get("location") or item.get("location_name") or ""
    if isinstance(location, dict):
        location = location.get("name", "")
    return Asset(
        id=str(item.get("id") or item.get("asset_id") or ""),
        name=item.get("name") or item.get("asset_name") or "",
        serial_number=item.get("serial_number") or item.get("serial") or "",
        category=item.get("category") or item.get("category_name") or "",
        status=item.get("status") or "",
        location=location,
        assigned_to_name=assigned.get("name", ""),
        assigned_to_email=assigned.get("email", ""),
        notes=item.get("notes") or item.get("description") or "",
        updated_at_epoch=_epoch(item.get("updated_at") or item.get("modified_at")),
        created_at_epoch=_epoch(item.get("created_at")),
        raw=item,
    )


def _epoch(value) -> int:
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return 0
