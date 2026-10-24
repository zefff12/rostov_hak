// Минимальный безопасный рендер Markdown (без внешних зависимостей — работает в закрытом контуре).
(function () {
  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function inline(s) {
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>");
    s = s.replace(/\[([^\]]+)\]\((\/[^)\s]*|https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    s = s.replace(/\[(\d{1,2})\]/g, '<button class="cite" data-n="$1">$1</button>');
    return s;
  }
  window.renderMarkdown = function (md) {
    const lines = md.replace(/\r/g, "").split("\n");
    let html = "", list = null, para = [], code = null, quote = [];
    const flushPara = () => { if (para.length) { html += "<p>" + para.map(inline).join("<br>") + "</p>"; para = []; } };
    const flushList = () => { if (list) { html += `</${list}>`; list = null; } };
    const flushQuote = () => { if (quote.length) { html += "<blockquote>" + quote.map(inline).join("<br>") + "</blockquote>"; quote = []; } };
    for (const raw of lines) {
      if (code !== null) {
        if (/^```/.test(raw)) { html += "<pre>" + esc(code.join("\n")) + "</pre>"; code = null; }
        else code.push(raw);
        continue;
      }
      if (/^```/.test(raw)) { flushPara(); flushList(); flushQuote(); code = []; continue; }
      const line = raw.replace(/^\s{0,3}/, "");
      let m;
      if (/^>\s?/.test(line)) { flushPara(); flushList(); quote.push(line.replace(/^>\s?/, "")); continue; }
      flushQuote();
      if (/^(-{3,}|\*{3,})$/.test(line.trim())) { flushPara(); flushList(); html += "<hr>"; continue; }
      if ((m = line.match(/^#{1,4}\s+(.*)/))) { flushPara(); flushList(); html += "<h3>" + inline(m[1]) + "</h3>"; continue; }
      if ((m = line.match(/^[-•]\s+(.*)/))) {
        flushPara(); if (list !== "ul") { flushList(); html += "<ul>"; list = "ul"; }
        html += "<li>" + inline(m[1]) + "</li>"; continue;
      }
      if ((m = line.match(/^(\d{1,2})[.)]\s+(.*)/))) {
        flushPara(); if (list !== "ol") { flushList(); html += `<ol start="${m[1]}">`; list = "ol"; }
        html += "<li>" + inline(m[2]) + "</li>"; continue;
      }
      if (!line.trim()) { flushPara(); flushList(); continue; }
      if (list && /^\s{2,}/.test(raw)) { html = html.replace(/<\/li>$/, "<br>" + inline(raw.trim()) + "</li>"); continue; }
      flushList(); para.push(line);
    }
    if (code !== null) html += "<pre>" + esc(code.join("\n")) + "</pre>";
    flushPara(); flushList(); flushQuote();
    return html;
  };
})();
