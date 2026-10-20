"""Export the current challenge sheet and update the archive index."""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

import gspread
import requests


DATA_DIR = Path("data")
SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
DISPATCH_EVENT_TYPE = "ku94_run_daily_report"


def summarize_records(records: list[dict]) -> str:
    runners = [
        row
        for row in records
        if str(row.get("이름", "")).strip() and _run_count(row) > 0
    ]
    runners.sort(key=_run_count, reverse=True)

    lines = ["🏃 KU94 RUN 100일 챌린지 오늘의 기록"]
    if runners:
        lines.append("오늘의 TOP 3")
        medals = ("🥇", "🥈", "🥉")
        for rank, runner in enumerate(runners[:3]):
            lines.append(
                f"{medals[rank]} {str(runner['이름']).strip()} · {_run_count(runner)}회"
            )
    else:
        lines.append("아직 기록이 없습니다.")

    milestone_count = sum(1 for runner in runners if _run_count(runner) >= 100)
    lines.append(f"🏆 현재 100회 완주자 {milestone_count}명!")
    return "\n".join(lines)


def _run_count(record: dict) -> float:
    try:
        value = record.get("달리기 횟수 ", record.get("달리기 횟수", 0))
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def read_sheet_records(worksheet) -> list[dict]:
    """Find the actual table header, then read records with unique column names."""
    values = worksheet.get_all_values()
    header_row_number = None
    raw_headers = None
    for row_number, row in enumerate(values, start=1):
        normalized = {str(cell).strip() for cell in row if str(cell).strip()}
        if "이름" in normalized and any(
            header.replace(" ", "") == "달리기횟수" for header in normalized
        ):
            header_row_number = row_number
            raw_headers = row
            break

    if header_row_number is None or raw_headers is None:
        raise ValueError(
            "첫 번째 시트에서 '이름'과 '달리기 횟수'가 있는 헤더 행을 찾지 못했습니다."
        )

    expected_headers = []
    header_counts = {}
    for column_number, raw_header in enumerate(raw_headers, start=1):
        header = str(raw_header).strip() or f"column_{column_number}"
        count = header_counts.get(header, 0) + 1
        header_counts[header] = count
        if count > 1:
            header = f"{header}_{count}"
        expected_headers.append(header)

    return worksheet.get_all_records(
        head=header_row_number,
        expected_headers=expected_headers,
    )


def dispatch_kakao_message(message: str) -> None:
    token = os.environ.get("COURIER_PAT")
    courier_owner = os.environ.get("COURIER_OWNER")
    missing = [
        name
        for name, value in (("COURIER_PAT", token), ("COURIER_OWNER", courier_owner))
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required environment variable(s): {', '.join(missing)}")

    response = requests.post(
        f"https://api.github.com/repos/{courier_owner}/courier/dispatches",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"token {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "event_type": DISPATCH_EVENT_TYPE,
            "client_payload": {"message": message},
        },
        timeout=20,
    )
    response.raise_for_status()


def main() -> None:
    credentials_json = os.environ.get("GCP_CREDENTIALS")
    sheet_url = os.environ.get("CURRENT_SHEET_URL")
    start_date = os.environ.get("START_DATE")

    missing = [
        name
        for name, value in (
            ("GCP_CREDENTIALS", credentials_json),
            ("CURRENT_SHEET_URL", sheet_url),
            ("START_DATE", start_date),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required environment variable(s): {', '.join(missing)}")

    year_match = re.match(r"^\s*(\d{4})", start_date)
    if not year_match:
        raise ValueError("START_DATE must begin with a four-digit year (for example, 2026-01-01)")
    year = year_match.group(1)

    sheet_match = SHEET_ID_PATTERN.search(sheet_url)
    if not sheet_match:
        raise ValueError("CURRENT_SHEET_URL does not contain a valid Google Spreadsheet ID")

    try:
        credentials = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise ValueError("GCP_CREDENTIALS must contain valid service account JSON") from exc

    client = gspread.service_account_from_dict(credentials)
    spreadsheet = client.open_by_key(sheet_match.group(1))
    worksheet = spreadsheet.get_worksheet(0)
    records = read_sheet_records(worksheet)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{year}.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    meta_path = DATA_DIR / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{meta_path} is not valid JSON") from exc
    else:
        meta = {"years": []}

    years = {str(item) for item in meta.get("years", [])}
    years.add(year)
    meta["years"] = sorted(years, key=int, reverse=True)
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Exported {len(records)} records to data/{year}.json")

    summary = summarize_records(records)
    print("Dispatching daily summary to courier.")
    dispatch_kakao_message(summary)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error: {type(error).__name__}: {error!r}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
