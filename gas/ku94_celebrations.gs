/**
 * KU94 RUN spreadsheet celebrations.
 * Keeps the source table shape intact: badges and finisher names go to a separate
 * summary tab; finishers are highlighted with conditional formatting.
 *
 * Paste this file into the spreadsheet's bound Apps Script project, then run
 * installKu94Celebrations() once and approve the requested spreadsheet access.
 */
const KU94_SUMMARY_SHEET = "챌린지 요약";
const KU94_MILESTONES = [10, 30, 50, 100];
const KU94_BADGES = { 10: "🥉", 30: "🥈", 50: "🥇", 100: "🏆" };
const KU94_REST_MARKS = new Set(["휴식", "rest", "x", "×"]);
const KU94_WEEKDAY_HEADERS = new Set([
  "월", "화", "수", "목", "금", "토", "일",
  "월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일",
]);
const KU94_PROPERTIES = PropertiesService.getScriptProperties();

/** Add a refresh menu when the spreadsheet opens. */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("KU94 RUN")
    .addItem("챌린지 요약 갱신", "refreshKu94ChallengeSummary")
    .addToUi();
}

/** Run once to install the edit trigger, seed current counts, and build the summary. */
function installKu94Celebrations() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const source = findKu94ChallengeTable_(spreadsheet);
  addKu94FinisherHighlight_(source);
  seedKu94Counts_(spreadsheet, source);
  const installed = ScriptApp.getProjectTriggers().some(
    (trigger) => trigger.getHandlerFunction() === "handleKu94ChallengeEdit",
  );
  if (!installed) {
    ScriptApp.newTrigger("handleKu94ChallengeEdit")
      .forSpreadsheet(spreadsheet)
      .onEdit()
      .create();
  }
  refreshKu94ChallengeSummary();
  spreadsheet.toast("완주자 강조와 챌린지 요약이 설정되었습니다.", "KU94 RUN", 5);
}

