# -*- coding: utf-8 -*-
"""
HTTP 测试服务器 (端口 8080) —— 支持文件上传 / 下载 / 列表浏览。
仅使用 Python 标准库，可由 PyInstaller 打包为单个 exe。

用法：
    直接运行：  python server.py
    打包 exe：  pyinstaller --onefile --console --name HttpFileServer server.py
"""

import http.server
import os
import re
import sys
import html
import socket
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from pathlib import Path

# Windows 控制台默认是 GBK（cp936），强制 UTF-8 以避免中文 / 特殊符号导致崩溃
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PORT = 8080
MAX_BYTES_DISPLAY = 100 * 1024 * 1024  # 单次请求体上限 100MB（纯内存读取）

# exe 旁目录用于存放上传文件，方便用户查找
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def get_lan_ip() -> str:
    """获取本机局域网 IP（无法获取时回退到 127.0.0.1）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def parse_multipart(body: bytes, boundary: bytes):
    """解析 multipart/form-data，返回 [(headers_dict, content_bytes), ...]。"""
    delimiter = b"--" + boundary
    segments = body.split(delimiter)
    parts = []
    for seg in segments:
        if seg in (b"", b"--", b"--\r\n", b"\r\n"):
            continue
        if seg.startswith(b"\r\n"):
            seg = seg[2:]
        if seg.endswith(b"\r\n"):
            seg = seg[:-2]
        header_block, sep, content = seg.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = {}
        for line in header_block.split(b"\r\n"):
            if b":" in line:
                k, _, v = line.partition(b":")
                headers[k.strip().lower()] = v.strip()
        parts.append((headers, content))
    return parts


def get_filename(content_disposition: bytes) -> str:
    """从 Content-Disposition 头里提取 filename（支持 filename* 编码）。"""
    text = content_disposition.decode("utf-8", "replace")
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", text, re.IGNORECASE)
    if m:
        return urllib.parse.unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename="([^"]*)"', text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"filename=([^;]+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')
    return ""


def sanitize(name: str) -> str:
    """去掉路径分隔符等危险字符，仅保留文件名部分。"""
    name = os.path.basename(name.strip().replace("\\", "/"))
    name = name.replace("..", "_").strip()
    if not name:
        name = "未命名文件"
    return name


def unique_path(dest: Path) -> Path:
    """若目标已存在，追加 (1)/(2) 后缀避免覆盖。"""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    i = 1
    while True:
        candidate = dest.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


# --------------------------------------------------------------------------
# 页面模板
# --------------------------------------------------------------------------
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HTTP 文件上传服务器</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         margin: 0; background: #f4f6fa; color: #1f2937; }}
  .wrap {{ max-width: 860px; margin: 0 auto; padding: 28px 18px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #6b7280; font-size: 13px; margin-bottom: 22px; }}
  .card {{ background: #fff; border-radius: 12px; padding: 22px;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); margin-bottom: 18px; }}
  .drop {{ border: 2px dashed #cbd5e1; border-radius: 10px; padding: 26px;
           text-align: center; color: #64748b; transition: .15s; cursor: pointer; }}
  .drop.hover {{ border-color: #2563eb; background: #eff6ff; color: #2563eb; }}
  input[type=file] {{ display: none; }}
  .btn {{ display: inline-block; background: #2563eb; color: #fff; border: none;
          padding: 10px 22px; border-radius: 8px; font-size: 14px; cursor: pointer;
          margin-top: 14px; }}
  .btn:hover {{ background: #1d4ed8; }}
  .btn:disabled {{ background: #94a3b8; cursor: not-allowed; }}
  .files {{ list-style: none; padding: 0; margin: 12px 0 0; }}
  .files li {{ display: flex; justify-content: space-between; align-items: center;
              padding: 9px 6px; border-bottom: 1px solid #f1f5f9; font-size: 14px; }}
  .files li:last-child {{ border-bottom: none; }}
  .files a {{ color: #2563eb; text-decoration: none; word-break: break-all; }}
  .files a:hover {{ text-decoration: underline; }}
  .meta {{ color: #94a3b8; font-size: 12px; white-space: nowrap; margin-left: 12px; }}
  .del {{ color: #ef4444; text-decoration: none; margin-left: 12px; font-size: 12px; }}
  .msg {{ background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0;
          padding: 10px 14px; border-radius: 8px; margin-bottom: 14px; font-size: 14px; }}
  .empty {{ color: #9ca3af; text-align: center; padding: 18px; font-size: 14px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>📦 HTTP 文件上传服务器</h1>
  <div class="sub">端口 {port} · 上传文件保存到 exe 同目录的 <code>uploads/</code> 文件夹</div>

  {msg}

  <div class="card">
    <label class="drop" id="drop" for="file">
      <div style="font-size:30px;line-height:1">⬆️</div>
      <div style="margin-top:8px"><b>点击选择文件</b> 或拖拽文件到此处</div>
      <div style="font-size:12px;margin-top:4px">支持多文件上传</div>
    </label>
    <form method="post" action="/upload" enctype="multipart/form-data" id="form">
      <input type="file" name="file" id="file" multiple>
      <button type="submit" class="btn" id="submit" disabled>开始上传</button>
    </form>
  </div>

  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <b style="font-size:16px">📂 已上传文件</b>
      <span style="color:#94a3b8;font-size:12px">共 {count} 个</span>
    </div>
    {file_list}
  </div>
</div>
<script>
  const drop = document.getElementById('drop');
  const file = document.getElementById('file');
  const submit = document.getElementById('submit');
  file.addEventListener('change', () => {{ submit.disabled = file.files.length === 0; }});
  ['dragenter','dragover'].forEach(e => drop.addEventListener(e, ev => {{
    ev.preventDefault(); drop.classList.add('hover');
  }}));
  ['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => {{
    ev.preventDefault(); drop.classList.remove('hover');
  }}));
  drop.addEventListener('drop', ev => {{
    if (ev.dataTransfer.files.length) {{ file.files = ev.dataTransfer.files;
      submit.disabled = false; }}
  }});
</script>
</body>
</html>
"""


