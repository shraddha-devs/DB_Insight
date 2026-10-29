const questionInput = document.querySelector("#question-input");
const sqlEditor = document.querySelector("#sql-editor");
const generateButton = document.querySelector("#generate-button");
const runButton = document.querySelector("#run-button");
const copyButton = document.querySelector("#copy-button");
const resultsContent = document.querySelector("#results-content");
const resultCount = document.querySelector("#result-count");
const runDuration = document.querySelector("#run-duration");
const questionMessage = document.querySelector("#question-message");
const resultsMessage = document.querySelector("#results-message");
const historyList = document.querySelector("#history-list");
const schemaList = document.querySelector("#schema-list");
const lineGutter = document.querySelector("#line-gutter");
const editorPosition = document.querySelector("#editor-position");

function showMessage(target, message, isError = false) {
  target.textContent = message;
  target.classList.toggle("is-error", isError);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.title || `Request failed (${response.status}).`);
  }
  return data;
}

function setBusy(button, busy, idleLabel, busyLabel) {
  button.disabled = busy;
  button.dataset.idle = idleLabel;
  button.innerHTML = busy ? busyLabel : idleLabel;
}

function renderRows(result) {
  resultCount.textContent = `${result.row_count} row${result.row_count === 1 ? "" : "s"}${result.truncated ? "+" : ""}`;
  runDuration.textContent = result.truncated ? `Capped at ${result.row_count} rows` : "Read-only result";
  resultsContent.replaceChildren();

  if (!result.columns.length) {
    const empty = document.createElement("div");
    empty.className = "empty-results";
    empty.innerHTML = '<span class="empty-mark" aria-hidden="true">✓</span><p>Query completed</p><span>No tabular results were returned.</span>';
    resultsContent.append(empty);
    return;
  }

  if (result.rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-results";
    empty.innerHTML = '<span class="empty-mark" aria-hidden="true">⌁</span><p>No matching rows</p><span>Try adjusting your question or SQL filters.</span>';
    resultsContent.append(empty);
    return;
  }

  const table = document.createElement("table");
  table.className = "result-table";
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  for (const column of result.columns) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = column;
    headerRow.append(th);
  }
  thead.append(headerRow);
  table.append(thead);

  const tbody = document.createElement("tbody");
  for (const row of result.rows) {
    const tr = document.createElement("tr");
    for (const column of result.columns) {
      const td = document.createElement("td");
      const value = row[column];
      if (value === null || value === undefined) {
        td.textContent = "NULL";
        td.className = "null-value";
      } else {
        td.textContent = String(value);
      }
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(tbody);
  resultsContent.append(table);
}

async function loadSchema() {
  try {
    const { tables } = await requestJson("/api/schema");
    document.querySelector("#table-count").textContent = String(tables.length);
    schemaList.replaceChildren();
    for (const table of tables) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "schema-item";
      button.title = table.columns.map((column) => `${column.name} (${column.type})`).join(", ");
      const name = document.createElement("span");
      name.textContent = table.name;
      const count = document.createElement("span");
      count.textContent = `${table.columns.length} cols`;
      button.append(name, count);
      button.addEventListener("click", () => {
        sqlEditor.value = `SELECT *\nFROM ${table.name}\nLIMIT 100;`;
        updateEditor();
        sqlEditor.focus();
      });
      schemaList.append(button);
    }
  } catch (error) {
    schemaList.replaceChildren();
    const note = document.createElement("div");
    note.className = "sidebar-placeholder";
    note.textContent = error.message;
    schemaList.append(note);
  }
}

function renderHistory(items) {
  historyList.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "sidebar-placeholder";
    empty.textContent = "No queries yet";
    historyList.append(empty);
    return;
  }
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "history-item";
    button.title = "Load this question and SQL into the editor";
    const question = document.createElement("span");
    question.className = "history-question";
    question.textContent = item.question || item.sql;
    const meta = document.createElement("span");
    meta.className = "history-meta";
    const date = new Date(`${item.created_at.replace(" ", "T")}Z`);
    meta.textContent = `${item.row_count}${item.truncated ? "+" : ""} rows · ${Number.isNaN(date.valueOf()) ? item.created_at : date.toLocaleString()}`;
    button.append(question, meta);
    button.addEventListener("click", () => {
      questionInput.value = item.question;
      sqlEditor.value = item.sql;
      updateEditor();
      document.querySelector(".sidebar").classList.remove("mobile-history-open");
      document.querySelector("#mobile-history-toggle").setAttribute("aria-expanded", "false");
      sqlEditor.focus();
    });
    historyList.append(button);
  }
}

