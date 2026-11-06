"use strict";

const POLL_INTERVAL_MS = 1500;
const POLL_MAX_FAILURES = 3;   // подряд неудачных опросов до сообщения об ошибке

const HINTS = {
  regex: "Быстро, без ИИ. Производителя и группу придётся указать вручную.",
  ai: "ИИ определит производителя, группу и маски. Обработка может занять время."
};

const $ = (id) => document.getElementById(id);
const overlay = $("overlay");
const body = $("modal-body");

let records = [];            // редактируемые данные
let extractCtl = null;       // отмена запроса извлечения
let jobId = null;            // активная задача генерации
let session = 0;             // номер «сеанса»: растёт при каждом сбросе, отсекает устаревшие ответы

/* ---------- Вспомогательные ---------- */
function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function errorText(data, fallback) {
  return data && typeof data.detail === "string" ? data.detail : fallback;
}
function showSpinner(text) {
  body.replaceChildren(el("div", { className: "center" }, el("div", { className: "spinner" }), text));
}
function showError(message) {
  body.replaceChildren(el("div", { className: "banner error", role: "alert" }, message));
}
function setBanner(banner, kind, message) {
  banner.replaceChildren(el("div", { className: `banner ${kind}`, role: kind === "error" ? "alert" : "status" }, message));
}

/* ---------- Стартовое состояние ---------- */
function currentMode() {
  return document.querySelector("input[name=mode]:checked").value;
}
function updateHint() {
  $("mode-hint").textContent = HINTS[currentMode()];
}
document.querySelectorAll("input[name=mode]").forEach((radio) => radio.addEventListener("change", updateHint));
updateHint();

$("upload-btn").addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", (event) => {
  const file = event.target.files[0];
  event.target.value = "";     // позволяет выбрать тот же файл повторно
  if (file) startExtraction(file);
});
$("close-btn").addEventListener("click", resetAll);
// Закрытие вкладки тоже останавливает генерацию и удаляет файлы.
window.addEventListener("pagehide", discardJob);

function discardJob() {
  if (jobId) {
    fetch(`/api/jobs/${jobId}`, { method: "DELETE", keepalive: true }).catch(() => {});
    jobId = null;
  }
}

function resetAll() {
  session += 1;
  if (extractCtl) extractCtl.abort();
  extractCtl = null;
  discardJob();
  records = [];
  body.replaceChildren();
  overlay.classList.remove("open");
}

/* ---------- Извлечение масок ---------- */
async function startExtraction(file) {
  const mode = currentMode();
  const mySession = ++session;

  $("file-name").textContent = file.name;
  overlay.classList.add("open");
  showSpinner(mode === "ai" ? "ИИ анализирует документ…" : "Ищем маски в документе…");

  const form = new FormData();
  form.append("file", file);
  form.append("mode", mode);

  extractCtl = new AbortController();

  try {
    const res = await fetch("/api/extract", { method: "POST", body: form, signal: extractCtl.signal });
    const data = await res.json().catch(() => ({}));
    if (mySession !== session) return;
    if (!res.ok) throw new Error(errorText(data, "Не удалось обработать файл."));

    records = data.records;
    renderRecords();
  } catch (err) {
    if (err.name === "AbortError" || mySession !== session) return;
    showError(err.message);
  }
}