/** Remove only this feature's edit trigger. */
function removeKu94Celebrations() {
  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (trigger.getHandlerFunction() === "handleKu94ChallengeEdit") {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}

/** Called by the installable edit trigger. */
function handleKu94ChallengeEdit(event) {
  if (!event || !event.range) return;
  const editedSheet = event.range.getSheet();
  if (editedSheet.getName() === KU94_SUMMARY_SHEET) return;

  const spreadsheet = event.source || editedSheet.getParent();
  const source = findKu94ChallengeTable_(spreadsheet);
  if (source.sheet.getSheetId() !== editedSheet.getSheetId()) return;
  if (!rangeTouchesColumns_(event.range, source.dayColumns)) return;

  const firstRow = Math.max(event.range.getRow(), source.firstDataRow);
  const lastRow = Math.min(event.range.getLastRow(), source.sheet.getLastRow());
  const notices = [];
  for (let rowNumber = firstRow; rowNumber <= lastRow; rowNumber += 1) {
    const runnerName = String(source.sheet.getRange(rowNumber, source.nameColumn).getDisplayValue()).trim();
    if (!runnerName) continue;
    const count = parseRunCount_(source.sheet.getRange(rowNumber, source.runCountColumn).getDisplayValue());
    const previousCount = getAndStoreKu94Count_(spreadsheet, source, runnerName, rowNumber, count);

    KU94_MILESTONES.forEach((milestone) => {
      if (previousCount < milestone && count >= milestone) {
        notices.push(`${KU94_BADGES[milestone]} ${runnerName} ${milestone}회 달성`);
      }
    });

    if (count >= 100) {
      const rowRange = source.sheet.getRange(rowNumber, 1, 1, source.lastColumn);
      rowRange.setNote(`🏆 챌린지 완주 · ${runnerName}`);
    }

    const editedStart = Math.max(event.range.getColumn(), source.dayColumns[0]);
    const editedEnd = event.range.getLastColumn();
    source.dayColumns.forEach((dayColumn, index) => {
      if (dayColumn < editedStart || dayColumn > editedEnd || index === 0) return;
      const mark = source.sheet.getRange(rowNumber, dayColumn).getDisplayValue();
      const previousDayMark = source.sheet.getRange(rowNumber, source.dayColumns[index - 1]).getDisplayValue();
      if (isKu94RunMark_(mark) && KU94_REST_MARKS.has(String(previousDayMark).trim().toLowerCase())) {
        const returnKey = ku94PropertyKey_(spreadsheet, source, runnerName, `return-${index}`);
        if (KU94_PROPERTIES.getProperty(returnKey) !== "sent") {
          notices.push(`👟 ${runnerName} 다시 달리기 시작!`);
          KU94_PROPERTIES.setProperty(returnKey, "sent");
        }
      }
    });
  }

  refreshKu94ChallengeSummary();
  if (notices.length) {
    spreadsheet.toast(notices.join(" · "), "KU94 RUN 축하", 8);
  }
}

/** Rebuild the generated summary tab without adding columns to the source table. */
function refreshKu94ChallengeSummary() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const source = findKu94ChallengeTable_(spreadsheet);
  const summary = spreadsheet.getSheetByName(KU94_SUMMARY_SHEET) || spreadsheet.insertSheet(KU94_SUMMARY_SHEET);
  const runners = [];
  for (let rowNumber = source.firstDataRow; rowNumber <= source.sheet.getLastRow(); rowNumber += 1) {
    const name = String(source.sheet.getRange(rowNumber, source.nameColumn).getDisplayValue()).trim();
    if (!name) continue;
    const count = parseRunCount_(source.sheet.getRange(rowNumber, source.runCountColumn).getDisplayValue());
    const badges = KU94_MILESTONES.filter((value) => count >= value)
      .map((value) => KU94_BADGES[value])
      .join(" ");
    runners.push({ name, count, badge: badges });
  }
  runners.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "ko"));
  const finishers = runners.filter((runner) => runner.count >= 100);
  const values = [
    ["KU94 RUN 챌린지 요약", ""],
    ["이름", "달리기 횟수", "배지"],
    ...runners.map((runner) => [runner.name, runner.count, runner.badge]),
    ["", "", ""],
    [`현재 챌린지 달성자 (${finishers.length}명)`, ""],
    ["완주자 이름", "달리기 횟수", "배지"],
    ...finishers.map((runner) => [runner.name, runner.count, "🏆"]),
  ];
  summary.clearContents();
  summary.getRange(1, 1, values.length, Math.max(...values.map((row) => row.length))).setValues(
    values.map((row) => [...row, ...Array(Math.max(...values.map((item) => item.length)) - row.length).fill("")]),
  );
  summary.setFrozenRows(2);
  summary.autoResizeColumns(1, 3);
  summary.getRange(1, 1, 1, 3).setFontWeight("bold").setFontSize(14);
  summary.getRange(2, 1, 1, 3).setFontWeight("bold").setBackground("#E8F0FE");
  const finisherHeaderRow = runners.length + 4;
  summary.getRange(finisherHeaderRow, 1, 1, 3).setFontWeight("bold").setBackground("#FFF2CC");
  summary.getRange(finisherHeaderRow + 1, 1, 1, 3).setFontWeight("bold");
  if (finishers.length) {
    summary.getRange(finisherHeaderRow + 2, 1, finishers.length, 3).setBackground("#FFF8E1");
  }
}

