"""workhub_to_glean.py

Syncs worker compliance data from Workhub into Glean as a custom data source,
making it searchable in your enterprise search.

WHAT IT DOES (in plain English):
    Pass 1 - For each active worker, calls 4 Workhub APIs:
        - Active Worker List      -> name, role, location, email
        - Worker Certificates     -> third-party credentials
        - Worker Competencies     -> internal skill sign-offs
        - Worker Policies         -> acknowledged company policies
        - Worker Reviewed Procedures -> reviewed safety procedures
        Builds one "Worker Card" Glean document per worker.

    Pass 2 - Inverts the policy + procedure data:
        - Builds one "Policy Card" per unique policy
        - Builds one "Procedure Card" per unique procedure

    Pass 3 - Pushes everything to Glean using the bulk indexing API. Bulk
        indexing is an atomic replace -- documents not present in this
        upload are removed on commit, so departed workers and renamed
        policies/procedures drop out automatically.

REQUIREMENTS:
    pip install requests python-dotenv

SETUP:
    Create a `.env` file next to this script with the variables listed in
    REQUIRED below.

HOW TO RUN:
    First time:    python workhub_to_glean.py --test
    Small live:    python workhub_to_glean.py --sync --limit 5
    Full sync:     python workhub_to_glean.py --sync
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ---------------------------------------------------------------------------
# CONFIG -- loaded from .env (never hardcoded)
# ---------------------------------------------------------------------------
load_dotenv()


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


WORKHUB_CLIENT_KEY = _env("WORKHUB_CLIENT_KEY")
WORKHUB_CLIENT_SECRET = _env("WORKHUB_CLIENT_SECRET")
WORKHUB_LIST_URL = _env("WORKHUB_LIST_URL")
WORKHUB_CERT_URL = _env("WORKHUB_CERT_URL")
WORKHUB_COMP_URL = _env("WORKHUB_COMP_URL")
WORKHUB_POLICY_URL = _env("WORKHUB_POLICY_URL")
WORKHUB_PROC_URL = _env("WORKHUB_PROC_URL")

GLEAN_INDEXING_API_TOKEN = _env("GLEAN_INDEXING_API_TOKEN")
GLEAN_INSTANCE = _env("GLEAN_INSTANCE")

# Must match the Unique Name you set when creating the data source in Glean.
GLEAN_DATASOURCE = _env("GLEAN_DATASOURCE", "workhub")

# Where workers/policies live in Workhub -- used to build clickable links.
WORKHUB_WEB_BASE = _env("WORKHUB_WEB_BASE", "https://app.workhub.com")

# How long to wait between Workhub calls (seconds).
RATE_LIMIT_SLEEP = float(_env("RATE_LIMIT_SLEEP", "0.3"))

REQUIRED_ENV = {
    "WORKHUB_CLIENT_KEY": WORKHUB_CLIENT_KEY,
    "WORKHUB_CLIENT_SECRET": WORKHUB_CLIENT_SECRET,
    "WORKHUB_LIST_URL": WORKHUB_LIST_URL,
    "WORKHUB_CERT_URL": WORKHUB_CERT_URL,
    "WORKHUB_COMP_URL": WORKHUB_COMP_URL,
    "WORKHUB_POLICY_URL": WORKHUB_POLICY_URL,
    "WORKHUB_PROC_URL": WORKHUB_PROC_URL,
    "GLEAN_INDEXING_API_TOKEN": GLEAN_INDEXING_API_TOKEN,
    "GLEAN_INSTANCE": GLEAN_INSTANCE,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("workhub-glean")


def check_environment() -> None:
    missing = [k for k, v in REQUIRED_ENV.items() if not v]
    if missing:
        log.error("Missing required env vars: %s", ", ".join(missing))
        log.error("Check your .env file in this folder.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Shared HTTP session with retry/backoff (handles 429 + 5xx transparently)
# ---------------------------------------------------------------------------
def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,  # 1s, 2s, 4s, 8s, 16s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "workhub-glean-sync/1.0"})
    return s


HTTP = _build_session()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """'Fall Protection Plan' -> 'fall-protection-plan'."""
    return _SLUG_RE.sub("-", text.lower()).strip("-") or "untitled"


def stable_id(prefix: str, key: str) -> str:
    """Slug for readability, hash to disambiguate near-duplicates."""
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{slugify(key)}-{digest}"


def clean_list(values: Any) -> list[str]:
    """Drop None / empty / literal 'null' entries Workhub sometimes returns."""
    if not values:
        return []
    out: list[str] = []
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() != "null":
            out.append(s)
    return out


def now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# WORKHUB SIDE -- pulling data
# ---------------------------------------------------------------------------
def workhub_headers() -> dict:
    # Workhub uses an unusual 'amx Key:Secret' format -- keep it exact.
    return {
        "Authorization": f"amx {WORKHUB_CLIENT_KEY}:{WORKHUB_CLIENT_SECRET}",
        "Accept": "application/json",
    }


def fetch_active_workers() -> list[dict]:
    log.info("Fetching active worker list from Workhub...")
    r = HTTP.get(WORKHUB_LIST_URL, headers=workhub_headers(), timeout=60)
    r.raise_for_status()
    data = r.json()

    # Tolerate both bare-array and envelope-wrapped shapes.
    if isinstance(data, dict):
        for key in ("workers", "data", "items", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        log.error("Unexpected worker list shape: %s", type(data).__name__)
        return []

    log.info("Got %d active workers.", len(data))
    return data


def _fetch_field(url_template: str, worker_id: str, field: str) -> list[str]:
    url = url_template.replace("{{WorkerIDNumber}}", str(worker_id))
    r = HTTP.get(url, headers=workhub_headers(), timeout=30)
    r.raise_for_status()
    return clean_list(r.json().get(field))


def fetch_certs(wid: str) -> list[str]:
    return _fetch_field(WORKHUB_CERT_URL, wid, "certificates")


def fetch_competencies(wid: str) -> list[str]:
    return _fetch_field(WORKHUB_COMP_URL, wid, "competencies")


def fetch_policies(wid: str) -> list[str]:
    return _fetch_field(WORKHUB_POLICY_URL, wid, "policies")


def fetch_procedures(wid: str) -> list[str]:
    return _fetch_field(WORKHUB_PROC_URL, wid, "reviewedProcedures")


def gather_worker(basic: dict) -> dict | None:
    """Pull all 4 sub-resources for one worker. A failure on one sub-call
    leaves that field empty rather than dropping the worker entirely."""
    wid = basic.get("idNumber")
    if not wid:
        log.warning("Skipping worker with no idNumber (email=%r)", basic.get("email"))
        return None
    wid = str(wid)

    def safe(fn: Callable[[str], list[str]], label: str) -> list[str]:
        try:
            return fn(wid)
        except requests.HTTPError as e:
            log.error("HTTP error fetching %s for worker %s: %s", label, wid, e)
        except requests.RequestException as e:
            log.error("Network error fetching %s for worker %s: %s", label, wid, e)
        except Exception:
            log.exception("Unexpected error fetching %s for worker %s", label, wid)
        return []

    certs = safe(fetch_certs, "certificates")
    time.sleep(RATE_LIMIT_SLEEP)
    comps = safe(fetch_competencies, "competencies")
    time.sleep(RATE_LIMIT_SLEEP)
    policies = safe(fetch_policies, "policies")
    time.sleep(RATE_LIMIT_SLEEP)
    procedures = safe(fetch_procedures, "procedures")

    return {
        "basic": basic,
        "certificates": certs,
        "competencies": comps,
        "policies": policies,
        "procedures": procedures,
    }


def gather_all(limit: int | None = None) -> list[dict]:
    workers = fetch_active_workers()
    if limit:
        workers = workers[:limit]
        log.info("Limiting to first %d workers for this run.", limit)

    out: list[dict] = []
    for i, w in enumerate(workers, 1):
        data = gather_worker(w)
        if data:
            out.append(data)
        if i % 25 == 0:
            log.info("  progress: %d / %d", i, len(workers))
        time.sleep(RATE_LIMIT_SLEEP)
    log.info("Gathered data for %d / %d workers.", len(out), len(workers))
    return out


# ---------------------------------------------------------------------------
# DOCUMENT BUILDERS
# ---------------------------------------------------------------------------
# Visibility: anyone authenticated in your Glean tenant can find these,
# matching Workhub's "all employees see compliance data" model. If Workhub
# ever introduces per-doc ACLs, this is the only knob to change.
COMPANY_VISIBLE = {"allowAnonymousAccess": True}


def _full_name(b: dict) -> str:
    parts = [b.get("firstName") or "", b.get("middleName") or "", b.get("lastName") or ""]
    return " ".join(p for p in parts if p) or "Unknown Worker"


def _person_summary(b: dict) -> dict:
    return {
        "name": _full_name(b),
        "position": b.get("position") or "Unknown role",
        "location": b.get("location") or "Unknown location",
    }


def build_worker_doc(w: dict) -> dict:
    b = w["basic"]
    wid = str(b.get("idNumber") or "unknown")
    name = _full_name(b)
    position = b.get("position") or "Unknown role"
    location = b.get("location") or "Unknown location"
    email = (b.get("email") or "").strip()

    lines: list[str] = [
        f"Worker: {name}",
        f"Email: {email}" if email else "Email: (not on file)",
        f"Worker ID: {wid}",
        f"Position: {position}",
        f"Location: {location}",
        "",
    ]

    def section(header: str, items: list[str], empty_msg: str) -> None:
        if items:
            lines.append(header)
            lines.extend(f"  - {item}" for item in items)
        else:
            lines.append(empty_msg)
        lines.append("")

    section(
        "Active Certifications (third-party credentials):",
        w["certificates"],
        "No active certifications on file.",
    )
    section(
        "Verified Competencies (internal sign-offs):",
        w["competencies"],
        "No verified competencies on file.",
    )

    # Summary counts only -- the full lists live in the policy/procedure docs.
    lines.append(f"Policies acknowledged: {len(w['policies'])}")
    lines.append(f"Procedures reviewed:   {len(w['procedures'])}")

    doc = {
        "id": f"worker-{wid}",
        "datasource": GLEAN_DATASOURCE,
        "objectType": "WorkerComplianceRecord",
        "title": f"{name} - {position}",
        "viewURL": f"{WORKHUB_WEB_BASE}/admin/workers/{wid}",
        "body": {"mimeType": "text/plain", "textContent": "\n".join(lines)},
        "updatedAt": now_epoch(),
        "permissions": COMPANY_VISIBLE,
    }
    if email:
        doc["owner"] = {"email": email}
    return doc


def build_policy_doc(name: str, acknowledgers: list[dict]) -> dict:
    lines = [
        f"Company Policy: {name}",
        "",
        f"Acknowledged by {len(acknowledgers)} worker(s):",
        "",
    ]
    for a in sorted(acknowledgers, key=lambda x: x["name"]):
        lines.append(f"  - {a['name']} ({a['position']}, {a['location']})")
    return {
        "id": stable_id("policy", name),
        "datasource": GLEAN_DATASOURCE,
        "objectType": "CompanyPolicy",
        "title": f"Policy: {name}",
        "viewURL": f"{WORKHUB_WEB_BASE}/admin/policies",
        "body": {"mimeType": "text/plain", "textContent": "\n".join(lines)},
        "updatedAt": now_epoch(),
        "permissions": COMPANY_VISIBLE,
    }


def build_procedure_doc(name: str, reviewers: list[dict]) -> dict:
    lines = [
        f"Safety Procedure: {name}",
        "",
        f"Reviewed by {len(reviewers)} worker(s):",
        "",
    ]
    for r in sorted(reviewers, key=lambda x: x["name"]):
        lines.append(f"  - {r['name']} ({r['position']}, {r['location']})")
    return {
        "id": stable_id("procedure", name),
        "datasource": GLEAN_DATASOURCE,
        "objectType": "SafetyProcedure",
        "title": f"Procedure: {name}",
        "viewURL": f"{WORKHUB_WEB_BASE}/admin/procedures",
        "body": {"mimeType": "text/plain", "textContent": "\n".join(lines)},
        "updatedAt": now_epoch(),
        "permissions": COMPANY_VISIBLE,
    }


# ---------------------------------------------------------------------------
# AGGREGATION -- invert worker->policy data into policy->worker
# ---------------------------------------------------------------------------
def invert_policies(workers: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for w in workers:
        person = _person_summary(w["basic"])
        for p in w["policies"]:
            out[p].append(person)
    return out


def invert_procedures(workers: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for w in workers:
        person = _person_summary(w["basic"])
        for p in w["procedures"]:
            out[p].append(person)
    return out


# ---------------------------------------------------------------------------
# GLEAN SIDE -- bulk indexing (atomic replace)
# ---------------------------------------------------------------------------
def _glean_url(path: str) -> str:
    return f"https://{GLEAN_INSTANCE}-be.glean.com/api/index/v1/{path}"


def _glean_post(path: str, body: dict) -> None:
    headers = {
        "Authorization": f"Bearer {GLEAN_INDEXING_API_TOKEN}",
        "Content-Type": "application/json",
    }
    r = HTTP.post(_glean_url(path), json=body, headers=headers, timeout=60)
    if not r.ok:
        log.error("Glean %s failed [%s]: %s", path, r.status_code, r.text[:500])
        r.raise_for_status()


def bulk_index(documents: list[dict], batch_size: int = 50) -> None:
    """Atomic full-replace of the datasource. Anything not present in this
    upload is removed from Glean on commit (last page). Safe to run on a
    schedule -- departed workers and renamed items disappear automatically.
    """
    if not documents:
        log.warning("Nothing to index.")
        return

    upload_id = (
        f"sync-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f"-{uuid.uuid4().hex[:8]}"
    )
    pages = [documents[i:i + batch_size] for i in range(0, len(documents), batch_size)]
    log.info(
        "Bulk indexing %d documents in %d page(s) (uploadId=%s)",
        len(documents), len(pages), upload_id,
    )

    for i, page in enumerate(pages):
        body = {
            "uploadId": upload_id,
            "isFirstPage": i == 0,
            "isLastPage": i == len(pages) - 1,
            "forceRestartUpload": i == 0,
            "datasource": GLEAN_DATASOURCE,
            "documents": page,
        }
        _glean_post("bulkindexdocuments", body)
        log.info("  page %d/%d sent (%d docs)", i + 1, len(pages), len(page))


# ---------------------------------------------------------------------------
# RUN MODES
# ---------------------------------------------------------------------------
def _print_doc(label: str, doc: dict) -> None:
    print(f"\n--- {label} ---")
    print(f"id:    {doc['id']}")
    print(f"title: {doc['title']}")
    print(f"url:   {doc['viewURL']}")
    print("body:")
    for line in doc["body"]["textContent"].splitlines():
        print(f"  {line}")


def run_test(limit: int) -> None:
    """Dry run: pull a small sample, print the docs we'd push, push nothing."""
    check_environment()
    workers = gather_all(limit=limit)
    if not workers:
        log.error("No workers gathered. Check URLs and credentials.")
        return

    log.info("--- TEST MODE: not pushing to Glean ---")

    print(f"\n=== Worker docs ({len(workers)}) ===")
    for w in workers:
        _print_doc("worker", build_worker_doc(w))

    pmap = invert_policies(workers)
    print(f"\n=== Policy docs (showing first 3 of {len(pmap)} from sample) ===")
    for name in list(pmap.keys())[:3]:
        _print_doc("policy", build_policy_doc(name, pmap[name]))

    qmap = invert_procedures(workers)
    print(f"\n=== Procedure docs (showing first 3 of {len(qmap)} from sample) ===")
    if not qmap:
        print("(none in this sample)")
    for name in list(qmap.keys())[:3]:
        _print_doc("procedure", build_procedure_doc(name, qmap[name]))

    log.info("Test complete. Run with --sync to push for real.")


