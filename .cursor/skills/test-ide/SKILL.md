---
name: test-ide
description: >-
  Run a Salesforce Workbench user access review for a named persona. Open
  Workbench in Production, run that persona's saved SOQL, capture the results
  screenshot and rows, and write each persona to its own tab on a new Google
  spreadsheet. Use when the user asks for a user access review, a persona
  review, Test IDE, or to capture Workbench SOQL evidence.
---

# Test IDE

When the user asks to run a user access review for a persona, follow this procedure. Never type a Salesforce username, password, or MFA code.

## Load the persona

- Personas and SOQL are maintained on the master Google Sheet tab, not in a local file. `config/uar.json` has the spreadsheet id, Apps Script id, and either `personaSheetId` (the gid in the sheet URL) or `personaSheet` (default `Personas`).
- If `config/uar.json` is missing, stop and tell the user to copy `config/uar.example.json` to `config/uar.json` and fill in those ids.
- Load the catalog with `python scripts/write_uar_evidence.py --list-personas`. The master tab has two columns, Persona and SOQL. One row is one query. Several rows may share a persona.
- Match the persona name without case sensitivity. If it is missing, stop and list the persona names from that command.
- Run every query for that persona, in sheet order. Use each query's `name` and `soql` from that command. Use the persona name exactly as listed.

## Create the evidence spreadsheet

- Once per prompt, create one new Google spreadsheet for the personas in that request. Do not add evidence to the master catalog spreadsheet.
- One persona gets one tab. Different personas in the same prompt get their own tabs on that same new spreadsheet. Do not create a second spreadsheet for another persona in the same prompt.
- A later prompt creates another new spreadsheet.
- Run this before writing evidence, using the catalog persona names:

```
python scripts/write_uar_evidence.py --create-spreadsheet --persona "Sales Manager" --persona "Analyst"
```

- The command prints `spreadsheetId`, `spreadsheetUrl`, and `tabs`. Tell the user the spreadsheet URL. Reuse that `spreadsheetId` in every payload for this prompt.

## Open Workbench

- Open https://workbench.developerforce.com/login.php in the IDE browser.
- If Workbench is already signed in, open https://workbench.developerforce.com/query.php and skip the login click.
- Otherwise select the environment option labeled Production, check "I agree to the terms of service", and click "Login with Salesforce".
- Stop and ask the user to finish Salesforce login. Continue only after they confirm they are signed in.
- Workbench uses the Salesforce user who signs in in that browser. Do not use `credentials.json` for Workbench. That file is only the Google OAuth client.

## Run each saved query

On https://workbench.developerforce.com/query.php for each query:

- Put that query's SOQL in the query box and run it.
- If Workbench shows an error instead of a results table, stop and report the error. Do not write evidence for that failed query.
- Follow Next while it is present and enabled, and record every results page. On each page, read the results table from the page into headers and rows. The first table row is `headers`. Do not OCR the screenshot for cell values.
- Save a PNG screenshot of that results page under `evidence/`. Keep each PNG at or under 2 MB.
- An empty result table is still evidence. Write headers and an empty `rows` array.

After the last page of a query, write one payload JSON file per page under `evidence/`:

```json
{
  "spreadsheetId": "NEW_SPREADSHEET_ID",
  "persona": "Sales Manager",
  "queryName": "Sales Manager",
  "soql": "SELECT Id, Name FROM User",
  "headers": ["Id", "Name"],
  "rows": [["001", "Ada"]],
  "screenshot": "evidence/sales-manager-p1.png",
  "page": 1,
  "pageCount": 1
}
```

`pageCount` is the number of results pages for that query. Paths in the payload are relative to the project root. From the project root, run one command per page:

```
python scripts/write_uar_evidence.py --payload evidence/<file>.json
```

The writer appends the screenshot and the rows on that persona's tab in the new spreadsheet. Another page for the same persona appends on the same tab. A different persona uses its own tab.

## If the sheet write cannot start

- Missing `credentials.json`: tell the user to create an OAuth desktop client in the same Google Cloud project as the Apps Script and save it as `credentials.json`.
- Missing `token.json`: tell the user to run `python scripts/write_uar_evidence.py --auth-only` and finish the Google consent screen.
- A Google permission error: tell the user to link the Apps Script project to that same Cloud project, deploy `apps-script/WriteEvidence.gs` as an API executable, and put its Script ID in `config/uar.json`.
