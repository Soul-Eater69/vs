"""Build value-stream stage ground truth from linked Jira Theme tickets."""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vs_app.modules.stages.stage_catalog import load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import build_ticket_stage_ground_truth


DEFAULT_GT_CONCURRENCY = 5


class JiraApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        verify_ssl: bool = False,
        debug: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = auth_headers(token)
        self.verify_ssl = verify_ssl
        self.debug = debug
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "JiraApiClient":
        self._client = httpx.AsyncClient(
            headers=self.headers,
            verify=self.verify_ssl,
            timeout=60.0,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_issue(
        self,
        issue_key: str,
        *,
        fields: list[str] | None = None,
        expand: bool = True,
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("JiraApiClient must be used as an async context manager")
        params: dict[str, str] = {}
        if fields:
            params["fields"] = ",".join(fields)
        if expand:
            params["expand"] = "names,schema"
        if self.debug:
            print(
                f"[jira] GET issue {issue_key} "
                f"fields={fields or 'all'} expand={expand} timeout=60s"
            )
        try:
            response = await self._client.get(
                f"{self.base_url}/rest/api/2/issue/{issue_key}",
                params=params,
            )
            response.raise_for_status()
        except Exception as exc:
            if self.debug:
                print(f"[jira] GET issue {issue_key} failed: {type(exc).__name__}: {exc}")
            raise
        if self.debug:
            print(f"[jira] GET issue {issue_key} -> HTTP {response.status_code}")
        return response.json()

    async def search_issues(
        self,
        jql: str,
        *,
        start_at: int = 0,
        max_results: int = 100,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("JiraApiClient must be used as an async context manager")
        params = {
            "jql": jql,
            "startAt": str(start_at),
            "maxResults": str(max_results),
        }
        if fields:
            params["fields"] = ",".join(fields)
        if self.debug:
            print(
                f"[jira] SEARCH jql={jql!r} start_at={start_at} "
                f"max_results={max_results} fields={fields or 'default'} timeout=60s"
            )
        try:
            response = await self._client.get(
                f"{self.base_url}/rest/api/2/search",
                params=params,
            )
            response.raise_for_status()
        except Exception as exc:
            if self.debug:
                print(f"[jira] SEARCH failed for jql={jql!r}: {type(exc).__name__}: {exc}")
            raise
        if self.debug:
            print(f"[jira] SEARCH jql={jql!r} -> HTTP {response.status_code}")
        return response.json()

    async def download_attachment(self, url_or_att: Any, dest_path: str = "") -> bytes:
        if self._client is None:
            raise RuntimeError("JiraApiClient must be used as an async context manager")
        url = attachment_url(url_or_att)
        if not url:
            raise ValueError("Attachment payload does not include a content URL")
        if self.debug:
            print(f"[jira] GET attachment {url}")
        response = await self._client.get(url)
        response.raise_for_status()
        return response.content


def auth_headers(token: str) -> dict[str, str]:
    token = str(token or "").strip()
    if token.lower().startswith(("bearer ", "basic ")):
        authorization = token
    else:
        authorization = f"Bearer {token}"
    return {
        "Accept": "application/json",
        "Authorization": authorization,
    }


def attachment_url(url_or_att: Any) -> str:
    if isinstance(url_or_att, str):
        return url_or_att
    if isinstance(url_or_att, dict):
        for key in ("content", "url", "self"):
            value = str(url_or_att.get(key) or "").strip()
            if value:
                return value
    return ""


def main() -> int:
    return asyncio.run(async_main())


async def async_main() -> int:
    args = parse_args()
    ticket_keys = load_ticket_keys(args)
    if not ticket_keys:
        raise SystemExit("No ticket keys provided. Use --ticket-id or --ticket-ids-file.")
    if not args.jira_base_url or not args.jira_token:
        raise SystemExit("Jira access requires JIRA_BASE_URL and JIRA_TOKEN or matching CLI flags.")

    catalog = load_stage_catalog(
        path=args.stage_catalog,
        source=args.stage_catalog_source,
    )

    async with JiraApiClient(
        base_url=args.jira_base_url,
        token=args.jira_token,
        verify_ssl=bool(args.verify_ssl),
        debug=bool(args.debug),
    ) as jira_client:
        tickets, errors = await build_stage_ground_truth_batch(
            ticket_keys=ticket_keys,
            jira_client=jira_client,
            catalog=catalog,
            concurrency=max(1, int(args.concurrency or DEFAULT_GT_CONCURRENCY)),
        )

    output = {
        "source": "jira",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "stage_catalog_source": args.stage_catalog_source,
        "stage_gt_lookup_strategy": "parent_link_only",
        "tickets": tickets,
        "errors": errors,
    }
    print(f"Writing output file: {args.output}")
    write_outputs(output, Path(args.output), debug=bool(args.debug))
    print(f"Wrote {len(tickets)} tickets to {args.output}")
    print(f"Errors: {len(errors)}")
    return 0


async def build_stage_ground_truth_batch(
    *,
    ticket_keys: list[str],
    jira_client: JiraApiClient,
    catalog: dict[str, Any],
    concurrency: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    total = len(ticket_keys)
    semaphore = asyncio.Semaphore(max(1, int(concurrency or DEFAULT_GT_CONCURRENCY)))

    async def build_one(index: int, ticket_key: str) -> dict[str, Any]:
        normalized_key = normalize_ticket_key(ticket_key)
        async with semaphore:
            print(f"[{index}/{total}] START {normalized_key}", flush=True)
            try:
                row = await build_ticket_stage_ground_truth(
                    ticket_key=normalized_key,
                    jira_client=jira_client,
                    catalog=catalog,
                )
                print_gt_ticket_status_done(index, total, normalized_key, row)
                return {"ticket_id": normalized_key, "row": row, "error": None}
            except Exception as exc:
                error = {
                    "ticket_id": normalized_key,
                    "error": compact_error(exc),
                }
                print(
                    f"[{index}/{total}] ERROR {normalized_key} | {compact_error(exc)}",
                    flush=True,
                )
                return {"ticket_id": normalized_key, "row": None, "error": error}

    results = await asyncio.gather(
        *(
            build_one(index, ticket_key)
            for index, ticket_key in enumerate(ticket_keys, start=1)
        )
    )
    result_by_ticket = {
        normalize_ticket_key(result["ticket_id"]): result
        for result in results
        if normalize_ticket_key(result.get("ticket_id"))
    }

    tickets: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    for ticket_key in ticket_keys:
        normalized_key = normalize_ticket_key(ticket_key)
        result = result_by_ticket.get(normalized_key)
        if not result:
            continue
        if result.get("row") is not None:
            tickets[normalized_key] = result["row"]
        if result.get("error") is not None:
            errors.append(result["error"])
    return tickets, errors


def print_gt_ticket_status_done(index: int, total: int, ticket_key: str, row: dict[str, Any]) -> None:
    gt_by_vs = row.get("gt_by_value_stream") or {}
    gt_stage_count = sum(len(stages or []) for stages in gt_by_vs.values())
    print(
        f"[{index}/{total}] DONE {ticket_key} | "
        f"gt_vs={len(gt_by_vs)} | "
        f"gt_stages={gt_stage_count} | "
        f"warnings={len(row.get('warnings') or [])}",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build stage ground truth from linked Jira Theme tickets.",
    )
    parser.add_argument(
        "--ticket-id",
        "--ticket",
        action="append",
        default=[],
        help="IDMT ticket key. Can be repeated.",
    )
    parser.add_argument(
        "--ticket-ids-file",
        "--tickets-file",
        help="Text, JSON, or CSV file with ticket IDs.",
    )
    parser.add_argument(
        "--stage-catalog",
        help="Path to JSON stage catalog when --stage-catalog-source=json.",
    )
    parser.add_argument(
        "--stage-catalog-source",
        choices=["json", "azure"],
        default="json",
        help="Approved stage catalog source.",
    )
    parser.add_argument(
        "--output",
        default="output/stage_eval/stage_ground_truth.json",
        help="Output JSON path. A sibling .jsonl file is also written.",
    )
    parser.add_argument("--jira-base-url", default=os.getenv("JIRA_BASE_URL"))
    parser.add_argument("--jira-token", default=os.getenv("JIRA_TOKEN"))
    parser.add_argument(
        "--verify-ssl",
        action="store_true",
        help="Verify Jira TLS certificates. Off by default.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print Jira calls, child lookup JQLs, and stage GT progress.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=env_int("STAGE_GT_CONCURRENCY", DEFAULT_GT_CONCURRENCY),
        help="Maximum concurrent IDMT tickets to process.",
    )
    return parser.parse_args()


def load_ticket_keys(args: argparse.Namespace) -> list[str]:
    keys = list(getattr(args, "ticket_id", None) or getattr(args, "ticket", None) or [])
    tickets_file = getattr(args, "ticket_ids_file", None) or getattr(args, "tickets_file", None)

    if tickets_file:
        path = Path(tickets_file)
        if not path.exists():
            raise SystemExit(f"--ticket-ids-file not found: {path}")
        suffix = path.suffix.lower()
        if suffix == ".json":
            keys.extend(_ticket_keys_from_json(json.loads(path.read_text(encoding="utf-8"))))
        elif suffix == ".csv":
            keys.extend(_ticket_keys_from_csv(path))
        else:
            keys.extend(_ticket_keys_from_text(path))

    return sorted({normalize_ticket_key(key) for key in keys if normalize_ticket_key(key)})


def write_outputs(payload: dict[str, Any], output_path: Path, *, debug: bool = False) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if debug:
        print(f"[stage-gt] writing JSON: {output_path}")
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    jsonl_path = output_path.with_suffix(".jsonl")
    if debug:
        print(f"[stage-gt] writing JSONL: {jsonl_path}")
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for ticket_key, ticket in (payload.get("tickets") or {}).items():
            fh.write(
                json.dumps({"ticket_id": ticket_key, **ticket}, ensure_ascii=False) + "\n"
            )


def _ticket_keys_from_json(payload: Any) -> list[str]:
    if isinstance(payload, list):
        return _ticket_keys_from_sequence(payload)
    if isinstance(payload, dict):
        for key in ("tickets", "results", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return _ticket_keys_from_sequence(value)
        direct = payload.get("ticket_id") or payload.get("ticket_key") or payload.get("key")
        return [str(direct)] if direct else []
    return []


def _ticket_keys_from_sequence(values: list[Any]) -> list[str]:
    keys: list[str] = []
    for value in values:
        if isinstance(value, str):
            keys.extend(_split_ticket_text(value))
        elif isinstance(value, dict):
            key = value.get("ticket_id") or value.get("ticket_key") or value.get("key")
            if key:
                keys.append(str(key))
    return keys


def _ticket_keys_from_csv(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return []
        preferred = ("ticket_id", "ticket_key", "key")
        field = next((name for name in preferred if name in reader.fieldnames), reader.fieldnames[0])
        return [str(row.get(field) or "") for row in reader]


def _ticket_keys_from_text(path: Path) -> list[str]:
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        keys.extend(_split_ticket_text(line))
    return keys


def _split_ticket_text(value: str) -> list[str]:
    return [part for part in re.split(r"[\s,]+", str(value or "").strip()) if part]


def normalize_ticket_key(value: Any) -> str:
    return str(value or "").strip().upper()


def compact_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    raise SystemExit(main())
