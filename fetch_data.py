"""Export the current challenge sheet and update the archive index."""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
import requests


DATA_DIR = Path("data")
SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
DISPATCH_EVENT_TYPE = "send-kakao-msg"
COURIER_PROJECT = "KU94 RUN 100일 챌린지"
COURIER_RECIPIENTS = ["ME"]
UNOFFICIAL_FINISHERS_BY_YEAR = {"2026": ["김상종"]}
CHALLENGE_CALENDAR_DAYS = 107  # 100 runs plus up to 7 rest days.
WEEKDAY_HEADERS = {"월", "화", "수", "목", "금", "토", "일"}


def summarize_records(
    records: list[dict],
    *,
    challenge_ended: bool = False,
    year: str | None = None,
    challenge_day: int | None = None,
) -> str:
    runners = [
        row
        for row in records
        if str(row.get("이름", "")).strip() and _run_count(row) > 0
    ]
    ranked_runners = _rank_runners(runners)

    if challenge_ended:
        official_finishers = sorted(
            str(runner["이름"]).strip()
            for runner, _ in ranked_runners
            if _run_count(runner) >= 100
        )
        year_key = str(year or "")
        unofficial_finishers = sorted(
            name for name in UNOFFICIAL_FINISHERS_BY_YEAR.get(year_key, [])
            if name not in official_finishers
        )
        lines = [f"🏁 KU94 RUN {year_key} 100일 챌린지 완주자"]
        lines.append(f"✅ 공식 완주자 {len(official_finishers)}명")
        lines.extend(_format_name_groups(official_finishers))
        if unofficial_finishers:
            lines.append("🎖️ 비공식 완주자")
            lines.extend(_format_name_groups(unofficial_finishers))
        return "\n".join(lines)

    if challenge_day is None or challenge_day < 1:
        raise ValueError("challenge_day must be a positive challenge-day number")
    day_key = f"챌린지 {challenge_day}일"
    daily_finishers = sorted(
        (
            row for row in records
            if str(row.get("이름", "")).strip() and _is_run_mark(row.get(day_key))
        ),
        key=lambda row: str(row["이름"]).strip(),
    )
    current_achievers = sorted(
        str(row["이름"]).strip()
        for row in records
        if str(row.get("이름", "")).strip()
        and _run_count(row) >= 100
        and row.get("완주일차") is not None
        and row["완주일차"] <= challenge_day
    )
    daily_names = [str(row["이름"]).strip() for row in daily_finishers]
    lines = [f"🏃 KU94 RUN 100일 챌린지 · {challenge_day}일차 기록"]
    if current_achievers:
        lines.append(f"🏆 현재 챌린지 달성자 {len(current_achievers)}명")
        lines.extend(_format_name_groups(current_achievers))
    if daily_names:
        lines.append(f"✅ {challenge_day}일차 달리기 기록자 {len(daily_names)}명")
        lines.extend(_format_name_groups(daily_names))
    else:
        lines.append(f"아직 {challenge_day}일차 달리기 기록이 없습니다.")
    return "\n".join(lines)


def _format_name_groups(names: list[str], max_line_chars: int = 100) -> list[str]:
    """Group complete names into compact lines that Courier can split safely."""
    lines: list[str] = []
    group: list[str] = []
    group_length = 0
    for name in names:
        added_length = len(name) + (2 if group else 0)
        if group and group_length + added_length > max_line_chars:
            lines.append(", ".join(group))
            group = []
            group_length = 0
            added_length = len(name)
        group.append(name)
        group_length += added_length
    if group:
        lines.append(", ".join(group))
    if not lines:
        lines.append("아직 완주자가 없습니다.")
    return lines


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
        name = str(record.get("이름", "")).strip()
        return (
            0,
            finish_day if finish_day is not None else float("inf"),
            _rest_count(record),
            name,
        )
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


def _latest_recorded_challenge_day(records: list[dict]) -> int:
    """Find the latest non-empty day column when START_DATE contains only a year."""
    latest_day = 0
    for record in records:
        for key, value in record.items():
            match = re.fullmatch(r"챌린지 (\d+)일", str(key))
            if match and str(value or "").strip():
                latest_day = max(latest_day, int(match.group(1)))
    return latest_day


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
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    if not run_id:
        raise ValueError("GITHUB_RUN_ID is required to create a stable Courier request ID")
    request_id = f"ku94-run:{run_id}"

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
            "client_payload": {
                "request_id": request_id,
                "project": COURIER_PROJECT,
                "message": message,
                "recipients": COURIER_RECIPIENTS,
            },
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

    start_value = start_date.strip()
    challenge_start = None
    if re.fullmatch(r"\d{4}", start_value):
        # Existing repository configuration uses START_DATE as a year label.
        year = start_value
        challenge_ended = False  # Resolved from the final populated day column below.
        challenge_day = None
    else:
        try:
            challenge_start = date.fromisoformat(start_value[:10])
        except ValueError as exc:
            raise ValueError("START_DATE must be a year (2026) or ISO date (2026-01-01)") from exc
        year = str(challenge_start.year)
        last_challenge_day = challenge_start + timedelta(days=CHALLENGE_CALENDAR_DAYS - 1)
        today_kst = datetime.now(ZoneInfo("Asia/Seoul")).date()
        challenge_ended = today_kst > last_challenge_day
        challenge_day = (today_kst - challenge_start).days + 1

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
    if challenge_start is None:
        challenge_day = _latest_recorded_challenge_day(records)
        if challenge_day < 1:
            raise ValueError("No populated challenge-day columns were found in the current sheet")
        challenge_ended = challenge_day >= CHALLENGE_CALENDAR_DAYS

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
    completed_years = {str(item) for item in meta.get("completed_years", [])}
    if challenge_ended:
        completed_years.add(year)
    else:
        completed_years.discard(year)
    unofficial_finishers = meta.get("unofficial_finishers", {})
    if not isinstance(unofficial_finishers, dict):
        unofficial_finishers = {}
    for unofficial_year, names in UNOFFICIAL_FINISHERS_BY_YEAR.items():
        current_names = set(unofficial_finishers.get(unofficial_year, []))
        current_names.update(names)
        unofficial_finishers[unofficial_year] = sorted(current_names)
    upcoming_years = {str(item) for item in meta.get("upcoming_years", [])}
    if challenge_ended:
        upcoming_years.add(str(int(year) + 1))
    else:
        upcoming_years.discard(year)
    upcoming_years.difference_update(years)
    meta["years"] = sorted(years, key=int, reverse=True)
    meta["completed_years"] = sorted(completed_years, key=int, reverse=True)
    meta["upcoming_years"] = sorted(upcoming_years, key=int, reverse=True)
    meta["unofficial_finishers"] = unofficial_finishers
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Exported {len(records)} records to data/{year}.json")

    summary = summarize_records(
        records,
        challenge_ended=challenge_ended,
        year=year,
        challenge_day=challenge_day,
    )
    print("Dispatching daily summary to courier.")
    dispatch_kakao_message(summary)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error: {type(error).__name__}: {error!r}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
