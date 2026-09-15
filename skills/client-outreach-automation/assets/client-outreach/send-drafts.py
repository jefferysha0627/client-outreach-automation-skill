#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
SENT_FIELDNAMES = [
    "sent_at",
    "draft_id",
    "message_id",
    "thread_id",
    "email",
    "店家名稱",
    "subject",
    "status",
    "error",
]
DONE_STATUSES = {"已送出", "草稿不存在（可能已送出或刪除）"}


def load_outreach_module() -> Any:
    module_path = BASE_DIR / "run-outreach.py"
    spec = importlib.util.spec_from_file_location("client_outreach_run_outreach", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"無法載入：{module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


outreach = load_outreach_module()


class GmailDraftSender(outreach.GmailDraftClient):
    def list_draft_ids(self) -> set[str]:
        draft_ids: set[str] = set()
        page_token = ""

        while True:
            query = {"maxResults": "500"}
            if page_token:
                query["pageToken"] = page_token
            url = (
                f"https://gmail.googleapis.com/gmail/v1/users/{urllib.parse.quote(self.user_id)}/drafts?"
                + urllib.parse.urlencode(query)
            )
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self.access_token()}"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Gmail 讀取草稿清單失敗：{detail}") from exc

            draft_ids.update(str(draft.get("id", "")).strip() for draft in data.get("drafts", []) if draft.get("id"))
            page_token = str(data.get("nextPageToken", "")).strip()
            if not page_token:
                return draft_ids

    def send_draft(self, draft_id: str) -> dict[str, Any]:
        payload = json.dumps({"id": draft_id}).encode("utf-8")
        url = f"https://gmail.googleapis.com/gmail/v1/users/{urllib.parse.quote(self.user_id)}/drafts/send"
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404:
                raise DraftNotFound(detail) from exc
            raise RuntimeError(f"Gmail 送出草稿失敗：{detail}") from exc


class DraftNotFound(RuntimeError):
    pass


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_sent_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SENT_FIELDNAMES)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in SENT_FIELDNAMES})


def sent_draft_ids(sent_rows: list[dict[str, str]]) -> set[str]:
    return {
        str(row.get("draft_id", "")).strip()
        for row in sent_rows
        if str(row.get("draft_id", "")).strip() and str(row.get("status", "")).strip() in DONE_STATUSES
    }


def pending_drafts(
    history_rows: list[dict[str, str]],
    done_ids: set[str],
    selected_ids: set[str] | None,
) -> list[dict[str, str]]:
    pending: list[dict[str, str]] = []
    seen: set[str] = set()

    for row in history_rows:
        draft_id = str(row.get("draft_id", "")).strip()
        if not draft_id or draft_id in done_ids or draft_id in seen:
            continue
        if selected_ids is not None and draft_id not in selected_ids:
            continue
        pending.append(row)
        seen.add(draft_id)

    return pending


