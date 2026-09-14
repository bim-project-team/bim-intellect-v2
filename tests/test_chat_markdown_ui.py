from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_chat_loads_readable_persian_font_and_markdown_renderer():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "Noto+Sans+Arabic" in html
    assert 'src="/static/chat-markdown.js?v=1"' in html
    assert '<textarea\n                  id="chat-input"' in html
    assert html.index('src="/static/chat-markdown.js') < html.index('src="/static/app.js')
    assert '"Noto Sans Arabic"' in css
    assert ".markdown-body" in css
    assert ".md-table-wrap" in css
    assert ".md-citation" in css
    assert "@media (max-width: 860px)" in css
    assert ".md-code-block" in css
    assert ".chat-form textarea" in css
    assert "font-size: clamp(1rem" in css
    assert "max-height: 10rem" in css
    assert "window.ChatMarkdown.render(text)" in app
    assert "window.ChatMarkdown.renderPlain(text)" in app
    assert "window.ChatMarkdown.direction(text)" in app
    assert "resizeChatInput()" in app
    assert 'event.key === "Enter"' in app


def test_markdown_renderer_formats_expected_blocks_and_escapes_raw_html():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")

    renderer = ROOT / "static" / "chat-markdown.js"
    sample = """# عنوان

متن **مهم** با `ID-42` و [پیوند](https://example.com).

- مورد اول
- مورد دوم [Clause 16-3-4-5, Page 70]

1. یک
2. دو

> نقل قول

| نام | مقدار |
| --- | ---: |
| فشار | 10 bar |

```js
const safe = true;
```

<script>alert(1)</script>
[bad](javascript:alert(1))
"""
    javascript = f"""
require({str(renderer)!r});
const html = globalThis.ChatMarkdown.render({sample!r});
const required = ['<h1', '<strong>', '<ul', '<ol', '<blockquote', '<table',
  '<pre', 'class="md-link"', 'class="md-citation"', '&lt;script&gt;',
  'class="md-ltr-token"', 'dir="rtl"', 'dir="ltr"'];
if (!required.every(token => html.includes(token))) process.exit(2);
if (html.includes('<script>') || html.includes('href="javascript:')) process.exit(3);
if (globalThis.ChatMarkdown.direction('This is a primarily English answer with واژه فارسی.') !== 'ltr') process.exit(4);
if (globalThis.ChatMarkdown.direction('این یک پاسخ فارسی درباره ASME16.1 و IFC است.') !== 'rtl') process.exit(5);
if (globalThis.ChatMarkdown.direction('https://example.com IFC-42 123') !== 'ltr') process.exit(6);
const mixed = globalThis.ChatMarkdown.render('این مقدار ASME16.1 برابر ۰.۳۸ متر است: https://example.com/spec.');
if ((mixed.match(/dir="ltr"/g) || []).length < 3 || !mixed.includes('md-bare-url')) process.exit(7);
const plain = globalThis.ChatMarkdown.renderPlain('**literal** <img src=x> IFC-42');
if (!plain.includes('**literal**') || !plain.includes('&lt;img') || plain.includes('<img')) process.exit(8);
"""
    result = subprocess.run(
        [node, "-e", javascript],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
