// BIM-Intellect — interface localisation (English / Persian)
//
// Presentation only. This file never touches request payloads, endpoints, or
// response parsing; it swaps interface strings and flips the document's
// writing direction.
//
// Direction strategy
// ------------------
//   · <html dir> is set to ltr/rtl for the chosen interface language, and the
//     stylesheet uses CSS logical properties throughout, so the whole layout
//     mirrors without per-element overrides.
//   · Free text whose language is not known ahead of time — the user's
//     question, a model answer, an IFC element name, a project ID — is marked
//     dir="auto" so the browser derives direction from the content itself. A
//     Persian answer therefore reads RTL even while the interface is English,
//     and an English element name reads LTR inside a Persian interface.
//   · Machine text that must never mirror (clause citations, metrics, the
//     activity log) is pinned LTR in CSS.

(function () {
  "use strict";

  var STORAGE_KEY = "bim-intellect-lang";

  var STRINGS = {
    en: {
      "brand.tagline": "Regulatory compliance agent",
      "nav.label": "Workspace",
      "nav.chat": "Chat",
      "nav.pipeline": "Pipeline",
      "nav.results": "Results",
      "nav.corpus": "Documents",

      "header.chat": "Hybrid retrieval over regulations and the building graph",
      "header.pipeline": "IFC federation, graph import, and clash detection",
      "header.results": "Detected clashes and clearance violations",
      "header.corpus": "Regulation documents in the vector store",

      "status.ready": "Ready",

      "common.refresh": "Refresh",
      "common.clear": "Clear",
      "common.selectAll": "Select all",
      "common.loading": "Loading…",
      "common.error": "Error",
      "common.unknownError": "Unknown error",
      "common.close": "Close",
      "common.menu": "Menu",

      "chat.welcomeTitle": "Welcome to BIM-Intellect",
      "chat.welcomeBody":
        "Ask about code compliance or about elements in the model. Each question is routed to the regulation corpus, the building graph, or both.",
      "chat.tryAsking": "Try asking",
      "chat.example1": "What is the minimum landing depth for stairs?",
      "chat.example2": "Any clearance violations near doors on Level 5?",
      "chat.example3": "List all clashes involving slabs.",
      "chat.placeholder": "Ask about regulations, clashes, or clearances…",
      "chat.inputLabel": "Your question",
      "chat.send": "Send",
      "chat.strongModels": "Stronger models",
      "chat.strongModelsHint":
        "Use stronger models for query understanding and the grounded answer",
      "chat.groundingNote":
        "Answers are limited to retrieved sources. Every regulatory claim carries a clause and page citation.",
      "chat.contextTitle": "Analysis context",
      "chat.contextEmpty":
        "Send a message to see which retrieval paths the orchestrator selects.",
      "chat.you": "You",
      "chat.assistant": "Assistant",
      "chat.noAnswer": "(no answer)",
      "chat.networkError": "Network error",
      "chat.sourcesConsulted": "Sources consulted",
      "chat.sourceRegulations": "Regulations",
      "chat.sourceGraph": "Building graph",
      "chat.sourcesNone": "None — no relevant data found.",
      "chat.citations": "Citations",
      "chat.clause": "Clause",
      "chat.page": "Page",
      "chat.element": "Element",
      "chat.elementId": "Element ID",
      "chat.source": "Source",
      "chat.regulation": "Regulation",
      "chat.modelMode": "Model mode",
      "chat.router": "Router",
      "chat.final": "Final",
      "chat.interpretedQuery": "Interpreted query",
      "chat.retrievalStats":
        "Candidates: {candidates} · Reranked: {reranked} · Context chunks: {chunks}",

      "pipeline.title": "Graph pipeline",
      "pipeline.subtitle":
        "Federate IFC models, import them, then run clash detection.",
      "pipeline.ifcModels": "IFC models",
      "pipeline.ifcDrop": "Drop .ifc files here, or click to select several",
      "pipeline.ifcHint":
        "Each model is parsed independently before it becomes selectable.",
      "pipeline.projectId": "Building / project ID",
      "pipeline.projectIdHint":
        "Only selected models in this project are federated for analysis.",
      "pipeline.storedModels": "Parsed models",
      "pipeline.loadingModels": "Loading models…",
      "pipeline.noModels": "No parsed IFC files in this project.",
      "pipeline.legacyFiles":
        "Files on disk that are not registered yet (not selectable):",
      "pipeline.notImported": "not imported",
      "pipeline.listFailed": "Failed to list IFC models",
      "pipeline.unspecified": "unspecified",
      "pipeline.nodes": "nodes",
      "pipeline.storeyFilter": "Storey filter (optional)",
      "pipeline.typeFilter": "IFC type filter (optional)",
      "pipeline.selectFileFirst": "Select an IFC file first",
      "pipeline.selectFilesFirst": "Select IFC files first",
      "pipeline.scanning": "Scanning files…",
      "pipeline.noStoreys": "No storeys found in this file",
      "pipeline.noTypes": "No types found in this file",
      "pipeline.storeyHint": "Leave empty to include all storeys.",
      "pipeline.typeHint": "Leave empty to include all types.",
      "pipeline.filterTypes": "Filter types…",
      "pipeline.refreshModels": "Refresh model list",
      "pipeline.reset": "Wipe the entire graph before loading (normally leave off)",
      "pipeline.run": "Run full pipeline",
      "pipeline.activity": "Activity log",
      "pipeline.clearLog": "Clear log",
      "pipeline.logIdle": "Idle. Run the pipeline to see activity here.",
      "pipeline.allSelected": "All",
      "pipeline.nSelected": "{n} selected",
      "pipeline.noMatches": "No matches",
      "pipeline.noneFound": "None found",

      "log.federating":
        "Federating {n} IFC model(s), validating alignment, importing, and analysing…",
      "log.ingestDone": "Ingestion complete",
      "log.clashDone": "Clash detection complete",
      "log.pipelineFailed": "Pipeline failed",
      "log.needProjectAndFiles":
        "Enter a project ID and select at least one uploaded IFC model.",
      "log.needProject": "Enter a Building / Project ID first.",
      "log.ifcUploadFailed": "IFC upload failed",
      "log.allMustBeIfc": "Every selected file must be an .ifc file.",
      "log.onlyIfc": "Only .ifc files are accepted.",
      "log.uploadFailedShort": "{n} file(s) — upload failed",
      "log.readyForIngestion": "Parsed and ready for ingestion",
      "log.loadedRows": 'Loaded {n} row(s) into "{tab}".',
      "log.loadFailed": 'Failed to load "{tab}" results',

      "results.title": "Detection results",
      "results.clashes": "Hard clashes",
      "results.violations": "Clearance violations",
      "results.all": "All issues",
      "results.storey": "Storey",
      "results.allStoreys": "All storeys",
      "results.ifcType": "IFC type",
      "results.multiHint": "Ctrl/Cmd-click for multiple. Empty means all types.",
      "results.apply": "Apply",
      "results.colElementA": "Element A",
      "results.colSourceA": "Source A",
      "results.colElementB": "Element B",
      "results.colSourceB": "Source B",
      "results.colIssue": "Issue",
      "results.colMetric": "Metric (model units)",
      "results.empty": "Choose Refresh to load results.",
      "results.loading": "Loading…",
      "results.noResults": "No results.",
      "results.crossFile": "cross-file",
      "results.legacySource": "legacy / unknown",

      "corpus.title": "Add documents",
      "corpus.subtitle": "Upload regulation PDFs to chunk, embed, and index.",
      "corpus.drop": "Drop PDFs here, or click to select several",
      "corpus.docId": "Document ID (optional)",
      "corpus.docIdHint":
        "Used only when exactly one PDF is selected. Batch IDs come from filename and content hash.",
      "corpus.submit": "Process and embed",
      "corpus.statusTitle": "Indexed documents",
      "corpus.clearDb": "Clear",
      "corpus.selectPdf": "Please select one or more PDF files first.",
      "corpus.allMustBePdf": "Every selected file must be a PDF.",
      "corpus.processing": "Processing {n} PDF file(s)…",
      "corpus.uploadFailed": "Upload failed",
      "corpus.chunksIndexed": "{file}: {n} chunks indexed",
      "corpus.fileFailed": "{file}: {error}",
      "corpus.failed": "failed",
      "corpus.confirmClear":
        "Delete the entire vector collection? This cannot be undone.",
      "corpus.clearFailed": "Failed to clear",
      "corpus.collection": "Collection",
      "corpus.indexedChunks": "Indexed chunks",
      "corpus.documents": "Documents",
      "corpus.storage": "Storage",
      "corpus.noDocuments": "No successfully indexed documents.",
      "corpus.indexed": "indexed",
      "corpus.chunks": "chunks",
      "corpus.pages": "pages",
      "corpus.loadFailed": "Failed to load",
      "corpus.selectedFiles": "{n} file(s): {names}",
    },

    fa: {
      "brand.tagline": "دستیار انطباق با مقررات",
      "nav.label": "میزکار",
      "nav.chat": "گفت‌وگو",
      "nav.pipeline": "خط پردازش",
      "nav.results": "نتایج",
      "nav.corpus": "اسناد",

      "header.chat": "بازیابی ترکیبی از مقررات و گراف ساختمان",
      "header.pipeline": "یکپارچه‌سازی IFC، ورود به گراف و تشخیص برخورد",
      "header.results": "برخوردها و نقض فاصله‌های مجاز شناسایی‌شده",
      "header.corpus": "اسناد مقررات در پایگاه برداری",

      "status.ready": "آماده",

      "common.refresh": "بازخوانی",
      "common.clear": "پاک‌کردن",
      "common.selectAll": "انتخاب همه",
      "common.loading": "در حال بارگذاری…",
      "common.error": "خطا",
      "common.unknownError": "خطای نامشخص",
      "common.close": "بستن",
      "common.menu": "منو",

      "chat.welcomeTitle": "به BIM-Intellect خوش آمدید",
      "chat.welcomeBody":
        "درباره انطباق با مقررات یا اجزای مدل بپرسید. هر پرسش به مجموعه مقررات، گراف ساختمان یا هر دو هدایت می‌شود.",
      "chat.tryAsking": "نمونه پرسش",
      "chat.example1": "حداقل عمق پاگرد پله چقدر است؟",
      "chat.example2": "آیا نقض فاصله مجاز نزدیک درها در طبقه ۵ وجود دارد؟",
      "chat.example3": "تمام برخوردهای مربوط به دال‌ها را فهرست کن.",
      "chat.placeholder": "درباره مقررات، برخوردها یا فاصله‌های مجاز بپرسید…",
      "chat.inputLabel": "پرسش شما",
      "chat.send": "ارسال",
      "chat.strongModels": "مدل‌های قوی‌تر",
      "chat.strongModelsHint":
        "استفاده از مدل‌های قوی‌تر برای درک پرسش و تولید پاسخ مستند",
      "chat.groundingNote":
        "پاسخ‌ها فقط بر پایه منابع بازیابی‌شده است. هر ادعای مقرراتی با شماره بند و صفحه ارجاع داده می‌شود.",
      "chat.contextTitle": "زمینه تحلیل",
      "chat.contextEmpty":
        "پیامی بفرستید تا مسیرهای بازیابی انتخاب‌شده نمایش داده شود.",
      "chat.you": "شما",
      "chat.assistant": "دستیار",
      "chat.noAnswer": "(بدون پاسخ)",
      "chat.networkError": "خطای شبکه",
      "chat.sourcesConsulted": "منابع بررسی‌شده",
      "chat.sourceRegulations": "مقررات",
      "chat.sourceGraph": "گراف ساختمان",
      "chat.sourcesNone": "هیچ‌کدام — داده مرتبطی یافت نشد.",
      "chat.citations": "ارجاعات",
      "chat.clause": "بند",
      "chat.page": "صفحه",
      "chat.element": "عضو",
      "chat.elementId": "شناسه عضو",
      "chat.source": "منبع",
      "chat.regulation": "مقررات",
      "chat.modelMode": "حالت مدل",
      "chat.router": "مسیریاب",
      "chat.final": "پاسخ نهایی",
      "chat.interpretedQuery": "پرسش تفسیرشده",
      "chat.retrievalStats":
        "نامزدها: {candidates} · بازرتبه‌بندی: {reranked} · قطعه‌های زمینه: {chunks}",

      "pipeline.title": "خط پردازش گراف",
      "pipeline.subtitle":
        "یکپارچه‌سازی مدل‌های IFC، ورود آن‌ها و سپس تشخیص برخورد.",
      "pipeline.ifcModels": "مدل‌های IFC",
      "pipeline.ifcDrop":
        "فایل‌های ifc. را اینجا رها کنید یا برای انتخاب چندگانه کلیک کنید",
      "pipeline.ifcHint":
        "هر مدل پیش از قابل‌انتخاب شدن، مستقل تجزیه می‌شود.",
      "pipeline.projectId": "شناسه ساختمان / پروژه",
      "pipeline.projectIdHint":
        "تنها مدل‌های انتخاب‌شده در این پروژه برای تحلیل یکپارچه می‌شوند.",
      "pipeline.storedModels": "مدل‌های تجزیه‌شده",
      "pipeline.loadingModels": "در حال بارگذاری مدل‌ها…",
      "pipeline.noModels": "فایل IFC تجزیه‌شده‌ای در این پروژه نیست.",
      "pipeline.legacyFiles":
        "فایل‌های روی دیسک که هنوز ثبت نشده‌اند (قابل انتخاب نیستند):",
      "pipeline.notImported": "وارد نشده",
      "pipeline.listFailed": "فهرست‌کردن مدل‌های IFC ناموفق بود",
      "pipeline.unspecified": "نامشخص",
      "pipeline.nodes": "گره",
      "pipeline.storeyFilter": "فیلتر طبقه (اختیاری)",
      "pipeline.typeFilter": "فیلتر نوع IFC (اختیاری)",
      "pipeline.selectFileFirst": "نخست یک فایل IFC انتخاب کنید",
      "pipeline.selectFilesFirst": "نخست فایل‌های IFC را انتخاب کنید",
      "pipeline.scanning": "در حال بررسی فایل‌ها…",
      "pipeline.noStoreys": "طبقه‌ای در این فایل یافت نشد",
      "pipeline.noTypes": "نوعی در این فایل یافت نشد",
      "pipeline.storeyHint": "برای شامل‌شدن همه طبقات خالی بگذارید.",
      "pipeline.typeHint": "برای شامل‌شدن همه انواع خالی بگذارید.",
      "pipeline.filterTypes": "جست‌وجوی نوع…",
      "pipeline.refreshModels": "بازخوانی فهرست مدل‌ها",
      "pipeline.reset":
        "پاک‌کردن کل گراف پیش از بارگذاری (معمولاً خاموش بماند)",
      "pipeline.run": "اجرای کامل خط پردازش",
      "pipeline.activity": "گزارش فعالیت",
      "pipeline.clearLog": "پاک‌کردن گزارش",
      "pipeline.logIdle": "بی‌کار. برای دیدن فعالیت، خط پردازش را اجرا کنید.",
      "pipeline.allSelected": "همه",
      "pipeline.nSelected": "{n} مورد انتخاب شد",
      "pipeline.noMatches": "موردی یافت نشد",
      "pipeline.noneFound": "موردی یافت نشد",

      "log.federating":
        "یکپارچه‌سازی {n} مدل IFC، بررسی هم‌راستایی، ورود و تحلیل…",
      "log.ingestDone": "بارگذاری کامل شد",
      "log.clashDone": "تشخیص برخورد کامل شد",
      "log.pipelineFailed": "خط پردازش با خطا متوقف شد",
      "log.needProjectAndFiles":
        "شناسه پروژه را وارد کنید و حداقل یک مدل IFC بارگذاری‌شده را انتخاب کنید.",
      "log.needProject": "نخست شناسه ساختمان / پروژه را وارد کنید.",
      "log.ifcUploadFailed": "بارگذاری IFC ناموفق بود",
      "log.allMustBeIfc": "همه فایل‌های انتخاب‌شده باید ifc. باشند.",
      "log.onlyIfc": "فقط فایل‌های ifc. پذیرفته می‌شوند.",
      "log.uploadFailedShort": "{n} فایل — بارگذاری ناموفق بود",
      "log.readyForIngestion": "تجزیه شد و آماده ورود است",
      "log.loadedRows": "{n} سطر در «{tab}» بارگذاری شد.",
      "log.loadFailed": "بارگذاری نتایج «{tab}» ناموفق بود",

      "results.title": "نتایج تشخیص",
      "results.clashes": "برخورد سخت",
      "results.violations": "نقض فاصله مجاز",
      "results.all": "همه موارد",
      "results.storey": "طبقه",
      "results.allStoreys": "همه طبقات",
      "results.ifcType": "نوع IFC",
      "results.multiHint":
        "برای انتخاب چندگانه Ctrl/Cmd را نگه دارید. خالی یعنی همه انواع.",
      "results.apply": "اعمال",
      "results.colElementA": "عضو الف",
      "results.colSourceA": "منبع الف",
      "results.colElementB": "عضو ب",
      "results.colSourceB": "منبع ب",
      "results.colIssue": "نوع مسئله",
      "results.colMetric": "سنجه (یکای مدل)",
      "results.empty": "برای بارگذاری نتایج، بازخوانی را بزنید.",
      "results.loading": "در حال بارگذاری…",
      "results.noResults": "نتیجه‌ای نیست.",
      "results.crossFile": "بین‌فایلی",
      "results.legacySource": "قدیمی / نامشخص",

      "corpus.title": "افزودن سند",
      "corpus.subtitle":
        "برای قطعه‌بندی، جای‌گذاری برداری و نمایه‌سازی، PDF مقررات را بارگذاری کنید.",
      "corpus.drop": "فایل‌های PDF را اینجا رها کنید یا برای انتخاب چندگانه کلیک کنید",
      "corpus.docId": "شناسه سند (اختیاری)",
      "corpus.docIdHint":
        "تنها زمانی به کار می‌رود که فقط یک PDF انتخاب شده باشد. شناسه‌های گروهی از نام فایل و درهم‌ساز محتوا ساخته می‌شوند.",
      "corpus.submit": "پردازش و جای‌گذاری",
      "corpus.statusTitle": "اسناد نمایه‌شده",
      "corpus.clearDb": "پاک‌کردن",
      "corpus.selectPdf": "نخست یک یا چند فایل PDF انتخاب کنید.",
      "corpus.allMustBePdf": "همه فایل‌های انتخاب‌شده باید PDF باشند.",
      "corpus.processing": "در حال پردازش {n} فایل PDF…",
      "corpus.uploadFailed": "بارگذاری ناموفق بود",
      "corpus.chunksIndexed": "{file}: {n} قطعه نمایه شد",
      "corpus.fileFailed": "{file}: {error}",
      "corpus.failed": "ناموفق",
      "corpus.confirmClear":
        "کل مجموعه برداری حذف شود؟ این عمل بازگشت‌پذیر نیست.",
      "corpus.clearFailed": "پاک‌کردن ناموفق بود",
      "corpus.collection": "مجموعه",
      "corpus.indexedChunks": "قطعه‌های نمایه‌شده",
      "corpus.documents": "سندها",
      "corpus.storage": "محل ذخیره",
      "corpus.noDocuments": "سند نمایه‌شده‌ای وجود ندارد.",
      "corpus.indexed": "نمایه‌شده",
      "corpus.chunks": "قطعه",
      "corpus.pages": "صفحه",
      "corpus.loadFailed": "بارگذاری ناموفق بود",
      "corpus.selectedFiles": "{n} فایل: {names}",
    },
  };

  var DIRECTION = { en: "ltr", fa: "rtl" };

  var current = "en";

  /** Look up a string, substituting {placeholder} tokens. Falls back to
   *  English, then to the key itself, so a missing translation is visible
   *  rather than rendering an empty element. */
  function t(key, vars) {
    var table = STRINGS[current] || STRINGS.en;
    var value = table[key];
    if (value === undefined) value = STRINGS.en[key];
    if (value === undefined) return key;

    if (vars) {
      Object.keys(vars).forEach(function (name) {
        value = value.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return value;
  }

  function applyToDom() {
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      el.textContent = t(el.getAttribute("data-i18n"));
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      el.setAttribute("placeholder", t(el.getAttribute("data-i18n-placeholder")));
    });
    document.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      el.setAttribute("title", t(el.getAttribute("data-i18n-title")));
    });

    document.querySelectorAll(".lang-btn").forEach(function (btn) {
      var on = btn.dataset.lang === current;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", String(on));
    });
  }

  function setLanguage(lang, opts) {
    if (!STRINGS[lang]) lang = "en";
    current = lang;

    var html = document.documentElement;
    html.setAttribute("lang", lang);
    html.setAttribute("dir", DIRECTION[lang]);

    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch (e) {
      /* private mode — the preference just won't persist */
    }

    applyToDom();

    if (!opts || opts.notify !== false) {
      document.dispatchEvent(new CustomEvent("languagechange", { detail: { lang: lang } }));
    }
  }

  function storedLanguage() {
    try {
      var saved = localStorage.getItem(STORAGE_KEY);
      if (saved && STRINGS[saved]) return saved;
    } catch (e) {
      /* ignore */
    }
    return (navigator.language || "en").toLowerCase().indexOf("fa") === 0 ? "fa" : "en";
  }

  // Applied immediately (before app.js runs) so the first paint already has the
  // right direction; app.js then renders its dynamic strings in the same locale.
  setLanguage(storedLanguage(), { notify: false });

  window.i18n = {
    t: t,
    get lang() {
      return current;
    },
    get dir() {
      return DIRECTION[current];
    },
    setLanguage: setLanguage,
    apply: applyToDom,
  };

  document.addEventListener("DOMContentLoaded", function () {
    // Re-apply: the tables above were only partially in the DOM when this
    // script ran in <head> order relative to the markup.
    applyToDom();

    document.querySelectorAll(".lang-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setLanguage(btn.dataset.lang);
      });
    });
  });
})();
