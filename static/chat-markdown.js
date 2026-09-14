/* Safe, dependency-free Markdown rendering for model responses.
 *
 * Raw HTML is always escaped. Only the small, explicit element set below can
 * be produced, which keeps chat output safe without relying on a CDN parser or
 * sanitizer. The renderer intentionally covers the structures used by model
 * answers: headings, paragraphs, emphasis, lists, quotes, links, code, tables,
 * and BIM-Intellect citations.
 */
(function exposeChatMarkdown(global) {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function safeLink(rawUrl) {
    const url = String(rawUrl || "").trim();
    if (/^(?:https?:\/\/|mailto:|\/|#)/i.test(url)) return url;
    return "#";
  }

  function detectDirection(value) {
    const original = String(value == null ? "" : value);
    const text = original
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/`[^`\n]+`/g, " ")
      .replace(/https?:\/\/\S+|mailto:\S+/gi, " ")
      .replace(/\[(?:Clause|Document)\s+[^\]\n]+\]/gi, " ")
      .replace(/\b[A-Z]{2,}[A-Z0-9_.:/-]*\b/g, " ");
    let rtlLetters = 0;
    let ltrLetters = 0;
    let firstDirection = "ltr";
    let foundFirst = false;

    for (const character of text) {
      if (!/\p{L}/u.test(character)) continue;
      const rtl = /[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFC]/u.test(character);
      const latin = /\p{Script=Latin}/u.test(character);
      if (!rtl && !latin) continue;
      if (!foundFirst) {
        firstDirection = rtl ? "rtl" : "ltr";
        foundFirst = true;
      }
      if (rtl) rtlLetters += 1;
      if (latin) ltrLetters += 1;
    }

    if (rtlLetters === ltrLetters) return foundFirst ? firstDirection : "ltr";
    return rtlLetters > ltrLetters ? "rtl" : "ltr";
  }

  function isolateLtrTokens(value, hold) {
    let text = String(value == null ? "" : value);
    text = text.replace(/https?:\/\/[^\s<>()]+/gi, (rawUrl) => {
      const punctuation = (rawUrl.match(/[.,!?;:،؛]+$/u) || [""])[0];
      const url = punctuation ? rawUrl.slice(0, -punctuation.length) : rawUrl;
      return hold(
        `<a class="md-link md-bare-url" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" dir="ltr">${escapeHtml(url)}</a>`
      ) + punctuation;
    });
    text = text.replace(
      /\b(?:[A-Z]{2,}[A-Z0-9_.:/-]*|[A-Za-z]+(?:[-_/.:][A-Za-z0-9]+)+|[A-Za-z]*\d+[A-Za-z0-9_.:/-]*)\b/g,
      (token) => hold(`<bdi class="md-ltr-token" dir="ltr">${escapeHtml(token)}</bdi>`)
    );
    text = text.replace(
      /(^|[^\p{L}\p{N}_])([+−-]?[۰-۹٠-٩\d]+(?:[./٫][۰-۹٠-٩\d]+)*%?)(?=$|[^\p{L}\p{N}_])/gu,
      (_, prefix, number) => `${prefix}${hold(`<bdi class="md-ltr-token md-number" dir="ltr">${escapeHtml(number)}</bdi>`)}`
    );
    return text;
  }

  function createTokenStore() {
    const tokens = [];
    return {
      hold(html) {
        const marker = String.fromCharCode(0xE000 + tokens.length);
        tokens.push({ marker, html });
        return marker;
      },
      restore(value) {
        let text = value;
        tokens.forEach(({ marker, html }) => {
          text = text.split(marker).join(html);
        });
        return text;
      },
    };
  }

  function renderInline(value) {
    let text = String(value == null ? "" : value);
    const store = createTokenStore();
    const hold = store.hold;

    text = text.replace(/`([^`\n]+)`/g, (_, code) =>
      hold(`<code class="md-inline-code" dir="ltr">${escapeHtml(code)}</code>`)
    );

    text = text.replace(/\[([^\]\n]+)\]\(([^\s)]+)(?:\s+["']([^"']*)["'])?\)/g,
      (_, label, url, title) => {
        const safe = safeLink(url);
        const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
        const target = /^https?:\/\//i.test(safe)
          ? ' target="_blank" rel="noopener noreferrer"'
          : "";
        return hold(
          `<a class="md-link" href="${escapeHtml(safe)}"${titleAttr}${target} dir="${detectDirection(label)}">${escapeHtml(label)}</a>`
        );
      }
    );

    text = text.replace(/\[(?:Clause|Document)\s+[^\]\n]+\]/gi, (citation) =>
      hold(`<span class="md-citation" dir="ltr" title="Source citation">${escapeHtml(citation)}</span>`)
    );

    text = isolateLtrTokens(text, hold);
    text = escapeHtml(text);
    text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    text = text.replace(/(^|[^_])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");
    text = text.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");

    return store.restore(text);
  }

  function renderPlain(value) {
    const store = createTokenStore();
    let text = String(value == null ? "" : value);
    text = text.replace(/\[(?:Clause|Document)\s+[^\]\n]+\]/gi, (citation) =>
      store.hold(`<span class="md-citation" dir="ltr">${escapeHtml(citation)}</span>`)
    );
    text = isolateLtrTokens(text, store.hold);
    return store.restore(escapeHtml(text));
  }

  function splitTableRow(line) {
    let source = String(line || "").trim();
    if (source.startsWith("|")) source = source.slice(1);
    if (source.endsWith("|")) source = source.slice(0, -1);
    const cells = [];
    let cell = "";
    let escaped = false;
    for (const char of source) {
      if (escaped) {
        cell += char;
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === "|") {
        cells.push(cell.trim());
        cell = "";
      } else {
        cell += char;
      }
    }
    cells.push(cell.trim());
    return cells;
  }

  function isTableDivider(line) {
    const cells = splitTableRow(line);
    return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  }

  function tableAlignment(cell) {
    const value = String(cell || "").trim();
    if (value.startsWith(":") && value.endsWith(":")) return "center";
    if (value.endsWith(":")) return "end";
    if (value.startsWith(":")) return "start";
    return "auto";
  }

  function startsBlock(lines, index) {
    const line = lines[index] || "";
    if (!line.trim()) return true;
    if (/^\s*```/.test(line)) return true;
    if (/^\s{0,3}#{1,6}\s+/.test(line)) return true;
    if (/^\s{0,3}>\s?/.test(line)) return true;
    if (/^\s{0,3}(?:[-+*]|\d+[.)])\s+/.test(line)) return true;
    if (/^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) return true;
    return index + 1 < lines.length && line.includes("|") && isTableDivider(lines[index + 1]);
  }

  function renderMarkdown(markdown) {
    const lines = String(markdown == null ? "" : markdown)
      .replace(/\r\n?/g, "\n")
      .split("\n");
    const output = [];
    let index = 0;

    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }

      const fence = line.match(/^\s*```\s*([\w.+-]*)\s*$/);
      if (fence) {
        const language = (fence[1] || "").replace(/[^\w.+-]/g, "");
        const code = [];
        index += 1;
        while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
          code.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        const languageClass = language ? ` language-${escapeHtml(language)}` : "";
        output.push(
          `<pre class="md-code-block" dir="ltr"><code class="${languageClass.trim()}">${escapeHtml(code.join("\n"))}</code></pre>`
        );
        continue;
      }

      const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) {
        const level = Math.min(heading[1].length, 3);
        output.push(`<h${level} class="md-heading md-h${level}" dir="${detectDirection(heading[2])}">${renderInline(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }

      if (index + 1 < lines.length && line.includes("|") && isTableDivider(lines[index + 1])) {
        const headers = splitTableRow(line);
        const alignments = splitTableRow(lines[index + 1]).map(tableAlignment);
        const rows = [];
        index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
          rows.push(splitTableRow(lines[index]));
          index += 1;
        }
        const head = headers.map((cell, cellIndex) =>
          `<th dir="${detectDirection(cell)}" data-align="${alignments[cellIndex] || "auto"}">${renderInline(cell)}</th>`
        ).join("");
        const body = rows.map((row) => `<tr>${headers.map((_, cellIndex) =>
          `<td dir="${detectDirection(row[cellIndex] || "")}" data-align="${alignments[cellIndex] || "auto"}">${renderInline(row[cellIndex] || "")}</td>`
        ).join("")}</tr>`).join("");
        const tableText = headers.concat(rows.flat()).join(" ");
        output.push(`<div class="md-table-wrap" role="region" aria-label="Markdown table" tabindex="0" dir="${detectDirection(tableText)}"><table class="md-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`);
        continue;
      }

      const quote = line.match(/^\s{0,3}>\s?(.*)$/);
      if (quote) {
        const quoteLines = [];
        while (index < lines.length) {
          const match = lines[index].match(/^\s{0,3}>\s?(.*)$/);
          if (!match) break;
          quoteLines.push(match[1]);
          index += 1;
        }
        output.push(`<blockquote class="md-blockquote" dir="${detectDirection(quoteLines.join(" "))}">${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
        continue;
      }

      const listItem = line.match(/^\s{0,3}([-+*]|\d+[.)])\s+(.+)$/);
      if (listItem) {
        const ordered = /^\d/.test(listItem[1]);
        const tag = ordered ? "ol" : "ul";
        const startNumber = ordered ? parseInt(listItem[1], 10) : 1;
        const items = [];
        const itemTexts = [];
        while (index < lines.length) {
          const match = lines[index].match(/^\s{0,3}([-+*]|\d+[.)])\s+(.+)$/);
          if (!match || /^\d/.test(match[1]) !== ordered) break;
          itemTexts.push(match[2]);
          items.push(`<li dir="${detectDirection(match[2])}">${renderInline(match[2])}</li>`);
          index += 1;
        }
        const start = ordered && startNumber !== 1 ? ` start="${startNumber}"` : "";
        output.push(`<${tag} class="md-list" dir="${detectDirection(itemTexts.join(" "))}"${start}>${items.join("")}</${tag}>`);
        continue;
      }

      if (/^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        output.push('<hr class="md-divider">');
        index += 1;
        continue;
      }

      const paragraph = [];
      while (index < lines.length && lines[index].trim() && (paragraph.length === 0 || !startsBlock(lines, index))) {
        paragraph.push(lines[index].trim());
        index += 1;
      }
      if (paragraph.length) {
        output.push(`<p class="md-paragraph" dir="${detectDirection(paragraph.join(" "))}">${paragraph.map(renderInline).join("<br>")}</p>`);
      } else {
        index += 1;
      }
    }

    return output.join("");
  }

  global.ChatMarkdown = Object.freeze({
    render: renderMarkdown,
    renderPlain,
    direction: detectDirection,
    escapeHtml,
  });
})(typeof window !== "undefined" ? window : globalThis);