def run_sync(limit: int | None = None) -> None:
    """Real sync: every active worker -> all 4 APIs -> 3 doc types -> Glean."""
    check_environment()

    log.info("=" * 60)
    log.info("PASS 1: Gathering data from Workhub")
    log.info("=" * 60)
    workers = gather_all(limit=limit)
    if not workers:
        log.error("No workers gathered. Aborting sync.")
        return

    log.info("=" * 60)
    log.info("PASS 2: Building Glean documents")
    log.info("=" * 60)
    worker_docs = [build_worker_doc(w) for w in workers]
    pmap = invert_policies(workers)
    qmap = invert_procedures(workers)
    policy_docs = [build_policy_doc(n, a) for n, a in pmap.items()]
    proc_docs = [build_procedure_doc(n, r) for n, r in qmap.items()]
    all_docs = worker_docs + policy_docs + proc_docs
    log.info(
        "Built %d docs (%d worker, %d policy, %d procedure)",
        len(all_docs), len(worker_docs), len(policy_docs), len(proc_docs),
    )

    log.info("=" * 60)
    log.info("PASS 3: Pushing to Glean (atomic bulk replace)")
    log.info("=" * 60)
    bulk_index(all_docs)

    log.info("=" * 60)
    log.info("Sync complete. Indexed %d documents into Glean.", len(all_docs))
    log.info("  - %d worker compliance cards", len(worker_docs))
    log.info("  - %d policy cards", len(policy_docs))
    log.info("  - %d procedure cards", len(proc_docs))
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Workhub data into Glean")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--test", action="store_true",
        help="Dry run: fetch a small sample, print docs, do NOT push to Glean.",
    )
    group.add_argument(
        "--sync", action="store_true",
        help="Real sync: bulk-replace the Workhub datasource in Glean.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap workers fetched (default: 2 in --test, all in --sync).",
    )
    args = parser.parse_args()

    if args.test:
        run_test(limit=args.limit if args.limit is not None else 2)
    else:
        run_sync(limit=args.limit)


if __name__ == "__main__":
    main()