async function loadHistory() {
  try {
    const { items } = await requestJson("/api/history");
    renderHistory(items);
  } catch (error) {
    renderHistory([]);
    showMessage(resultsMessage, `History unavailable: ${error.message}`, true);
  }
}

function updateEditor() {
  const lineCount = Math.max(1, sqlEditor.value.split("\n").length);
  lineGutter.textContent = Array.from({ length: lineCount }, (_, index) => index + 1).join("\n");
  const before = sqlEditor.value.slice(0, sqlEditor.selectionStart);
  const lines = before.split("\n");
  editorPosition.textContent = `Ln ${lines.length}, Col ${lines.at(-1).length + 1}`;
}

async function generateSql() {
  const question = questionInput.value.trim();
  if (question.length < 3) {
    showMessage(questionMessage, "Enter a question with at least three characters.", true);
    questionInput.focus();
    return;
  }
  showMessage(questionMessage, "");
  setBusy(generateButton, true, '<span class="button-symbol" aria-hidden="true">✳</span> Generate SQL', "Generating…");
  try {
    const { sql } = await requestJson("/api/generate", {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    sqlEditor.value = sql;
    updateEditor();
    showMessage(questionMessage, "SQL generated. Review it before running.");
    sqlEditor.focus();
  } catch (error) {
    showMessage(questionMessage, error.message, true);
  } finally {
    setBusy(generateButton, false, '<span class="button-symbol" aria-hidden="true">✳</span> Generate SQL', "Generating…");
  }
}

async function runSql() {
  const sql = sqlEditor.value.trim();
  if (!sql) {
    showMessage(resultsMessage, "Enter a SELECT query or generate SQL from a question.", true);
    sqlEditor.focus();
    return;
  }
  showMessage(resultsMessage, "");
  resultCount.textContent = "";
  runDuration.textContent = "Running…";
  setBusy(runButton, true, '<span aria-hidden="true">▶</span> Run query', "Running…");
  const started = performance.now();
  try {
    const result = await requestJson("/api/query", {
      method: "POST",
      body: JSON.stringify({ sql, question: questionInput.value.trim() }),
    });
    renderRows(result);
    runDuration.textContent = `${(performance.now() - started).toFixed(0)} ms${result.truncated ? " · row limit reached" : ""}`;
    showMessage(resultsMessage, "");
    await loadHistory();
  } catch (error) {
    resultCount.textContent = "";
    runDuration.textContent = "";
    resultsContent.replaceChildren();
    showMessage(resultsMessage, error.message, true);
  } finally {
    setBusy(runButton, false, '<span aria-hidden="true">▶</span> Run query', "Running…");
  }
}

generateButton.addEventListener("click", generateSql);
runButton.addEventListener("click", runSql);
copyButton.addEventListener("click", async () => {
  if (!sqlEditor.value.trim()) {
    showMessage(resultsMessage, "There is no SQL to copy yet.", true);
    return;
  }
  try {
    await navigator.clipboard.writeText(sqlEditor.value);
    showMessage(resultsMessage, "SQL copied to clipboard.");
  } catch {
    sqlEditor.focus();
    sqlEditor.select();
    showMessage(resultsMessage, "Clipboard access is unavailable. SQL is selected; copy it with Ctrl+C.", true);
  }
});

document.querySelector("#clear-history").addEventListener("click", async () => {
  try {
    await requestJson("/api/history", { method: "DELETE" });
    renderHistory([]);
  } catch (error) {
    showMessage(resultsMessage, `Could not clear history: ${error.message}`, true);
  }
});

document.querySelector("#mobile-history-toggle").addEventListener("click", (event) => {
  const isOpen = document.querySelector(".sidebar").classList.toggle("mobile-history-open");
  event.currentTarget.setAttribute("aria-expanded", String(isOpen));
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    questionInput.value = button.dataset.question;
    questionInput.focus();
    showMessage(questionMessage, "");
  });
});

sqlEditor.addEventListener("input", updateEditor);
sqlEditor.addEventListener("click", updateEditor);
sqlEditor.addEventListener("keyup", updateEditor);
sqlEditor.addEventListener("keydown", (event) => {
  if (event.key === "Tab") {
    event.preventDefault();
    const start = sqlEditor.selectionStart;
    const end = sqlEditor.selectionEnd;
    sqlEditor.setRangeText("  ", start, end, "end");
    updateEditor();
  }
});
sqlEditor.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    runSql();
  }
});
questionInput.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    generateSql();
  }
});

updateEditor();
loadSchema();
loadHistory();