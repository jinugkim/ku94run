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
WEEKDAY_HEADERS = {"월", "화", "수", "목", "금", "토", "일"}


def summarize_records(records: list[dict]) -> str:
    runners = [
        row
        for row in records
        if str(row.get("이름", "")).strip() and _run_count(row) > 0
    ]
    ranked_runners = _rank_runners(runners)

    lines = ["🏃 KU94 RUN 100일 챌린지 오늘의 기록"]
    top_three = [(runner, rank) for runner, rank in ranked_runners if rank <= 3]
    if top_three:
        lines.append("오늘의 TOP 3")
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for runner, rank in top_three:
            finish_day = runner.get("완주일차")
            finish_note = f" · {finish_day}일차 완주" if finish_day else ""
            lines.append(
                f"{medals[rank]} {str(runner['이름']).strip()} · {_run_count(runner)}회{finish_note}"
            )
    else:
        lines.append("아직 기록이 없습니다.")

    milestone_count = sum(1 for runner, _ in ranked_runners if _run_count(runner) >= 100)
    lines.append(f"🏆 현재 100회 완주자 {milestone_count}명!")
    return "\n".join(lines)


def _rank_runners(runners: list[dict]) -> list[tuple[dict, int]]:
    ordered = sorted(runners, key=_ranking_key)
    finished_total = sum(1 for runner in ordered if _run_count(runner) >= 100)
    finished_position = 0
    unfinished_position = 0
    previous_finish_day = None
    previous_rank = None
    ranked = []

    for runner in ordered:
        if _run_count(runner) >= 100:
            finished_position += 1
            finish_day = runner.get("완주일차")
            if finish_day is not None and finish_day == previous_finish_day:
                rank = previous_rank
            else:
                rank = finished_position
            previous_finish_day = finish_day
            previous_rank = rank
        else:
            unfinished_position += 1
            rank = finished_total + unfinished_position
        ranked.append((runner, rank))
    return ranked


def _run_count(record: dict) -> float:
    try:
        value = record.get("달리기 횟수 ", record.get("달리기 횟수", 0))
        return min(100, max(0, float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _rest_count(record: dict) -> float:
    try:
        return max(0, float(record.get("휴식 횟수", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _ranking_key(record: dict) -> tuple:
    runs = _run_count(record)
    if runs >= 100:
        finish_day = record.get("완주일차")
        return (0, finish_day if finish_day is not None else float("inf"), _rest_count(record))
    return (1, -runs, _rest_count(record))


def _is_run_mark(value) -> bool:
    text = str(value or "").strip()
    if not text or text.lower() in {"x", "×", "휴식", "-", "0", "n", "no"}:
        return False
    if text.upper() in {"O", "○", "⭕", "✓", "✔", "✅", "Y", "YES", "RUN"}:
        return True
    try:
        return float(text.replace(",", "")) > 0
    except ValueError:
        return bool(re.fullmatch(r"\d+(?:\.\d+)?\s*(?:km|키로|킬로)", text, re.IGNORECASE))


def read_sheet_records(worksheet) -> list[dict]:
    """Preserve repeated weekday columns so each challenge day stays in order."""
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

    # get_all_records() collapses repeated weekday headers into one dictionary
    # key. Keep each of the 107 daily columns under its own ordered key instead.
    record_headers = []
    day_number = 0
    header_counts = {}
    for column_number, raw_header in enumerate(raw_headers, start=1):
        header = str(raw_header).strip()
        if header in WEEKDAY_HEADERS:
            day_number += 1
            record_headers.append(f"챌린지 {day_number}일")
            continue
        if header.replace(" ", "") == "달리기횟수":
            header = "달리기 횟수 "
        elif header == "이름":
            header = "이름"
        elif header == "휴식횟수":
            header = "휴식 횟수"
        if not header:
            header = f"column_{column_number}"
        count = header_counts.get(header, 0) + 1
        header_counts[header] = count
        record_headers.append(header if count == 1 else f"{header}_{count}")

    records = []
    for row in values[header_row_number:]:
        padded_row = row + [""] * max(0, len(record_headers) - len(row))
        records.append(dict(zip(record_headers, padded_row)))

    for record in records:
        raw_run_count = _run_count(record)
        daily_keys = sorted(
            (key for key in record if key.startswith("챌린지 ") and key.endswith("일")),
            key=lambda key: int(key.split()[1][:-1]),
        )
        marked_run_count = 0
        finish_day = None
        for day_index, key in enumerate(daily_keys, start=1):
            if _is_run_mark(record.get(key)):
                marked_run_count += 1
                if marked_run_count == 100:
                    finish_day = day_index
                    break

        capped_run_count = min(100, max(raw_run_count, marked_run_count))
        capped_run_count = float(capped_run_count)
        record["달리기 횟수 "] = int(capped_run_count) if capped_run_count.is_integer() else capped_run_count
        record["완주일차"] = finish_day
        if capped_run_count >= 100 and finish_day is not None:
            # A completed runner's rest allowance is frozen on their 100th run.
            record["휴식 횟수"] = max(0, finish_day - 100)

    return records


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
