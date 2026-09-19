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
      "nav.pipeline": "IFC Pipeline",
      "nav.results": "Clash Results",
      "nav.sustainability": "Sustainability Score",
      "nav.corpus": "PDF Documents",

      "header.chat": "Hybrid retrieval over regulations and the building graph",
      "header.pipeline": "IFC federation, graph import, and clash detection",
      "header.results": "Detected clashes and clearance violations",
      "header.sustainability": "Deterministic carbon evidence and LEED-oriented findings",
      "header.corpus": "Regulation documents in the vector store",

      "status.ready": "Ready",

      "common.refresh": "Refresh",
      "common.clear": "Clear",
      "common.selectAll": "Select All",
      "common.deleteSelected": "Delete Selected",
      "common.delete": "Delete",
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
      "chat.example4": "What is this project's estimated embodied carbon?",
      "chat.example5": "Which materials contribute most, and what LEED guidance applies?",
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
      "chat.sourceSustainability": "Deterministic sustainability results",
      "chat.sourcesNone": "None — no relevant data found.",
      "chat.citations": "Citations",
      "chat.clause": "Clause",
      "chat.page": "Page",
      "chat.section": "Section",
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

      "viewer.title": "3D Visualization",
      "viewer.reset": "Reset view",
      "viewer.empty": "Ask about elements in the model to see them here.",
      "viewer.open": "View in 3D",
      "viewer.openRelated": "Show {types} in 3D",
      "viewer.loading": "Loading model geometry…",
      "viewer.elements": "{n} element(s) highlighted",
      "viewer.scenes": "Storeys shown: {names}",
      "viewer.boxFallback":
        "Showing bounding boxes: no exported 3D geometry exists for this project. Re-run the pipeline to generate it.",
      "viewer.mappingFailed":
        "Exported 3D geometry exists, but no matching scene could be resolved for the selected elements.",
      "viewer.relatedNotice":
        "Not evidence for this answer — showing all {types} in the model for orientation only.",
      "viewer.unavailable": "No 3D geometry is available for this project.",
      "viewer.truncated": "Showing the first {n} elements only.",
      "viewer.sceneLimit": "Showing {shown} of {total} storeys to keep the download small.",
      "viewer.noMatch":
        "The identified elements are not present in the exported geometry. Re-run the pipeline for this project.",

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
      "pipeline.typeHint": "Use Select all to include every IFC type.",
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
      "pipeline.confirmDelete": "Delete {n} selected IFC model(s)? Their graph elements, clash results, generated scenes, and model-specific sustainability results will also be removed.",
      "pipeline.deleteFailed": "Failed to delete selected IFC models",
      "pipeline.deleted": "Deleted {n} IFC model(s)",
      "pipeline.selectForAnalysis": "Include {name} in analysis",
      "pipeline.selectForDeletion": "Select {name} for deletion",

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
      "results.allTypes": "ALL",
      "results.apply": "Apply",
      "results.colElementA": "Element A",
      "results.colSourceA": "Source A",
      "results.colElementB": "Element B",
      "results.colSourceB": "Source B",
      "results.elementId": "Element ID",
      "results.ifcGuid": "IFC GUID",
      "results.sourceFile": "Source File",
      "results.discipline": "Discipline",
      "results.relation": "Relation",
      "results.colIssue": "Issue",
      "results.colMetric": "Metric (model units; clash volume units³)",
      "results.empty": "No issues found for the selected filters.",
      "results.loading": "Loading…",
      "results.noResults": "No results.",
      "results.crossFile": "CROSS-FILE",
      "results.intraFile": "INTRA-FILE",
      "results.scrollRegion": "Scrollable clash results",
      "results.legacySource": "legacy / unknown",
      "results.count": "Showing {n} result(s)",
      "results.issueClash": "Hard clash",
      "results.issueClearance": "Clearance violation",
      "results.exportCsv": "Export CSV",
      "results.exportCsvHint": "Download the current table as CSV",
      "results.csv.type": "Type",
      "results.csv.name": "Name",
      "results.viewLabel": "Result view",
      "results.viewCards": "Card view",
      "results.viewTable": "Table view",
      "results.sourceModel": "Source model",
      "results.allModels": "All models",
      "results.searchLabel": "Search",
      "results.search": "Search element, GUID, model…",
      "results.sortBy": "Sort",
      "results.sortMetricDesc": "Highest metric first",
      "results.sortMetricAsc": "Lowest metric first",
      "results.sortType": "Element type",
      "results.sortSourceFile": "Source file",
      "results.crossFileOnly": "Cross-file only",
      "results.crossFileLabel": "Cross-file",
      "results.sameModelLabel": "Same model",
      "results.viewDetails": "View details",
      "results.hideDetails": "Hide details",
      "results.copyRefs": "Copy references",
      "results.copied": "Copied",
      "results.overlap": "Overlap",
      "results.clearanceMetric": "Clearance",
      "results.modelUnits": "model units",
      "results.modelUnitsCubic": "model units³",
      "results.showingRange": "Showing {shown} of {total} results",
      "results.loadMore": "Load more",
      "results.loadError": "Unable to load results",
      "results.graphId": "Graph ID",
      "results.anomalyScore": "Anomaly score",
      "results.projectLabel": "Project",
      "results.clashesWith": "clashes with",

      "sustainability.title": "Sustainability analysis",
      "sustainability.subtitle": "Deterministic embodied-carbon results for the active project and IFC scope.",
      "sustainability.project": "Project",
      "sustainability.scope": "IFC scope",
      "sustainability.selectedModels": "Selected IFC files",
      "sustainability.allModels": "All ingested project models",
      "sustainability.allowDerived": "Allow low-quality geometry-derived estimates when explicit quantities are unavailable",
      "sustainability.run": "Run analysis",
      "sustainability.empty": "No analysis loaded for this exact scope.",
      "sustainability.loading": "Loading sustainability results for this exact scope…",
      "sustainability.running": "Extracting IFC evidence and running deterministic analysis…",
      "sustainability.success": "Analysis loaded for this project and IFC scope.",
      "sustainability.partial": "Partial result: unevaluated or estimated records are shown in Data quality.",
      "sustainability.backendError": "Sustainability backend error",
      "sustainability.noProject": "Enter a project ID in Pipeline.",
      "sustainability.noSelectedFiles": "Select at least one IFC file in Pipeline, or choose all project models.",
      "sustainability.scopeFiles": "{n} model(s): {names}",
      "sustainability.noAnalysis": "Run or load an analysis to see results.",
      "sustainability.estimatedCarbon": "Estimated embodied carbon",
      "sustainability.elementsEvaluated": "Elements evaluated",
      "sustainability.elementsNotEvaluated": "Elements not evaluated",
      "sustainability.explicitCoverage": "Explicit quantity coverage",
      "sustainability.estimatedCoverage": "Estimated quantity coverage",
      "sustainability.unmatchedMaterials": "Unmatched materials",
      "sustainability.breakdown": "Carbon breakdown",
      "sustainability.byMaterial": "Material",
      "sustainability.byIfcType": "IFC type",
      "sustainability.byDiscipline": "Discipline",
      "sustainability.byFile": "Source IFC file",
      "sustainability.topContributors": "Top contributors",
      "sustainability.element": "Element",
      "sustainability.guid": "IFC GUID",
      "sustainability.sourceFile": "Source IFC file",
      "sustainability.material": "Material",
      "sustainability.quantity": "Quantity",
      "sustainability.factor": "Factor",
      "sustainability.kgco2e": "kgCO2e",
      "sustainability.ifcQuantity": "IFC quantity",
      "sustainability.estimated": "Estimated",
      "sustainability.dataQuality": "Data quality",
      "sustainability.missingMaterial": "Missing material",
      "sustainability.missingQuantity": "Missing quantity",
      "sustainability.ambiguousQuantity": "Ambiguous quantity",
      "sustainability.ambiguousMapping": "Ambiguous material mapping",
      "sustainability.missingFactor": "Missing carbon factor",
      "sustainability.unitIncompatibility": "Unit incompatibility",
      "sustainability.derivedQuantity": "Derived / estimated quantity",
      "sustainability.status": "Status",
      "sustainability.reason": "Reason",
      "sustainability.leedAssessment": "LEED / sustainability assessment",
      "sustainability.leedHint": "Findings appear after a grounded LEED assessment question is asked in Chat. They are session-only and are not certification decisions.",
      "sustainability.criterion": "Criterion / requirement",
      "sustainability.evidence": "Evidence",
      "sustainability.citation": "Document citation",
      "sustainability.missingData": "Missing project data",
      "sustainability.noFindings": "No grounded assessment findings for this scope in the current chat session.",
      "sustainability.reports": "Reports",
      "sustainability.reportHint": "Downloads preserve scope, factor provenance, breakdowns, exclusions, findings, citations, and limitations.",
      "sustainability.reportFailed": "Report download failed",
      "sustainability.status.satisfied_from_available_evidence": "Satisfied from available evidence",
      "sustainability.status.not_satisfied_from_available_evidence": "Not satisfied from available evidence",
      "sustainability.status.insufficient_evidence": "Insufficient evidence",
      "sustainability.status.not_automatically_evaluable": "Not automatically evaluable",

      "corpus.title": "Add documents",
      "corpus.subtitle": "Upload regulation PDFs to chunk, embed, and index.",
      "corpus.drop": "Drop PDFs here, or click to select several",
      "corpus.docId": "Document ID (optional)",
      "corpus.docIdHint":
        "Used only when exactly one PDF is selected. Batch IDs come from filename and content hash.",
      "corpus.domain": "Document domain",
      "corpus.domainRegulation": "Regulation",
      "corpus.domainSustainability": "Sustainability",
      "corpus.domainStandard": "Standard",
      "corpus.standardName": "Standard name",
      "corpus.standardVersion": "Standard version",
      "corpus.standardHint": "Version is required for LEED and standard documents.",
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
      "corpus.confirmDelete": "Delete {n} selected PDF document(s) and all of their indexed chunks?",
      "corpus.deleteFailed": "Failed to delete selected documents",
      "corpus.deleted": "Deleted {n} document(s)",
      "corpus.selectDocument": "Select PDF document {name}",
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
      "nav.pipeline": "خط لوله IFC",
      "nav.results": "نتایج برخورد",
      "nav.sustainability": "امتیاز پایداری",
      "nav.corpus": "اسناد PDF",

      "header.chat": "بازیابی ترکیبی از مقررات و گراف ساختمان",
      "header.pipeline": "یکپارچه‌سازی IFC، ورود به گراف و تشخیص برخورد",
      "header.results": "برخوردها و نقض فاصله‌های مجاز شناسایی‌شده",
      "header.sustainability": "شواهد قطعی کربن و یافته‌های ارزیابی‌محور LEED",
      "header.corpus": "اسناد مقررات در پایگاه برداری",

      "status.ready": "آماده",

      "common.refresh": "بازخوانی",
      "common.clear": "پاک‌کردن",
      "common.selectAll": "انتخاب همه",
      "common.deleteSelected": "حذف انتخاب‌شده‌ها",
      "common.delete": "حذف",
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
      "chat.example4": "کربن نهفته برآوردشده این پروژه چقدر است؟",
      "chat.example5": "کدام مصالح بیشترین سهم را دارند و چه راهنمای LEED مرتبط است؟",
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
      "chat.sourceSustainability": "نتایج قطعی پایداری",
      "chat.sourcesNone": "هیچ‌کدام — داده مرتبطی یافت نشد.",
      "chat.citations": "ارجاعات",
      "chat.clause": "بند",
      "chat.page": "صفحه",
      "chat.section": "بخش",
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

      "viewer.title": "نمایش سه‌بعدی",
      "viewer.reset": "بازنشانی نما",
      "viewer.empty": "درباره اعضای مدل بپرسید تا اینجا نمایش داده شوند.",
      "viewer.open": "نمایش سه‌بعدی",
      "viewer.openRelated": "نمایش سه‌بعدی {types}",
      "viewer.loading": "در حال بارگذاری هندسه مدل…",
      "viewer.elements": "{n} عضو مشخص شد",
      "viewer.scenes": "طبقات نمایش‌داده‌شده: {names}",
      "viewer.boxFallback":
        "نمایش جعبه‌های مرزی: برای این پروژه هیچ هندسه سه‌بعدی تولید نشده است. برای ساخت آن خط پردازش را دوباره اجرا کنید.",
      "viewer.mappingFailed":
        "هندسه سه‌بعدی تولیدشده وجود دارد، اما صحنه منطبقی برای اعضای انتخاب‌شده یافت نشد.",
      "viewer.relatedNotice":
        "شواهد این پاسخ نیست — تنها برای موقعیت‌یابی، همه {types} مدل نمایش داده می‌شود.",
      "viewer.unavailable": "هندسه سه‌بعدی برای این پروژه در دسترس نیست.",
      "viewer.truncated": "تنها {n} عضو نخست نمایش داده می‌شود.",
      "viewer.sceneLimit": "برای کوچک ماندن حجم بارگذاری، {shown} طبقه از {total} طبقه نمایش داده می‌شود.",
      "viewer.noMatch":
        "اعضای شناسایی‌شده در هندسه تولیدشده وجود ندارند. خط پردازش این پروژه را دوباره اجرا کنید.",

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
      "pipeline.typeHint": "برای شامل‌شدن همه انواع IFC، «انتخاب همه» را بزنید.",
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
      "pipeline.confirmDelete": "{n} مدل IFC انتخاب‌شده حذف شوند؟ عناصر گراف، نتایج برخورد، صحنه‌های تولیدشده و نتایج پایداری وابسته نیز حذف می‌شوند.",
      "pipeline.deleteFailed": "حذف مدل‌های IFC انتخاب‌شده ناموفق بود",
      "pipeline.deleted": "{n} مدل IFC حذف شد",
      "pipeline.selectForAnalysis": "افزودن {name} به محدوده تحلیل",
      "pipeline.selectForDeletion": "انتخاب {name} برای حذف",

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
      "results.allTypes": "همه",
      "results.apply": "اعمال",
      "results.colElementA": "عضو الف",
      "results.colSourceA": "منبع الف",
      "results.colElementB": "عضو ب",
      "results.colSourceB": "منبع ب",
      "results.elementId": "شناسه عنصر",
      "results.ifcGuid": "شناسه IFC GUID",
      "results.sourceFile": "فایل منبع",
      "results.discipline": "رشته تخصصی",
      "results.relation": "نوع ارتباط",
      "results.colIssue": "نوع مسئله",
      "results.colMetric": "سنجه (یکای مدل؛ حجم برخورد به یکای مدل مکعب)",
      "results.empty": "نتیجه‌ای برای فیلترهای انتخابی نیست.",
      "results.loading": "در حال بارگذاری…",
      "results.noResults": "نتیجه‌ای نیست.",
      "results.crossFile": "بین‌فایلی",
      "results.intraFile": "درون‌فایلی",
      "results.scrollRegion": "نتایج برخورد با پیمایش افقی",
      "results.legacySource": "قدیمی / نامشخص",
      "results.count": "نمایش {n} نتیجه",
      "results.issueClash": "برخورد سخت",
      "results.issueClearance": "نقض فاصله مجاز",
      "results.exportCsv": "خروجی CSV",
      "results.exportCsvHint": "دانلود جدول فعلی به‌صورت CSV",
      "results.csv.type": "نوع",
      "results.csv.name": "نام",
      "results.viewLabel": "نمای نتایج",
      "results.viewCards": "نمای کارت",
      "results.viewTable": "نمای جدول",
      "results.sourceModel": "مدل منبع",
      "results.allModels": "همه مدل‌ها",
      "results.searchLabel": "جست‌وجو",
      "results.search": "جست‌وجوی عنصر، GUID، مدل…",
      "results.sortBy": "مرتب‌سازی",
      "results.sortMetricDesc": "بیشترین سنجه در ابتدا",
      "results.sortMetricAsc": "کمترین سنجه در ابتدا",
      "results.sortType": "نوع عنصر",
      "results.sortSourceFile": "فایل منبع",
      "results.crossFileOnly": "فقط بین‌فایلی",
      "results.crossFileLabel": "بین‌فایلی",
      "results.sameModelLabel": "درون‌فایلی",
      "results.viewDetails": "مشاهده جزئیات",
      "results.hideDetails": "بستن جزئیات",
      "results.copyRefs": "کپی ارجاع‌ها",
      "results.copied": "کپی شد",
      "results.overlap": "هم‌پوشانی",
      "results.clearanceMetric": "فاصله",
      "results.modelUnits": "یکای مدل",
      "results.modelUnitsCubic": "یکای مدل مکعب",
      "results.showingRange": "نمایش {shown} از {total} نتیجه",
      "results.loadMore": "نمایش بیشتر",
      "results.loadError": "بارگذاری نتایج ناموفق بود",
      "results.graphId": "شناسه گراف",
      "results.anomalyScore": "امتیاز ناهنجاری",
      "results.projectLabel": "پروژه",
      "results.clashesWith": "برخورد با",

      "sustainability.title": "تحلیل پایداری",
      "sustainability.subtitle": "نتایج قطعی کربن نهفته برای پروژه و دامنه IFC فعال.",
      "sustainability.project": "پروژه",
      "sustainability.scope": "دامنه IFC",
      "sustainability.selectedModels": "فایل‌های IFC انتخاب‌شده",
      "sustainability.allModels": "همه مدل‌های واردشده پروژه",
      "sustainability.allowDerived": "در نبود مقادیر صریح، برآوردهای هندسی کم‌کیفیت مجاز باشد",
      "sustainability.run": "اجرای تحلیل",
      "sustainability.empty": "تحلیلی برای این دامنه دقیق بارگذاری نشده است.",
      "sustainability.loading": "در حال بارگذاری نتایج پایداری این دامنه دقیق…",
      "sustainability.running": "در حال استخراج شواهد IFC و اجرای تحلیل قطعی…",
      "sustainability.success": "تحلیل این پروژه و دامنه IFC بارگذاری شد.",
      "sustainability.partial": "نتیجه ناقص: رکوردهای ارزیابی‌نشده یا برآوردی در کیفیت داده نمایش داده شده‌اند.",
      "sustainability.backendError": "خطای سامانه پایداری",
      "sustainability.noProject": "شناسه پروژه را در خط پردازش وارد کنید.",
      "sustainability.noSelectedFiles": "حداقل یک فایل IFC را در خط پردازش انتخاب کنید یا همه مدل‌های پروژه را برگزینید.",
      "sustainability.scopeFiles": "{n} مدل: {names}",
      "sustainability.noAnalysis": "برای دیدن نتایج، تحلیل را اجرا یا بارگذاری کنید.",
      "sustainability.estimatedCarbon": "کربن نهفته برآوردشده",
      "sustainability.elementsEvaluated": "عناصر بررسی‌شده",
      "sustainability.elementsNotEvaluated": "عناصر ارزیابی‌نشده",
      "sustainability.explicitCoverage": "پوشش مقادیر صریح",
      "sustainability.estimatedCoverage": "پوشش مقادیر برآوردی",
      "sustainability.unmatchedMaterials": "مصالح تطبیق‌نیافته",
      "sustainability.breakdown": "تفکیک کربن",
      "sustainability.byMaterial": "مصالح",
      "sustainability.byIfcType": "نوع IFC",
      "sustainability.byDiscipline": "رشته",
      "sustainability.byFile": "فایل IFC منبع",
      "sustainability.topContributors": "بیشترین مشارکت‌کنندگان",
      "sustainability.element": "عنصر",
      "sustainability.guid": "شناسه IFC",
      "sustainability.sourceFile": "فایل IFC منبع",
      "sustainability.material": "مصالح",
      "sustainability.quantity": "مقدار",
      "sustainability.factor": "ضریب",
      "sustainability.kgco2e": "kgCO2e",
      "sustainability.ifcQuantity": "مقدار IFC",
      "sustainability.estimated": "برآوردی",
      "sustainability.dataQuality": "کیفیت داده",
      "sustainability.missingMaterial": "مصالح مفقود",
      "sustainability.missingQuantity": "مقدار مفقود",
      "sustainability.ambiguousQuantity": "مقدار مبهم",
      "sustainability.ambiguousMapping": "تطبیق مبهم مصالح",
      "sustainability.missingFactor": "ضریب کربن مفقود",
      "sustainability.unitIncompatibility": "ناسازگاری یکا",
      "sustainability.derivedQuantity": "مقدار مشتق‌شده / برآوردی",
      "sustainability.status": "وضعیت",
      "sustainability.reason": "دلیل",
      "sustainability.leedAssessment": "ارزیابی پایداری / LEED",
      "sustainability.leedHint": "یافته‌ها پس از طرح پرسش ارزیابی LEED مستند در گفت‌وگو ظاهر می‌شوند. این موارد فقط در نشست نگهداری می‌شوند و تصمیم گواهی نیستند.",
      "sustainability.criterion": "معیار / الزام",
      "sustainability.evidence": "شواهد",
      "sustainability.citation": "ارجاع سند",
      "sustainability.missingData": "داده مفقود پروژه",
      "sustainability.noFindings": "در نشست گفت‌وگوی فعلی یافته مستندی برای این دامنه وجود ندارد.",
      "sustainability.reports": "گزارش‌ها",
      "sustainability.reportHint": "دانلودها دامنه، منشأ ضرایب، تفکیک‌ها، موارد حذف‌شده، یافته‌ها، ارجاعات و محدودیت‌ها را حفظ می‌کنند.",
      "sustainability.reportFailed": "دانلود گزارش ناموفق بود",
      "sustainability.status.satisfied_from_available_evidence": "بر پایه شواهد موجود برآورده است",
      "sustainability.status.not_satisfied_from_available_evidence": "بر پایه شواهد موجود برآورده نیست",
      "sustainability.status.insufficient_evidence": "شواهد ناکافی",
      "sustainability.status.not_automatically_evaluable": "قابل ارزیابی خودکار نیست",

      "corpus.title": "افزودن سند",
      "corpus.subtitle":
        "برای قطعه‌بندی، جای‌گذاری برداری و نمایه‌سازی، PDF مقررات را بارگذاری کنید.",
      "corpus.drop": "فایل‌های PDF را اینجا رها کنید یا برای انتخاب چندگانه کلیک کنید",
      "corpus.docId": "شناسه سند (اختیاری)",
      "corpus.docIdHint":
        "تنها زمانی به کار می‌رود که فقط یک PDF انتخاب شده باشد. شناسه‌های گروهی از نام فایل و درهم‌ساز محتوا ساخته می‌شوند.",
      "corpus.domain": "حوزه سند",
      "corpus.domainRegulation": "مقررات",
      "corpus.domainSustainability": "پایداری",
      "corpus.domainStandard": "استاندارد",
      "corpus.standardName": "نام استاندارد",
      "corpus.standardVersion": "نسخه استاندارد",
      "corpus.standardHint": "نسخه برای اسناد LEED و استاندارد الزامی است.",
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
      "corpus.confirmDelete": "{n} سند PDF انتخاب‌شده و همه قطعه‌های نمایه‌شده آن‌ها حذف شوند؟",
      "corpus.deleteFailed": "حذف اسناد انتخاب‌شده ناموفق بود",
      "corpus.deleted": "{n} سند حذف شد",
      "corpus.selectDocument": "انتخاب سند PDF {name}",
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
    document.querySelectorAll("[data-i18n-aria-label]").forEach(function (el) {
      el.setAttribute("aria-label", t(el.getAttribute("data-i18n-aria-label")));
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
