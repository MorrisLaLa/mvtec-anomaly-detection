"""把 frontend/expense-demo.html 打包成單一、自帶模擬伺服器的示範頁 frontend/demo.html（可直接發布為 Artifact）。
正式版 expense-demo.html 不被修改。用法：python scripts/build_demo.py"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = (ROOT / "frontend" / "expense-demo.html").read_text(encoding="utf-8")
sync = (ROOT / "frontend" / "sync.js").read_text(encoding="utf-8")
mock = (ROOT / "scripts" / "demo_mock.js").read_text(encoding="utf-8")

# 1) 取出 <style>、<body> 內容；丟掉 doctype／html／head／body 標籤與 PWA 用的 meta／manifest（發布時會自動包骨架）
style = re.search(r"<style>(.*?)</style>", src, re.S).group(1)
body = re.search(r"<body>(.*)</body>", src, re.S).group(1)
fonts = re.search(r'<link href="(https://fonts.googleapis.com/[^"]+)" rel="stylesheet">', src).group(1)

# 2) 深色主題改成標準寫法（原檔用巢狀 @media，部分瀏覽器不支援）
dark_vars = re.search(r':root\[data-theme="dark"\] \{(.*?)\n  \}', style, re.S).group(1)
nested = re.search(r':root:not\(\[data-theme="light"\]\) \{\n    @media \(prefers-color-scheme: dark\) \{.*?\n    \}\n  \}', style, re.S).group(0)
style = style.replace(nested, '@media (prefers-color-scheme: dark) {\n    :root:not([data-theme="light"]) {' + dark_vars + "\n      color-scheme: dark;\n    }\n  }")
style = style.replace(':root[data-theme="dark"] {' + dark_vars + "\n  }", ':root[data-theme="dark"] {' + dark_vars + "\n    color-scheme: dark;\n  }")
# 3) 骨架已為 :root 加上安全區域 padding，body 不要重複
style = re.sub(r"\n    padding-top: env\(safe-area-inset-top, 0px\);\n    padding-bottom: env\(safe-area-inset-bottom, 0px\);", "", style, count=1)

# 4) 示範列：說明＋控制面板（放在 #app 之前，不遮住畫面）
bar_css = """
  .demo-bar { max-width: 480px; margin: 0 auto; padding: 8px 16px; background: var(--steel-soft); color: var(--steel); font-size: 12px; line-height: 1.6; border-bottom: 1px solid var(--line); }
  .demo-bar summary { cursor: pointer; font-weight: 600; }
  .demo-bar .row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }
  .demo-bar .row b { min-width: 4.5em; }
  .demo-bar button { border: 1px solid var(--line-strong); background: var(--surface); color: var(--ink); border-radius: 8px; padding: 5px 9px; font-size: 11.5px; font-weight: 600; cursor: pointer; }
  .demo-bar button[aria-pressed="true"] { background: var(--hazard); color: var(--hazard-ink); border-color: var(--hazard); }
  .demo-bar p { margin: 6px 0 0; color: var(--ink-soft); }"""
bar = """<div class="demo-bar"><details open><summary>示範版：資料在模擬伺服器，不是真實系統</summary>
  <p>先用「填入金鑰」綁定裝置；「拍照申報」是模擬辨識（統一發票視為 QR 驗證成功，同店同金額再送一次會被判重複退回）。檢視器會略過確認視窗，示範時會自動選「確定」。</p>
  <div class="row"><b>綁定金鑰</b><button onclick="document.getElementById('bindKey').value='ek_demo_submitter'">申報人（王志明）</button><button onclick="document.getElementById('bindKey').value='ek_demo_reviewer'">主管（陳主管）</button></div>
  <div class="row"><b>手機網路</b><button data-net="wifi" aria-pressed="true">Wi‑Fi</button><button data-net="cellular" aria-pressed="false">行動網路</button><button data-net="none" aria-pressed="false">無法判斷（iPhone）</button></div>
  <div class="row"><b>伺服器</b><button data-srv="ok" aria-pressed="true">正常</button><button data-srv="offline" aria-pressed="false">離線</button><button data-srv="503" aria-pressed="false">503 錯誤</button><button data-srv="429" aria-pressed="false">429 限流</button></div>
  <div class="row"><b>資料</b><button onclick="__demoReset()">重設示範</button></div>
</details></div>
<script>
document.querySelectorAll('[data-net]').forEach(function (b) { b.onclick = function () { __demoNet(b.dataset.net);
  document.querySelectorAll('[data-net]').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); }); }; });
document.querySelectorAll('[data-srv]').forEach(function (b) { b.onclick = function () {
  __demoSim.offline = b.dataset.srv === 'offline'; __demoSim.force = b.dataset.srv === 'offline' ? 'ok' : b.dataset.srv;
  document.querySelectorAll('[data-srv]').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); }); }; });
</script>
"""
# 5) 綁定畫面加示範金鑰提示
body = body.replace('<div class="pin-error" id="bindError"', '<div class="onboard-sub" style="margin:8px 0 0;">示範金鑰：<span class="mono">ek_demo_submitter</span>（申報人）、<span class="mono">ek_demo_reviewer</span>（主管）。上方示範列有「填入金鑰」按鈕。</div>\n      <div class="pin-error" id="bindError"', 1)
# 6) 內嵌 sync.js 與模擬伺服器（模擬伺服器必須先於 sync.js）
body = body.replace('<script src="sync.js"></script>', "<script>\n" + mock + "\n</script>\n<script>\n" + sync + "\n</script>", 1)

out = f"""<title>支出申報示範</title>
<link href="{fonts}" rel="stylesheet">
<style>{style}{bar_css}
</style>
{bar}{body}"""
(ROOT / "frontend" / "demo.html").write_text(out, encoding="utf-8")
print("frontend/demo.html", len(out) // 1024, "KB")
