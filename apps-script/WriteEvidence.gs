/**
 * Append one Workbench user-access-review evidence block to a spreadsheet tab
 * and insert the screenshot into the private spreadsheet.
 *
 * Deploy this standalone project as an API executable. writeEvidence runs as
 * the Google account that authorized the caller, so that account must be able
 * to edit the spreadsheet.
 *
 * One-time setup:
 * 1. Create a standalone Apps Script project and paste this file as Code.gs.
 * 2. Show the appsscript.json manifest and set:
 *    "oauthScopes": ["https://www.googleapis.com/auth/spreadsheets"]
 * 3. Link this Apps Script project to the same Google Cloud project that owns
 *    credentials.json (Project Settings > Google Cloud Platform project).
 * 4. Enable the Google Sheets API and the Apps Script API on that Cloud project.
 *    Also turn on the Apps Script API at https://script.google.com/home/usersettings.
 * 5. Deploy > New deployment > API executable.
 * 6. Copy Project Settings > Script ID (not the deployment id) into
 *    config/uar.json as scriptId.
 *
 * @param {Object} payload
 * @param {string} payload.spreadsheetId
 * @param {string} payload.tab
 * @param {string} payload.persona
 * @param {string} payload.queryName
 * @param {string} payload.soql
 * @param {string[]} payload.headers
 * @param {string[][]} payload.rows
 * @param {string} payload.screenshotBase64 PNG bytes, base64, without a data-URL prefix.
 * @param {number} payload.page
 * @param {number} payload.pageCount
 * @param {number} payload.imageHeightPx
 * @return {{ok: boolean, tab: string, startRow: number, imageRow: number}}
 */
function writeEvidence(payload) {
  if (!payload || !payload.spreadsheetId || !payload.tab) {
    throw new Error("spreadsheetId and tab are required");
  }

  const ss = SpreadsheetApp.openById(payload.spreadsheetId);
  let sheet = ss.getSheetByName(payload.tab);
  if (!sheet) {
    sheet = ss.insertSheet(payload.tab);
  }

  const headers = (payload.headers || []).map(cellToString_);
  const sourceRows = payload.rows || [];
  let width = headers.length;
  if (width === 0) {
    sourceRows.forEach(function (row) {
      const source = Array.isArray(row) ? row : [row];
      width = Math.max(width, source.length);
    });
  }
  const rows = sourceRows.map(function (row) {
    const source = Array.isArray(row) ? row : [row];
    const copy = source.slice(0, width).map(cellToString_);
    while (copy.length < width) {
      copy.push("");
    }
    return copy;
  });

  const stamp = Utilities.formatDate(
    new Date(),
    Session.getScriptTimeZone(),
    "yyyy-MM-dd HH:mm:ss"
  );
  const meta = [
    ["Run timestamp", stamp],
    ["Persona", cellToString_(payload.persona)],
    ["Query", cellToString_(payload.queryName)],
    ["SOQL", cellToString_(payload.soql)],
    [
      "Page",
      cellToString_(payload.page || 1) + " of " + cellToString_(payload.pageCount || 1),
    ],
  ];
  const startRow = nextAppendRow_(sheet);
  sheet.getRange(startRow, 1, meta.length, 2).setValues(meta);
  sheet.getRange(startRow, 1, meta.length, 1).setFontWeight("bold");
  sheet.getRange(startRow + 3, 2).setWrap(true);

  let lastContentRow = startRow + meta.length - 1;
  if (width > 0) {
    let dataStartRow = lastContentRow + 2;
    if (headers.length > 0) {
      sheet.getRange(dataStartRow, 1, 1, width).setValues([headers]).setFontWeight("bold");
      lastContentRow = dataStartRow;
      dataStartRow += 1;
    }
    if (rows.length > 0) {
      sheet.getRange(dataStartRow, 1, rows.length, width).setValues(rows);
      lastContentRow = dataStartRow + rows.length - 1;
    }
  }

  const imageRow = lastContentRow + 2;
  const imageHeight = Number(payload.imageHeightPx) || 0;
  const rowHeight = sheet.getRowHeight(startRow) || 21;
  const spacer = imageHeight > 0 ? Math.ceil(imageHeight / rowHeight) + 4 : 2;
  const markerRow = imageRow + spacer;
  sheet.getRange(markerRow, 1).setValue("End of evidence block");

  if (payload.screenshotBase64) {
    const blob = Utilities.newBlob(
      Utilities.base64Decode(stripBase64Prefix_(payload.screenshotBase64)),
      "image/png",
      "workbench.png"
    );
    sheet.insertImage(blob, 1, imageRow);
  }

  return {
    ok: true,
    tab: payload.tab,
    startRow: startRow,
    imageRow: imageRow,
  };
}

function nextAppendRow_(sheet) {
  const last = sheet.getLastRow();
  if (last === 0) {
    return 1;
  }
  return last + 2;
}

function cellToString_(value) {
  if (value === null || value === undefined) {
    return "";
  }
  const text = String(value);
  return text.charAt(0) === "=" ? "'" + text : text;
}

function stripBase64Prefix_(value) {
  const text = String(value).replace(/\s/g, "");
  const marker = "base64,";
  const index = text.indexOf(marker);
  return index === -1 ? text : text.substring(index + marker.length);
}
