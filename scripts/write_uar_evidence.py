"""Append a Workbench user-access-review screenshot and result rows to Google Sheets.

One-time setup is documented in apps-script/WriteEvidence.gs. Save the OAuth
desktop client as credentials.json in the project root, then run:

    python scripts/write_uar_evidence.py --auth-only

Personas and SOQL live on the master sheet tab named by personaSheet
(default Personas). The columns are Persona and SOQL.
List them with:

    python scripts/write_uar_evidence.py --list-personas

Each prompt creates a new evidence spreadsheet, with one tab per persona:

    python scripts/write_uar_evidence.py --create-spreadsheet --persona "Sales Manager"

Evidence runs read config/uar.json and a payload JSON file. The payload
spreadsheetId is the new spreadsheet, not the master catalog.

    python scripts/write_uar_evidence.py --payload evidence/run.json
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "uar.json"
CREDENTIALS_PATH = ROOT / "credentials.json"
TOKEN_PATH = ROOT / "token.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
PLACEHOLDERS = {"", "PASTE_SHEET_ID", "PASTE_APPS_SCRIPT_ID"}
MAX_PNG_BYTES = 2 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auth-only",
        action="store_true",
        help="Complete Google consent and save token.json, then exit.",
    )
    parser.add_argument(
        "--list-personas",
        action="store_true",
        help="Print personas and SOQL from the master sheet tab.",
    )
    parser.add_argument(
        "--create-spreadsheet",
        action="store_true",
        help="Create a new evidence spreadsheet with one tab per --persona.",
    )
    parser.add_argument(
        "--persona",
        action="append",
        dest="persona_names",
        help="Persona tab to create. Repeat for each persona in this prompt.",
    )
    parser.add_argument(
        "--title",
        help="Title for the new evidence spreadsheet.",
    )
    parser.add_argument(
        "--payload",
        help="JSON file with spreadsheetId, persona, queryName, soql, headers, rows, screenshot, page, and pageCount.",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Run the latest saved Apps Script instead of the API executable deployment.",
    )
    args = parser.parse_args(argv)

    if args.auth_only:
        get_credentials()
        print(f"Google token saved to {TOKEN_PATH}")
        return 0

    if args.create_spreadsheet:
        if not args.persona_names:
            parser.error("--create-spreadsheet requires at least one --persona")
        json.dump(create_evidence_spreadsheet(args.persona_names, args.title), sys.stdout, indent=2)
        print()
        return 0

    if not args.list_personas and not args.payload:
        parser.error(
            "--payload is required unless --auth-only, --list-personas, or --create-spreadsheet is set"
        )

    config = load_json(CONFIG_PATH)
    _master_tab, master_rows = fetch_persona_rows(config)
    catalog = group_personas(master_rows)
    if args.list_personas:
        json.dump({"personas": catalog}, sys.stdout, indent=2)
        print()
        return 0

    payload_path = resolve_path(args.payload)
    payload = load_json(payload_path)
    spreadsheet_id = evidence_spreadsheet_id(payload, config)
    script_id = required_setting(config, "scriptId")
    persona_name, tab, query = resolve_query(
        master_rows,
        required_text(payload, "persona"),
        required_text(payload, "queryName"),
    )
    soql = required_text(payload, "soql")
    if " ".join(soql.split()) != " ".join(query["soql"].split()):
        raise SystemExit(
            f"Payload SOQL does not match the master sheet query {query['queryName']!r} "
            f"for persona {persona_name!r}."
        )

    screenshot_path = resolve_path(required_text(payload, "screenshot"))
    if not screenshot_path.is_file():
        raise SystemExit(f"Screenshot not found: {screenshot_path}")
    screenshot_bytes = screenshot_path.read_bytes()
    image_height = png_height(screenshot_bytes, screenshot_path)
    if len(screenshot_bytes) > MAX_PNG_BYTES:
        raise SystemExit(
            f"{screenshot_path} is {len(screenshot_bytes)} bytes. "
            "Workbench screenshots must be 2 MB or smaller so Google Sheets can insert them."
        )

    page = positive_int(payload.get("page", 1), "page")
    page_count = positive_int(payload.get("pageCount", 1), "pageCount")
    if page > page_count:
        raise SystemExit(f"page {page} is greater than pageCount {page_count}")

    request_payload = {
        "spreadsheetId": spreadsheet_id,
        "tab": tab,
        "persona": persona_name,
        "queryName": query["queryName"],
        "soql": soql,
        "headers": string_list(payload.get("headers") or []),
        "rows": string_grid(payload.get("rows") or []),
        "screenshotBase64": base64.b64encode(screenshot_bytes).decode("ascii"),
        "page": page,
        "pageCount": page_count,
        "imageHeightPx": image_height,
    }
    result = run_apps_script(script_id, request_payload, dev_mode=args.dev)
    start_row = result.get("startRow") if isinstance(result, dict) else None
    print(
        f"Appended {persona_name} / {request_payload['queryName']} "
        f"page {page} of {page_count} to tab {tab!r} at row {start_row}."
    )
    return 0


def get_credentials():
    if not CREDENTIALS_PATH.is_file():
        raise SystemExit(
            "Missing credentials.json in the project root. "
            "Create an OAuth desktop client in the same Google Cloud project as the Apps Script "
            "and save it as credentials.json."
        )

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_PATH.is_file():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def run_apps_script(script_id: str, payload: dict, dev_mode: bool) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    service = build("script", "v1", credentials=get_credentials(), cache_discovery=False)
    body = {
        "function": "writeEvidence",
        "parameters": [payload],
        "devMode": dev_mode,
    }
    try:
        response = service.scripts().run(scriptId=script_id, body=body).execute()
    except HttpError as exc:
        detail = exc.content.decode("utf-8", errors="replace") if exc.content else str(exc)
        raise SystemExit(f"Google API error: {detail}") from exc

    if "error" in response:
        raise SystemExit(f"Apps Script error: {script_error_text(response['error'])}")
    return response.get("response", {}).get("result") or {}


def script_error_text(error: dict) -> str:
    messages = []
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("errorMessage"):
            messages.append(str(detail["errorMessage"]))
    if messages:
        return "; ".join(messages)
    return str(error.get("message") or error)


def load_json(path: Path) -> dict:
    if not path.is_file():
        if path == CONFIG_PATH:
            raise SystemExit(
                "Missing config/uar.json. Copy config/uar.example.json to config/uar.json "
                "and fill in the spreadsheet id, Apps Script id, and master persona tab."
            )
        raise SystemExit(f"File not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return data


def persona_sheet_name(config: dict) -> str:
    name = str(config.get("personaSheet") or "Personas").strip()
    if not name:
        raise SystemExit("Set personaSheet in config/uar.json.")
    return name


def fetch_persona_rows(config: dict) -> tuple[str, list[dict]]:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    spreadsheet_id = required_setting(config, "spreadsheetId")
    service = build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)
    sheet_name = resolve_sheet_title(config, service, spreadsheet_id)
    try:
        response = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=sheet_range(sheet_name))
            .execute()
        )
    except HttpError as exc:
        detail = exc.content.decode("utf-8", errors="replace") if exc.content else str(exc)
        raise SystemExit(
            f"Could not read master tab {sheet_name!r}. "
            f"Add a tab with columns Persona and SOQL. Google API error: {detail}"
        ) from exc
    values = response.get("values") or []
    if not values:
        raise SystemExit(
            f"Master tab {sheet_name!r} is empty. Add a header row: Persona, SOQL."
        )
    return sheet_name, parse_persona_table(values, sheet_name)


def resolve_sheet_title(config: dict, service, spreadsheet_id: str) -> str:
    from googleapiclient.errors import HttpError

    sheet_id = config.get("personaSheetId")
    if sheet_id is None or sheet_id == "":
        return persona_sheet_name(config)
    if isinstance(sheet_id, bool) or not isinstance(sheet_id, int):
        raise SystemExit("personaSheetId in config/uar.json must be the numeric gid from the sheet URL.")
    try:
        meta = (
            service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title))")
            .execute()
        )
    except HttpError as exc:
        detail = exc.content.decode("utf-8", errors="replace") if exc.content else str(exc)
        raise SystemExit(f"Could not open the spreadsheet. Google API error: {detail}") from exc
    for sheet in meta.get("sheets") or []:
        props = sheet.get("properties") or {}
        if props.get("sheetId") == sheet_id:
            title = str(props.get("title") or "").strip()
            if title:
                return title
    raise SystemExit(f"No tab with gid {sheet_id} was found in the spreadsheet.")


def parse_persona_table(values: list[list], sheet_name: str) -> list[dict]:
    header_map = {
        "persona": "persona",
        "tab": "tab",
        "query name": "query name",
        "query": "query name",
        "soql": "soql",
    }
    index: dict[str, int] = {}
    for position, cell in enumerate(values[0]):
        canonical = header_map.get(normalize_header(cell))
        if canonical and canonical not in index:
            index[canonical] = position
    missing = [name for name in ("persona", "soql") if name not in index]
    if missing:
        found = ", ".join(str(cell) for cell in values[0]) or "(none)"
        raise SystemExit(
            f"Master tab {sheet_name!r} must have columns Persona and SOQL. Found: {found}"
        )

    rows = []
    for row_number, row in enumerate(values[1:], start=2):
        persona = cell_at(row, index["persona"])
        soql = cell_at(row, index["soql"])
        query_name = cell_at(row, index["query name"]) if "query name" in index else ""
        tab = cell_at(row, index["tab"]) if "tab" in index else ""
        if not any((persona, query_name, soql, tab)):
            continue
        if not persona or not soql:
            raise SystemExit(f"Row {row_number} on {sheet_name!r} needs Persona and SOQL.")
        rows.append(
            {
                "persona": persona,
                "tab": tab or persona,
                "queryName": query_name,
                "soql": soql,
            }
        )
    if not rows:
        raise SystemExit(f"Master tab {sheet_name!r} has no persona queries.")
    return assign_query_names(rows)


def assign_query_names(rows: list[dict]) -> list[dict]:
    unlabeled: dict[str, int] = {}
    for row in rows:
        if not row["queryName"]:
            key = row["persona"].casefold()
            unlabeled[key] = unlabeled.get(key, 0) + 1
    seen: dict[str, int] = {}
    for row in rows:
        if row["queryName"]:
            continue
        key = row["persona"].casefold()
        seen[key] = seen.get(key, 0) + 1
        row["queryName"] = row["persona"] if unlabeled[key] == 1 else f"Query {seen[key]}"
    return rows


def group_personas(rows: list[dict]) -> list[dict]:
    grouped: list[dict] = []
    by_persona: dict[str, dict] = {}
    seen_queries: set[tuple[str, str]] = set()
    for row in rows:
        key = row["persona"].casefold()
        query_key = (key, row["queryName"].casefold())
        if query_key in seen_queries:
            raise SystemExit(
                f"Persona {row['persona']!r} lists query {row['queryName']!r} more than once."
            )
        seen_queries.add(query_key)
        if key not in by_persona:
            entry = {"persona": row["persona"], "tab": row["tab"], "queries": []}
            by_persona[key] = entry
            grouped.append(entry)
        elif by_persona[key]["tab"].casefold() != row["tab"].casefold():
            raise SystemExit(
                f"Persona {row['persona']!r} uses more than one evidence tab: "
                f"{by_persona[key]['tab']!r} and {row['tab']!r}."
            )
        by_persona[key]["queries"].append({"name": row["queryName"], "soql": row["soql"]})
    return grouped


def create_evidence_spreadsheet(personas: list[str], title: str | None) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    tabs = evidence_tabs(personas)
    sheet_title = " ".join((title or "").split()) or datetime.now().strftime(
        "User Access Review %Y-%m-%d %H%M"
    )
    body = {
        "properties": {"title": sheet_title[:100]},
        "sheets": [{"properties": {"title": tab}} for tab in tabs],
    }
    service = build("sheets", "v4", credentials=get_credentials(), cache_discovery=False)
    try:
        created = (
            service.spreadsheets()
            .create(body=body, fields="spreadsheetId,spreadsheetUrl")
            .execute()
        )
    except HttpError as exc:
        detail = exc.content.decode("utf-8", errors="replace") if exc.content else str(exc)
        raise SystemExit(f"Could not create the evidence spreadsheet. Google API error: {detail}") from exc
    spreadsheet_id = str(created.get("spreadsheetId") or "").strip()
    if not spreadsheet_id:
        raise SystemExit("Google Sheets did not return a spreadsheet id.")
    return {
        "spreadsheetId": spreadsheet_id,
        "spreadsheetUrl": created.get("spreadsheetUrl")
        or f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
        "tabs": tabs,
    }


def evidence_tabs(personas: list[str]) -> list[str]:
    tabs = []
    seen: set[str] = set()
    for persona in personas:
        name = str(persona).strip()
        if not name:
            raise SystemExit("Each --persona value must be a non-empty name.")
        tab = evidence_tab_name(name)
        key = tab.casefold()
        if key in seen:
            raise SystemExit(f"Tab {tab!r} would be created more than once.")
        seen.add(key)
        tabs.append(tab)
    return tabs


def evidence_tab_name(name: str) -> str:
    cleaned = "".join(" " if character in "[]*?/\\:" else character for character in name)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        raise SystemExit(f"Persona {name!r} cannot be used as a tab name.")
    return cleaned[:100]


def evidence_spreadsheet_id(payload: dict, config: dict) -> str:
    spreadsheet_id = required_text(payload, "spreadsheetId")
    master_id = required_setting(config, "spreadsheetId")
    if spreadsheet_id == master_id:
        raise SystemExit(
            "Refusing to write evidence onto the master spreadsheet. "
            "Create a new spreadsheet with --create-spreadsheet for this prompt."
        )
    return spreadsheet_id


def resolve_query(rows: list[dict], persona: str, query_name: str) -> tuple[str, str, dict]:
    matches = [row for row in rows if row["persona"].casefold() == persona.casefold()]
    if not matches:
        known = list(dict.fromkeys(row["persona"] for row in rows))
        names = ", ".join(known) or "(none)"
        raise SystemExit(f"Persona {persona!r} is not on the master sheet. Known personas: {names}")
    match = next(
        (row for row in matches if row["queryName"].casefold() == query_name.casefold()),
        None,
    )
    if match is None:
        names = ", ".join(row["queryName"] for row in matches)
        raise SystemExit(
            f"Query {query_name!r} is not on the master sheet for persona {matches[0]['persona']!r}. "
            f"Queries: {names}"
        )
    return match["persona"], evidence_tab_name(match["tab"]), match


def sheet_range(sheet_name: str) -> str:
    escaped = sheet_name.replace("'", "''")
    return f"'{escaped}'!A:Z"


def normalize_header(value: object) -> str:
    return " ".join(str(value).strip().casefold().split())


def cell_at(row: list, index: int) -> str:
    if index >= len(row):
        return ""
    return str(row[index]).strip()


def required_setting(config: dict, field: str) -> str:
    value = str(config.get(field) or "").strip()
    if value in PLACEHOLDERS:
        raise SystemExit(f"Set {field} in config/uar.json before writing evidence.")
    return value


def required_text(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"Payload field {field} must be a non-empty string")
    return value.strip()


def positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SystemExit(f"Payload field {field} must be an integer >= 1")
    return value


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise SystemExit("Payload field headers must be a list")
    return ["" if item is None else str(item) for item in value]


def string_grid(value: object) -> list[list[str]]:
    if not isinstance(value, list):
        raise SystemExit("Payload field rows must be a list")
    grid = []
    for row in value:
        if not isinstance(row, list):
            raise SystemExit("Each entry in payload field rows must be a list")
        grid.append(["" if cell is None else str(cell) for cell in row])
    return grid


def resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def png_height(data: bytes, path: Path) -> int:
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise SystemExit(f"{path} is not a PNG file.")
    height = int.from_bytes(data[20:24], "big")
    if height < 1:
        raise SystemExit(f"{path} has an invalid PNG height.")
    return height


if __name__ == "__main__":
    sys.exit(main())