function findKu94ChallengeTable_(spreadsheet) {
  for (const sheet of spreadsheet.getSheets()) {
    if (sheet.getName() === KU94_SUMMARY_SHEET) continue;
    const values = sheet.getDataRange().getDisplayValues();
    for (let rowIndex = 0; rowIndex < values.length; rowIndex += 1) {
      const headers = values[rowIndex].map((value) => String(value).trim());
      const nameColumn = headers.findIndex((value) => value === "이름") + 1;
      const runCountColumn = headers.findIndex((value) => value.replace(/\s/g, "") === "달리기횟수") + 1;
      const dayColumns = headers
        .map((value, index) => ({ value, column: index + 1 }))
        .filter((item) => KU94_WEEKDAY_HEADERS.has(item.value))
        .map((item) => item.column);
      if (nameColumn && runCountColumn && dayColumns.length) {
        return {
          sheet,
          headerRow: rowIndex + 1,
          firstDataRow: rowIndex + 2,
          nameColumn,
          runCountColumn,
          dayColumns,
          lastColumn: headers.length,
        };
      }
    }
  }
  throw new Error("이름, 달리기 횟수, 요일 헤더가 있는 챌린지 표를 찾지 못했습니다.");
}

function addKu94FinisherHighlight_(table) {
  const rowCount = Math.max(1, table.sheet.getMaxRows() - table.headerRow);
  const target = table.sheet.getRange(table.firstDataRow, 1, rowCount, table.lastColumn);
  const countColumnLetter = columnToLetter_(table.runCountColumn);
  const formula = `=$${countColumnLetter}${table.firstDataRow}>=100`;
  const rules = table.sheet.getConditionalFormatRules();
  const alreadyExists = rules.some((rule) => {
    const condition = rule.getBooleanCondition();
    return condition &&
      condition.getCriteriaType() === SpreadsheetApp.BooleanCriteria.CUSTOM_FORMULA &&
      condition.getCriteriaValues()[0] === formula;
  });
  if (!alreadyExists) {
    rules.push(
      SpreadsheetApp.newConditionalFormatRule()
        .whenFormulaSatisfied(formula)
        .setBackground("#FFF2CC")
        .setBold(true)
        .setRanges([target])
        .build(),
    );
    table.sheet.setConditionalFormatRules(rules);
  }
}

function seedKu94Counts_(spreadsheet, table) {
  for (let rowNumber = table.firstDataRow; rowNumber <= table.sheet.getLastRow(); rowNumber += 1) {
    const name = String(table.sheet.getRange(rowNumber, table.nameColumn).getDisplayValue()).trim();
    if (!name) continue;
    const count = parseRunCount_(table.sheet.getRange(rowNumber, table.runCountColumn).getDisplayValue());
    const key = ku94PropertyKey_(spreadsheet, table, name, "count");
    if (KU94_PROPERTIES.getProperty(key) === null) KU94_PROPERTIES.setProperty(key, String(count));
  }
}

function getAndStoreKu94Count_(spreadsheet, table, name, rowNumber, count) {
  const key = ku94PropertyKey_(spreadsheet, table, name, "count");
  const stored = KU94_PROPERTIES.getProperty(key);
  const previous = stored === null ? count : Number(stored);
  KU94_PROPERTIES.setProperty(key, String(count));
  return Number.isFinite(previous) ? previous : count;
}

function ku94PropertyKey_(spreadsheet, table, name, suffix) {
  return ["KU94", spreadsheet.getId(), table.sheet.getSheetId(), name, suffix].join("_");
}

function parseRunCount_(value) {
  const match = String(value || "").replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : 0;
}

function isKu94RunMark_(value) {
  const text = String(value || "").trim();
  if (!text || KU94_REST_MARKS.has(text.toLowerCase()) || ["-", "0", "n", "no"].includes(text.toLowerCase())) return false;
  if (["o", "○", "⭕", "✓", "✔", "✅", "y", "yes", "run"].includes(text.toLowerCase())) return true;
  const number = Number(text.replace(/,/g, ""));
  return Number.isFinite(number) ? number > 0 : /^\d+(?:\.\d+)?\s*(?:km|키로|킬로)$/i.test(text);
}

function rangeTouchesColumns_(range, columns) {
  return columns.some((column) => column >= range.getColumn() && column <= range.getLastColumn());
}

function columnToLetter_(column) {
  let letters = "";
  while (column > 0) {
    const remainder = (column - 1) % 26;
    letters = String.fromCharCode(65 + remainder) + letters;
    column = Math.floor((column - 1) / 26);
  }
  return letters;
}