def print_preview(rows: list[dict[str, str]], limit: int) -> None:
    print(f"待送出草稿數：{len(rows)}")
    if not rows:
        return

    print("\n前 20 筆：")
    for row in rows[:20]:
        name = row.get("店家名稱") or "(未命名)"
        email = row.get("email") or "(無 Email)"
        subject = row.get("subject") or "(無主旨)"
        draft_id = row.get("draft_id") or ""
        print(f"  - {name} <{email}> | {subject} | draft_id={draft_id}")

    if len(rows) > limit:
        print(f"\n本次上限：{limit}，其餘 {len(rows) - limit} 筆會保留到下次。")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="將 outreach-history.csv 內尚未送出的 Gmail 草稿一鍵送出。")
    parser.add_argument("--config", default=str(BASE_DIR / "config.json"), help="config.json 路徑")
    parser.add_argument("--confirm", action="store_true", help="確認送出；沒有加這個參數時只會預覽")
    parser.add_argument("--all", action="store_true", help="送出所有待送出草稿，不套用 send_batch_size")
    parser.add_argument("--limit", type=int, help="本次最多送出幾封；預設讀取 config.send_batch_size")
    parser.add_argument("--draft-id", action="append", help="只送出指定 draft_id；可重複指定")
    parser.add_argument("--delay", type=float, default=0.3, help="每封送出後等待秒數，預設 0.3")
    parser.add_argument("--skip-live-check", action="store_true", help="不要向 Gmail 即時確認草稿是否仍存在")
    parser.add_argument("--check-setup", action="store_true", help="檢查 Gmail OAuth 是否可用")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    config = outreach.load_config(Path(args.config).resolve())
    env_file = outreach.load_env_file(config)

    if args.check_setup:
        print("Gmail OAuth 檢查：")
        if env_file is None:
            print("  - 缺少 .env，請先執行：python3 client-outreach/setup-gmail-oauth.py")
            return 2
        try:
            GmailDraftSender.from_env(config).access_token()
        except Exception as exc:  # noqa: BLE001 - setup checks should report the exact failure.
            print(f"  - 授權測試失敗：{exc}")
            return 2
        print("  - Gmail compose 授權可用")
        return 0

    history_file = outreach.resolve_path(config, "history_file")
    sent_history_file = outreach.resolve_path(config, "sent_history_file")
    history_rows = read_rows(history_file)
    sent_rows = read_rows(sent_history_file)
    selected_ids = {draft_id.strip() for draft_id in args.draft_id if draft_id.strip()} if args.draft_id else None
    local_pending = pending_drafts(history_rows, sent_draft_ids(sent_rows), selected_ids)

    gmail_client: GmailDraftSender | None = None
    if args.skip_live_check:
        pending = local_pending
        live_draft_ids: set[str] | None = None
    else:
        try:
            gmail_client = GmailDraftSender.from_env(config)
            live_draft_ids = gmail_client.list_draft_ids()
        except Exception as exc:  # noqa: BLE001 - live check is the safety gate before sending.
            print(f"Gmail 即時草稿檢查失敗：{exc}", file=sys.stderr)
            print("若只想看本機歷史紀錄，可加上 --skip-live-check。", file=sys.stderr)
            return 2
        pending = [row for row in local_pending if str(row.get("draft_id", "")).strip() in live_draft_ids]

    if args.all:
        send_limit = len(pending)
    elif args.limit is not None:
        send_limit = args.limit
    else:
        send_limit = int(config.get("send_batch_size", 50))

    if send_limit < 0:
        raise SystemExit("--limit 不可小於 0")

    to_send = pending[:send_limit]
    print(f"本機尚未標記送出的草稿紀錄：{len(local_pending)}")
    if live_draft_ids is not None:
        print(f"Gmail 目前實際草稿數：{len(live_draft_ids)}")
        print(f"本機紀錄中仍存在於 Gmail 的待送草稿：{len(pending)}")
        stale_count = len(local_pending) - len(pending)
        if stale_count:
            print(f"已忽略本機舊草稿紀錄：{stale_count}")
        print()
    print_preview(pending, send_limit)

    if not args.confirm:
        print("\n目前只預覽，尚未送出。確認後請改用：")
        if args.all:
            print(f"python3 {Path(__file__).resolve()} --all --confirm")
        else:
            print(f"python3 {Path(__file__).resolve()} --confirm")
        return 0

    if not to_send:
        print("\n沒有待送出的草稿。")
        return 0

    try:
        if gmail_client is None:
            gmail_client = GmailDraftSender.from_env(config)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    sent_results: list[dict[str, str]] = []
    for row in to_send:
        draft_id = str(row.get("draft_id", "")).strip()
        result_row = {
            "sent_at": outreach.timestamp(),
            "draft_id": draft_id,
            "message_id": "",
            "thread_id": "",
            "email": str(row.get("email", "")),
            "店家名稱": str(row.get("店家名稱", "")),
            "subject": str(row.get("subject", "")),
            "status": "",
            "error": "",
        }
        try:
            message = gmail_client.send_draft(draft_id)
            result_row["message_id"] = str(message.get("id", ""))
            result_row["thread_id"] = str(message.get("threadId", ""))
            result_row["status"] = "已送出"
        except DraftNotFound as exc:
            result_row["status"] = "草稿不存在（可能已送出或刪除）"
            result_row["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - keep row-level failure in the sent history.
            result_row["status"] = "送出失敗"
            result_row["error"] = str(exc)
        sent_results.append(result_row)
        time.sleep(args.delay)

    write_sent_rows(sent_history_file, sent_results)
    success_count = sum(1 for row in sent_results if row["status"] == "已送出")
    not_found_count = sum(1 for row in sent_results if row["status"].startswith("草稿不存在"))
    failed_count = sum(1 for row in sent_results if row["status"] == "送出失敗")

    print("\n送出結果：")
    print(f"  - 已送出：{success_count}")
    print(f"  - 草稿不存在：{not_found_count}")
    print(f"  - 送出失敗：{failed_count}")
    print(f"紀錄檔：{sent_history_file}")
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