/* ---------- Редактирование ---------- */
function renderRecords() {
  if (!records.length) {
    showError("В документе не найдено ни одной маски.");
    return;
  }

  const list = el("div");

  records.forEach((rec, i) => {
    const man = el("input", { type: "text", value: rec.manufacture ?? "", id: `man-${i}` });
    const grp = el("input", { type: "text", value: rec.group ?? "", id: `grp-${i}` });
    const sym = el("input", { type: "text", value: rec.symbol ?? "*", id: `sym-${i}`, maxLength: 1, style: "width:70px;text-align:center" });
    const msk = el("textarea", { value: (rec.masks || []).join("\n"), id: `msk-${i}`, spellcheck: false });

    man.addEventListener("input", () => { rec.manufacture = man.value; man.classList.remove("invalid"); });
    grp.addEventListener("input", () => { rec.group = grp.value; grp.classList.remove("invalid"); });
    sym.addEventListener("input", () => { rec.symbol = sym.value; sym.classList.remove("invalid"); });
    msk.addEventListener("input", () => { rec.masks = msk.value.split(/[\s,;]+/).filter(Boolean); msk.classList.remove("invalid"); });

    list.append(el("section", { className: "record" },
      el("div", { className: "record-title" }, `Группа ${i + 1} из ${records.length}`),
      el("div", { className: "field" }, el("label", { className: "label", htmlFor: man.id }, "Производитель"), man),
      el("div", { className: "field" }, el("label", { className: "label", htmlFor: grp.id }, "Товарная группа"), grp),
      el("div", { className: "field" }, el("label", { className: "label", htmlFor: sym.id }, "Символ подстановки"), sym),
      el("div", { className: "field" },
        el("label", { className: "label", htmlFor: msk.id }, "Извлечённые маски артикулов"), msk,
        el("div", { className: "small-note" }, "Одна маска на строку"))
    ));
  });

  const banner = el("div", { id: "status" });
  const btn = el("button", { className: "primary", id: "gen-btn", type: "button" }, "Сгенерировать артикулы");
  btn.addEventListener("click", () => startGeneration(btn, banner));
  body.replaceChildren(list, banner, el("div", { className: "footer" }, btn));
}

function validate() {
  let ok = true;

  records.forEach((rec, i) => {
    const checks = [
      [`man-${i}`, (rec.manufacture || "").trim()],
      [`grp-${i}`, (rec.group || "").trim()],
      [`sym-${i}`, (rec.symbol || "").length === 1],
      [`msk-${i}`, (rec.masks || []).length]
    ];
    for (const [id, valid] of checks) {
      if (!valid) { $(id).classList.add("invalid"); ok = false; }
    }
  });

  return ok;
}

function setFormDisabled(disabled) {
  document.querySelectorAll(".modal input, .modal textarea").forEach((node) => { node.disabled = disabled; });
}

/* ---------- Генерация и скачивание ---------- */
async function startGeneration(btn, banner) {
  banner.replaceChildren();

  if (!validate()) {
    setBanner(banner, "error", "Заполните производителя, группу, символ подстановки и маски во всех блоках.");
    return;
  }

  const mySession = session;
  setFormDisabled(true);
  btn.disabled = true;
  btn.replaceChildren(el("span", { className: "spinner small" }), "Генерация…");

  let id;

  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ records })
    });
    const data = await res.json().catch(() => ({}));

    if (mySession !== session) {
      // Окно закрыли, пока шёл запрос: задачу нужно остановить.
      if (res.ok && data.job_id) fetch(`/api/jobs/${data.job_id}`, { method: "DELETE", keepalive: true }).catch(() => {});
      return;
    }
    if (!res.ok) throw new Error(errorText(data, "Не удалось запустить генерацию."));
    id = data.job_id;

  } catch (err) {
    if (mySession !== session) return;
    // Задача не запущена: данные можно поправить и повторить.
    setFormDisabled(false);
    btn.disabled = false;
    btn.replaceChildren("Сгенерировать артикулы");
    setBanner(banner, "error", err.message);
    return;
  }

  jobId = id;
  await pollJob(id, mySession, btn, banner);
}

async function pollJob(id, mySession, btn, banner) {
  let failures = 0;

  while (true) {
    await sleep(POLL_INTERVAL_MS);
    if (mySession !== session || jobId !== id) return;

    let data;

    try {
      const res = await fetch(`/api/jobs/${id}`);
      data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(errorText(data, "Не удалось получить статус задачи."));
      failures = 0;

    } catch (err) {
      failures += 1;
      if (failures < POLL_MAX_FAILURES) continue;
      if (mySession !== session) return;
      generationFailed(btn, banner, err.message);
      return;
    }

    if (data.status === "running") continue;

    if (data.status === "ok") {
      setBanner(banner, "ok", "Архив готов.");
      const download = el("button", { className: "primary", type: "button" }, "Скачать файл");
      download.addEventListener("click", () => { window.location.href = `/api/download/${id}`; });
      btn.replaceWith(download);
    } else {
      generationFailed(btn, banner, data.message);
    }
    return;
  }
}

function generationFailed(btn, banner, message) {
  // После ошибки задачи пользователь закрывает окно и начинает заново.
  btn.replaceChildren("Сгенерировать артикулы");
  setBanner(banner, "error", `Произошла ошибка: ${message}`);
}