def render_page(msg: str = "") -> str:
    files = []
    if UPLOAD_DIR.exists():
        files = sorted(
            (p for p in UPLOAD_DIR.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    if files:
        rows = []
        for p in files:
            name = html.escape(p.name)
            href = "/uploads/" + urllib.parse.quote(p.name)
            size = human_size(p.stat().st_size)
            del_href = "/delete?name=" + urllib.parse.quote(p.name)
            rows.append(
                f'<li><span><a href="{href}" download>{name}</a></span>'
                f'<span><span class="meta">{size}</span>'
                f'<a class="del" href="{del_href}" '
                f'onclick="return confirm(\'删除 {name} ？\')">删除</a></span></li>'
            )
        file_list = '<ul class="files">' + "".join(rows) + "</ul>"
    else:
        file_list = '<div class="empty">还没有文件，上传一个试试吧～</div>'
    return PAGE_TEMPLATE.format(
        port=PORT, msg=msg, count=len(files), file_list=file_list
    )


# --------------------------------------------------------------------------
# 请求处理
# --------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "HttpFileServer/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))
        sys.stdout.flush()

    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ("/", ""):
            self._send_html(render_page())
        elif path == "/uploads":
            self._send_html(render_page())
        elif path.startswith("/uploads/"):
            name = urllib.parse.unquote(path[len("/uploads/"):])
            self._serve_download(name)
        elif path == "/delete":
            self._handle_delete(parsed.query)
        elif path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
        else:
            self._send_text(HTTPStatus.NOT_FOUND, "404 Not Found")

    # ---------- HEAD ----------
    def do_HEAD(self):
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    # ---------- POST ----------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        # 同时接受 POST / 和 POST /upload，方便 curl 直接 -F 上传到根路径
        if parsed.path in ("/", "/upload") or parsed.path.endswith("/upload"):
            self._handle_upload()
        else:
            self._send_text(HTTPStatus.NOT_FOUND, "404 Not Found")

    # ---------- 路由实现 ----------
    def _handle_upload(self):
        ctype = self.headers.get("Content-Type", "")
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > MAX_BYTES_DISPLAY:
            self._send_text(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"文件太大：单次最多 {human_size(MAX_BYTES_DISPLAY)}",
            )
            return
        body = self.rfile.read(length) if length else b""
        UPLOAD_DIR.mkdir(exist_ok=True)

        saved = 0
        if ctype.startswith("multipart/form-data"):
            m = re.search(r"boundary=(.+)", ctype)
            if m:
                boundary = m.group(1).strip().strip('"').encode()
                for headers, content in parse_multipart(body, boundary):
                    cd = headers.get(b"content-disposition", b"")
                    filename = get_filename(cd)
                    if filename:
                        dest = unique_path(UPLOAD_DIR / sanitize(filename))
                        dest.write_bytes(content)
                        saved += 1
        elif ctype.startswith("application/octet-stream") or ctype == "":
            # 支持直接 PUT 式上传原始字节
            disp = self.headers.get("Content-Disposition", "")
            filename = get_filename(disp.encode()) or "raw_upload.bin"
            dest = unique_path(UPLOAD_DIR / sanitize(filename))
            dest.write_bytes(body)
            saved += 1

        msg = (
            f'<div class="msg">✅ 成功上传 {saved} 个文件</div>' if saved
            else '<div class="msg" style="background:#fef2f2;color:#991b1b;border-color:#fecaca">'
            '⚠️ 没有接收到文件</div>'
        )
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/?msg=" + urllib.parse.quote(msg))
        self.end_headers()

    def _handle_delete(self, query: str):
        qs = urllib.parse.parse_qs(query)
        name = qs.get("name", [""])[0]
        name = sanitize(name)
        target = UPLOAD_DIR / name
        if name and target.is_file() and UPLOAD_DIR.resolve() == target.parent.resolve():
            try:
                target.unlink()
            except OSError:
                pass
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.end_headers()

    def _serve_download(self, name: str):
        name = sanitize(name)
        target = UPLOAD_DIR / name
        if not (name and target.is_file() and UPLOAD_DIR.resolve() == target.parent.resolve()):
            self._send_text(HTTPStatus.NOT_FOUND, "文件不存在")
            return
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header(
            "Content-Disposition",
            'attachment; filename*=UTF-8\'\'%s' % urllib.parse.quote(name),
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(data)

    # ---------- 输出辅助 ----------
    def _send_html(self, text: str, status=HTTPStatus.OK):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(data)

    def _send_text(self, status, text: str):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(data)


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------
def main():
    UPLOAD_DIR.mkdir(exist_ok=True)
    lan_ip = get_lan_ip()
    urls = [f"http://localhost:{PORT}/"]
    if lan_ip != "127.0.0.1":
        urls.append(f"http://{lan_ip}:{PORT}/")

    print("=" * 56)
    print("  HTTP 测试服务器 · 端口 {} · 支持文件上传".format(PORT))
    print("=" * 56)
    print("  访问地址：")
    for u in urls:
        print("    " + u)
    print("  上传目录：" + str(UPLOAD_DIR))
    print("-" * 56)
    if lan_ip != "127.0.0.1":
        print("  ⓘ 已监听所有网卡，局域网内其它设备可用上面的 IP 访问。")
    print("  按 Ctrl+C 停止服务器")
    print("=" * 56)
    sys.stdout.flush()

    # 1 秒后自动打开浏览器
    threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{PORT}/")).start()

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
