#!/usr/bin/env python3

__version__ = "2026.01.03.01"
__author__ = "Muthukumar Subramanian"

"""
Custom HTTP/HTTPS Server Service

A lightweight Python-based HTTP/HTTPS file server with extended MIME support.
This systemd-enabled service allows users to serve files from a configurable
directory, ports, and access mode.

Configuration is managed via a config file, with optional CLI overrides
(for standalone or development usage only).

Key Features:
- Native HTTPS support with SSL certificate and key
- Optional HTTP → HTTPS redirection
- Threaded server model to handle multiple simultaneous client requests efficiently
- macOS-friendly: dynamic port fallback and bind-test
- Extended MIME types for common file formats
- Read / Write access modes for safe or full-control serving
- Easy-to-run drop-in Python server for local or LAN file serving

Access Modes:
- read  : Safe, read-only UI
          • No upload or delete actions
          • No checkboxes or write controls
          • Breadcrumb navigation, search, and sorting enabled
          • Clean HTML with no JS write handlers loaded
- write : Full management UI
          • Upload, delete, progress, and select-all enabled
          • Interactive controls and client-side JS enabled
          • Intended for trusted environments only

Changelog:
- 2026.01.03.01 : Initial draft
                  Added MIME types and macOS support
                  Multithreaded server
                  macOS bind-test and dynamic IP fallback
                  HTTP + HTTPS dual-protocol support with redirection
                  Read / Write access mode support
"""

import os
import ssl
import pwd
import sys
import socket
import signal
import base64
import platform
import datetime
import mimetypes
import threading
import unicodedata
import urllib.parse
from pathlib import Path
from subprocess import run
from http import HTTPStatus
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer


def get_real_user_home():
    """
    Resolve the *actual* user home even when running under sudo / launchd.
    """

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            return pwd.getpwnam(sudo_user).pw_dir
        except KeyError:
            pass

    return os.path.expanduser("~")


def ensure_psutil():
    """
    Ensure psutil is importable.
    - macOS: installs into user's Library/Python (launchd-safe)
    - Linux: installs into user or system site-packages depending on permissions
    """
    try:
        import psutil
        return psutil
    except ImportError:
        import subprocess
        import site

        print("⚠️ psutil not found — installing dynamically", flush=True)

        system = platform.system()
        python = sys.executable

        # -------------------------------
        # macOS (launchd-safe path)
        # -------------------------------
        if system == "Darwin":
            user_home = get_real_user_home()

            target = os.path.join(
                user_home,
                "Library",
                "Python",
                f"{sys.version_info.major}.{sys.version_info.minor}",
                "lib",
                "python",
                "site-packages",
            )

            os.makedirs(target, exist_ok=True)

            subprocess.check_call([
                python,
                "-m", "pip",
                "install",
                "psutil",
                "--target", target
            ])

            if target not in sys.path:
                sys.path.insert(0, target)

        # -------------------------------
        # Linux
        # -------------------------------
        else:
            # Prefer user site if writable
            try:
                subprocess.check_call([
                    python,
                    "-m", "pip",
                    "install",
                    "--user",
                    "psutil"
                ])
            except Exception:
                # Fallback: install into site-packages we control
                target = site.getusersitepackages()
                os.makedirs(target, exist_ok=True)

                subprocess.check_call([
                    python,
                    "-m", "pip",
                    "install",
                    "psutil",
                    "--target", target
                ])

                if target not in sys.path:
                    sys.path.insert(0, target)

        # -------------------------------
        # Final import
        # -------------------------------
        try:
            import psutil
            return psutil
        except ImportError as e:
            raise RuntimeError(
                "psutil installation completed but import still failed"
            ) from e


psutil = ensure_psutil()

# ------------------------------
# Custom MIME types
# ------------------------------
mimetypes.init()
for ext, mime in {
    # Text formats
    '.log': 'text/plain', '.txt': 'text/plain', '.tap': 'text/plain', '.md': 'text/plain',
    '.conf': 'text/plain', '.ini': 'text/plain', '.env': 'text/plain',

    # Code & markup
    '.html': 'text/html', '.xml': 'application/xml', '.json': 'application/json', '.js': 'application/javascript',
    '.css': 'text/css', '.py': 'text/x-python', '.sh': 'text/plain',

    # Images
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
    '.svg': 'image/svg+xml', '.webp': 'image/webp', '.ico': 'image/x-icon',

    # Docs
    '.pdf': 'application/pdf', '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt': 'application/vnd.ms-powerpoint',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',

    # Archives
    '.zip': 'application/zip', '.tar': 'application/x-tar', '.gz': 'application/gzip',
    '.bz2': 'application/x-bzip2', '.7z': 'application/x-7z-compressed'
}.items():
    mimetypes.add_type(mime, ext, strict=False)


def file_icon_for(name, is_dir):
    if is_dir:
        return "&#128193;", "dir"  # 📁 folder (yellow via CSS)

    ext = os.path.splitext(name.lower())[1]

    ICONS = {
        ".jpg": "&#128247;",  # 📷 image
        ".jpeg": "&#128247;",
        ".png": "&#128247;",
        ".gif": "&#128247;",
        ".zip": "&#128230;",  # 📦 archive
        ".tar": "&#128230;",
        ".gz": "&#128230;",
        ".cfg": "&#128221;",  # 📝 config
        ".conf": "&#128221;",
        ".yaml": "&#128221;",
        ".yml": "&#128221;",
        ".ini": "&#128221;",
        ".ks": "&#128221;",
        ".txt": "&#128221;",
        ".py": "&#128013;",  # 🐍 python
        ".sh": "&#128736;",  # 🛠 script
        ".log": "&#128196;",  # 📄 text
    }

    return ICONS.get(ext, "&#128196;"), "file"  # default 📄


def normalize_filename(name):
    return unicodedata.normalize("NFC", name)


def human_readable_size(size_bytes):
    """
    ..code-author:: Muthukumar Subramanian
    Convert bytes to human-readable format.
    """
    if size_bytes is None:
        return ""

    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0

    while size_bytes >= 1024 and i < len(units) - 1:
        size_bytes /= 1024
        i += 1

    # No decimals for bytes, decimals for others
    if i == 0:
        return f"{int(size_bytes)} {units[i]}"
    return f"{size_bytes:.2f} {units[i]}"


class CustomHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, auth_username, auth_password, server_mode="read", **kwargs):
        self.AUTH_USERNAME = auth_username
        self.AUTH_PASSWORD = auth_password
        self.server_mode = server_mode
        super().__init__(*args, **kwargs)

    # -------------------------
    # Logging
    # -------------------------
    def log_message(self, format, *args):
        thread = threading.current_thread().name
        timestamp = self.log_date_time_string()
        client_ip = self.client_address[0]

        print(f"[{thread}] {client_ip} - - [{timestamp}] {format % args}", flush=True)

    def is_text_config(self, filename):
        TEXT_EXTENSIONS = {
            ".cfg", ".conf", ".yaml", ".yml",
            ".ks", ".ini", ".txt",
            ".env", ".properties",
            ".md", ".log",
            ".sh", ".py"
        }
        return os.path.splitext(filename.lower())[1] in TEXT_EXTENSIONS

    def guess_type(self, path):
        # ---- allowlist of no-extension text files ----
        test_no_ext = {"README", "LICENSE", "Makefile"}

        # ---- allowlist of text extensions ----
        text_ext = {
            ".sh", ".py", ".log", ".md", ".conf", ".ini", ".env", ".txt", ".service", ".plist"
        }

        name = os.path.basename(path)
        _, ext = os.path.splitext(name.lower())

        # 1) Explicit no-extension text files
        if "." not in name and name in test_no_ext:
            return "text/plain; charset=utf-8"

        # 2) Force inline view for known text extensions
        if ext in text_ext:
            return "text/plain; charset=utf-8"

        # 3) Let system decide for everything else
        ctype = super().guess_type(path)
        return ctype or "application/octet-stream"

    def do_AUTHHEAD(self):
        """Send 401 Unauthorized header to prompt for login."""
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="File Server"')
        self.send_header('Content-type', 'text/html')
        self.end_headers()

    def check_auth(self):
        """Check if the client provided valid Authorization header."""
        auth_header = self.headers.get('Authorization')
        if auth_header is None or not auth_header.startswith('Basic '):
            return False
        # Decode base64 credentials
        encoded = auth_header.split(' ')[1]
        decoded = base64.b64decode(encoded).decode('utf-8')
        username, password = decoded.split(':', 1)
        return username == self.AUTH_USERNAME and password == self.AUTH_PASSWORD

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()

        # ctype = super().guess_type(path)
        ctype = self.guess_type(path)

        try:
            f = open(path, 'rb')
        except OSError:
            self.send_error(404, "File not found")
            return None

        fs = os.fstat(f.fileno())
        size = fs.st_size

        range_header = self.headers.get('Range')
        if range_header:
            start, end = range_header.replace("bytes=", "").split("-")
            start = int(start)
            end = int(end) if end else size - 1

            if start >= size:
                self.send_error(416, "Requested Range Not Satisfiable")
                return None

            self.send_response(206)
            self.send_header("Content-type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header(
                "Content-Range",
                f"bytes {start}-{end}/{size}"
            )
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()

            f.seek(start)
            self.wfile.write(f.read(end - start + 1))
            f.close()
            return None

        # ---- Normal full-file response ----
        self.send_response(200)
        self.send_header("Content-type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return f

    # -------------------------
    # HTML renderer
    # -------------------------
    def render_page(self, message_html=""):
        is_write = self.server_mode == "write"

        path_decoded = urllib.parse.unquote(self.path)
        fs_path = self.translate_path(path_decoded)

        # Only render directories
        if not os.path.isdir(fs_path):
            return None

        try:
            entries = os.listdir(fs_path)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, "Path not found")
            return None
        except PermissionError:
            self.send_response(HTTPStatus.FORBIDDEN)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"""
                    <html>
                    <h2>403 Forbidden</h2>
                    <p>macOS blocked access to this directory.</p>
                    <p>Grant <b>Full Disk Access</b> to the Python executable.</p>
                    </html>
                """)
            return

        # ---------- Breadcrumb navigation ----------
        parts = path_decoded.strip("/").split("/")
        breadcrumb = '<a href="/">Home</a>'
        cur_path = ""
        for p in parts:
            if not p:
                continue
            cur_path += f"/{p}"
            breadcrumb += f" / <a href='{cur_path}/'>{p}</a>"

        # ---------- Compute total size of current directory ----------
        dir_total_size = 0
        for name in os.listdir(fs_path):
            p = os.path.join(fs_path, name)
            try:
                if os.path.isfile(p):
                    dir_total_size += os.path.getsize(p)
            except OSError:
                pass

        # ---------- File listing ----------
        files_html = ""
        for fname in sorted(os.listdir(fs_path)):
            fpath = os.path.join(fs_path, fname)
            quoted = urllib.parse.quote(fname)

            try:
                stat = os.stat(fpath)
            except OSError:
                continue

            is_dir = os.path.isdir(fpath)

            # raw size
            raw_size = stat.st_size if not is_dir else 0  # directories shown as DIR
            size = human_readable_size(raw_size)
            percent = f"{(raw_size / dir_total_size * 100):.1f}%" if raw_size and dir_total_size else ""

            mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

            icon, icon_type = file_icon_for(fname, is_dir)
            icon_class = f"icon {icon_type}"
            size_text = "DIR" if is_dir else f"{size}"

            # Only show checkbox in write mode
            checkbox_html = ""
            if is_write:
                checkbox_html = (f"<input type='checkbox' class='delbox' value='{fname}' "
                                 f"style='visibility:hidden;' {'disabled' if is_dir else ''}>")

            files_html += (
                f"<li data-size='{raw_size}'>"
                f"{checkbox_html}"
                f"<span class='{icon_class}'>{icon}</span>"
                f"<a href='{quoted}' class='file-name'>{fname}</a>"
                f"<span class='file-meta'>[{size_text} | {percent} | {mtime}]</span>"
                f"</li>"
            )

        # ---------- Upload/Delete controls (write mode only) ----------
        upload_block = ""
        if is_write:
            upload_block = f"""
            <h2>Upload files to {path_decoded}</h2>
            <div class="top-bar">
                <form id="upload-form" enctype="multipart/form-data" class="upload-form">
                    <input type="file" name="file" multiple>
                    <input type="submit" value="Upload">
                </form>

                <div class="right-tools">
                    <button id="delbtn">Select file to delete</button>
                    <button id="selectall-btn" style="display:none;">Select All</button>
                    <input type="text" id="search-box" placeholder="Search files...">
                </div>
            </div>
            """
        else:
            upload_block = """
            <div class="top-bar">
                <div></div>
                <div class="right-tools">
                    <input type="text" id="search-box" placeholder="Search files...">
                </div>
            </div>
            """

        # ---------- Progress bar (write mode only) ----------
        progress_block = ""
        if is_write:
            progress_block = """
            <progress id="common-progress" max="100" style="display:none;"></progress>
            <div id="progress-text" style="display:none;"></div>
            """

        # ---------- JavaScript - conditional based on mode ----------
        if is_write:
            javascript = """
    <script>
    const uploadForm = document.getElementById("upload-form");
    const progressBar = document.getElementById("common-progress");
    const progressText = document.getElementById("progress-text");
    const deleteBtn = document.getElementById("delbtn");
    const selectAllBtn = document.getElementById("selectall-btn");
    const searchBox = document.getElementById("search-box");
    let deleteMode = false;

    function startProgress() {
        progressBar.style.display = "block";
        progressText.style.display = "block";
        progressBar.value = 0;
        progressText.textContent = "0%";
    }
    function updateProgress(pct) {
        progressBar.value = pct;
        progressText.textContent = pct < 100 ? pct + "%" : "Finalizing…";
    }
    function finishProgress() {
        progressBar.value = 100;
        progressText.textContent = "Completed";
        setTimeout(() => location.reload(), 800);
    }

    // UPLOAD
    uploadForm.addEventListener("submit", e => {
        e.preventDefault();
        const files = uploadForm.querySelector("input[type=file]").files;
        if (!files.length) { alert("No files selected"); return; }
        const fd = new FormData();
        [...files].forEach(f => fd.append("file", f));
        startProgress();
        const xhr = new XMLHttpRequest();
        xhr.open("POST", location.pathname, true);
        xhr.upload.onprogress = e => {
            if (e.lengthComputable) updateProgress(Math.floor((e.loaded / e.total) * 100));
        };
        xhr.onload = finishProgress;
        xhr.send(fd);
    });

    // DELETE
    deleteBtn.onclick = () => {
        const list = [...document.querySelectorAll("ul li")];
        const visibleBoxes = list.filter(li => li.style.display !== "none").map(li => li.querySelector(".delbox"));
        if (!deleteMode) {
            deleteMode = true;
            deleteBtn.textContent = "Delete selected";
            selectAllBtn.style.display = "inline";
            visibleBoxes.forEach(b => b.style.visibility = "visible");
            return;
        }
        const selected = visibleBoxes.filter(b => b.checked && !b.disabled).map(b => b.value);
        if (!selected.length) { alert("No files selected"); resetDelete(); return; }
        if (!confirm("Are you sure you want to delete:\\n\\n" + selected.join("\\n"))) { resetDelete(); return; }
        const fd = new FormData();
        selected.forEach(f => fd.append("delete_files", f));
        startProgress();
        const xhr = new XMLHttpRequest();
        xhr.open("POST", location.pathname, true);
        xhr.onload = finishProgress;
        xhr.send(fd);
    };

    // SELECT ALL
    selectAllBtn.onclick = () => {
        const list = [...document.querySelectorAll("ul li")];
        const visibleBoxes = list.filter(li => li.style.display !== "none").map(li => li.querySelector(".delbox"));
        const allChecked = visibleBoxes.every(b => b.checked);
        visibleBoxes.forEach(b => { if (!b.disabled) b.checked = !allChecked; });
    };

    function resetDelete() {
        deleteMode = false;
        deleteBtn.textContent = "Select file to delete";
        selectAllBtn.style.display = "none";
        document.querySelectorAll(".delbox").forEach(b => {
            b.checked = false;
            b.style.visibility = "hidden";
        });
    }

    // SORT BY SIZE
    let sizeAsc = true;
    function sortBySize() {
        const ul = document.querySelector("ul");
        const items = [...ul.querySelectorAll("li")];
        items.sort((a,b) => {
            const sa = parseInt(a.dataset.size || 0);
            const sb = parseInt(b.dataset.size || 0);
            return sizeAsc ? sa - sb : sb - sa;
        });
        sizeAsc = !sizeAsc;
        items.forEach(i => ul.appendChild(i));
    }

    // SEARCH
    searchBox.addEventListener("input", () => {
        const q = searchBox.value.toLowerCase();
        document.querySelectorAll("ul li").forEach(li => {
            const name = li.querySelector(".file-name").textContent.toLowerCase();
            li.style.display = name.includes(q) ? "" : "none";
        });
    });
    </script>
            """
        else:
            # Read mode - only search and sort
            javascript = """
    <script>
    const searchBox = document.getElementById("search-box");

    // SORT BY SIZE
    let sizeAsc = true;
    function sortBySize() {
        const ul = document.querySelector("ul");
        const items = [...ul.querySelectorAll("li")];
        items.sort((a,b) => {
            const sa = parseInt(a.dataset.size || 0);
            const sb = parseInt(b.dataset.size || 0);
            return sizeAsc ? sa - sb : sb - sa;
        });
        sizeAsc = !sizeAsc;
        items.forEach(i => ul.appendChild(i));
    }

    // SEARCH
    searchBox.addEventListener("input", () => {
        const q = searchBox.value.toLowerCase();
        document.querySelectorAll("ul li").forEach(li => {
            const name = li.querySelector(".file-name").textContent.toLowerCase();
            li.style.display = name.includes(q) ? "" : "none";
        });
    });
    </script>
            """

        # ---------- Adjust grid columns based on mode ----------
        grid_columns = "26px 26px 1fr 260px" if is_write else "26px 1fr 260px"

        # ---------- HTML ----------
        return f"""<!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <title>File Server</title>

    <style>
    body {{ font-family: sans-serif; }}
    button {{ margin-right:6px; }}
    progress {{ width:100%; height:22px; }}
    #progress-text {{ text-align:center; margin-top:4px; }}

    ul {{
        list-style: none;
        padding-left: 0;
    }}
    ul li {{
        display: grid;
        grid-template-columns: {grid_columns};
        align-items: center;
        gap: 8px;
        padding: 4px 0;
    }}
    .icon {{ font-size: 18px; }}
    .file-name {{
        overflow: hidden;
        white-space: nowrap;
        text-overflow: ellipsis;
    }}
    .file-meta {{
        text-align: right;
        color: #555;
        white-space: nowrap;
        font-size: 0.9em;
    }}
    .icon.dir {{
        background: #f1c40f;
        border-radius: 4px;
        padding: 2px 4px;
    }}
    .top-bar {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
    }}
    .upload-form {{
        display: flex;
        align-items: center;
        gap: 6px;
    }}
    .right-tools {{
        display: flex;
        align-items: center;
        gap: 8px;
    }}
    #search-box {{
        width: 220px;
        padding: 6px;
    }}
    </style>
    </head>

    <body>
    {message_html}
    <div><strong>Path:</strong> {breadcrumb}</div>
    {upload_block}
    {progress_block}
    <hr>

    <h3>
        Files <button onclick="sortBySize()">⇅ Size</button>
    </h3>
    <ul>
        {files_html}
    </ul>

    {javascript}
    </body>
    </html>
    """

    # -------------------------
    # GET
    # -------------------------
    def do_GET(self):
        if not self.check_auth():
            self.do_AUTHHEAD()
            self.wfile.write(b"Authentication required")
            return

        path_decoded = urllib.parse.unquote(self.path)
        fs_path = self.translate_path(path_decoded)

        # --- FIX: redirect directories without trailing slash ---
        if os.path.isdir(fs_path) and not self.path.endswith("/"):
            self.send_response(301)
            self.send_header("Location", self.path + "/")
            self.end_headers()
            return

        # --- Render UI for directories (both read and write mode) ---
        if os.path.isdir(fs_path):
            html = self.render_page()
            if html:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
                return

        # ---------- RAW TEXT VIEW ----------
        if os.path.isfile(fs_path) and self.is_text_config(fs_path):
            try:
                with open(fs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception as e:
                self.send_error(500, str(e))
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
            return

        # --- Let base handler serve files ---
        try:
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            # Browser closed connection (seek / stop / tab close)
            pass

    # -------------------------
    # POST - Streaming multipart parser for large files
    # -------------------------
    def do_POST(self):
        if not self.check_auth():
            self.do_AUTHHEAD()
            self.wfile.write(b"Authentication required")
            return

        if self.server_mode != "write":
            self.send_error(403)
            return

        target_dir = self.translate_path(urllib.parse.unquote(self.path))

        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", 0))

        if not content_type.startswith("multipart/form-data"):
            self.send_error(400, "Invalid content type")
            return

        # Extract boundary from content-type
        boundary = None
        for param in content_type.split(";"):
            param = param.strip()
            if param.startswith("boundary="):
                boundary = param.split("=", 1)[1].strip('"')
                break

        if not boundary:
            self.send_error(400, "Missing boundary in multipart/form-data")
            return

        uploaded_files = []
        delete_files = []

        try:
            # Use streaming parser to avoid loading entire body into memory
            uploaded, deleted = self._parse_multipart_stream(
                self.rfile, boundary, content_length, target_dir
            )
            uploaded_files.extend(uploaded)
            delete_files.extend(deleted)
        except Exception as e:
            self.send_error(500, f"Error processing request: {str(e)}")
            return

        # ---- Apply delete ----
        for name in delete_files:
            path = os.path.join(target_dir, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except Exception as e:
                    self.send_error(500, f"Error deleting file {name}: {str(e)}")
                    return

        # ---- Message ----
        if uploaded_files:
            msg_html = "<div style='color:green;text-align:center;'>Upload successful</div>"
        else:
            msg_html = "<div style='color:green;text-align:center;'>Delete successful</div>"

        html = self.render_page(msg_html)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _parse_multipart_stream(self, stream, boundary, content_length, target_dir):
        """Stream-parse multipart data without loading entire body into memory."""
        uploaded_files = []
        delete_files = []

        boundary_bytes = f"--{boundary}".encode()
        end_boundary_bytes = f"--{boundary}--".encode()

        BUFFER_SIZE = 8192  # 8KB buffer
        buffer = b""
        bytes_read = 0

        # --- Step 1: Read until the first boundary ---
        while True:
            chunk = stream.read(min(BUFFER_SIZE, content_length - bytes_read))
            if not chunk:
                raise ValueError("No boundary found in request")
            bytes_read += len(chunk)
            buffer += chunk
            boundary_pos = buffer.find(boundary_bytes)
            if boundary_pos != -1:
                buffer = buffer[boundary_pos + len(boundary_bytes):]
                break

        # --- Step 2: Process each part ---
        while True:
            # Read headers
            while b"\r\n\r\n" not in buffer and bytes_read < content_length:
                chunk = stream.read(min(BUFFER_SIZE, content_length - bytes_read))
                if not chunk:
                    break
                bytes_read += len(chunk)
                buffer += chunk

            headers_end = buffer.find(b"\r\n\r\n")
            if headers_end == -1:
                break  # no more parts

            part_headers = buffer[:headers_end].decode("utf-8", errors="replace")
            buffer = buffer[headers_end + 4:]  # skip \r\n\r\n

            filename = None
            field_name = None
            for line in part_headers.split("\r\n"):
                if line.lower().startswith("content-disposition:"):
                    if "filename=" in line:
                        filename = line.split("filename=")[1].strip('"').strip("'")
                    if "name=" in line:
                        field_name = line.split("name=")[1].split(";")[0].strip('"').strip("'")

            next_boundary_marker = b"\r\n" + boundary_bytes

            if field_name == "file" and filename:
                # --- File upload ---
                filename = normalize_filename(os.path.basename(filename))
                filepath = os.path.join(target_dir, filename)
                with open(filepath, "wb") as f:
                    while True:
                        boundary_pos = buffer.find(next_boundary_marker)
                        if boundary_pos != -1:
                            f.write(buffer[:boundary_pos])
                            buffer = buffer[boundary_pos + 2:]  # skip \r\n
                            break
                        elif bytes_read < content_length:
                            if len(buffer) > len(next_boundary_marker):
                                write_size = len(buffer) - len(next_boundary_marker)
                                f.write(buffer[:write_size])
                                buffer = buffer[write_size:]
                            chunk = stream.read(min(BUFFER_SIZE, content_length - bytes_read))
                            if not chunk:
                                f.write(buffer)
                                buffer = b""
                                break
                            bytes_read += len(chunk)
                            buffer += chunk
                        else:
                            f.write(buffer)
                            buffer = b""
                            break
                uploaded_files.append(filename)

            elif field_name == "delete_files":
                # --- Delete field ---
                while True:
                    boundary_pos = buffer.find(next_boundary_marker)
                    if boundary_pos != -1:
                        value = buffer[:boundary_pos].decode("utf-8", errors="replace").strip()
                        delete_files.append(normalize_filename(value))
                        buffer = buffer[boundary_pos + 2:]  # skip \r\n
                        break
                    elif bytes_read < content_length:
                        chunk = stream.read(min(BUFFER_SIZE, content_length - bytes_read))
                        if not chunk:
                            break
                        bytes_read += len(chunk)
                        buffer += chunk
                    else:
                        break

            # --- Check for end boundary ---
            if buffer.startswith(boundary_bytes + b"--") or buffer.startswith(end_boundary_bytes):
                break

        return uploaded_files, delete_files


# ------------------------------
# Helper Functions
# ------------------------------

def kill_process_on_port(port, *, force=False):
    killed_pids = []
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            for conn in proc.net_connections(kind="inet"):
                if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                    sig = signal.SIGKILL if force else signal.SIGTERM
                    os.kill(proc.pid, sig)
                    killed_pids.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed_pids


def test_bind_ip(ip, port):
    """Return True if ip:port can be bound."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((ip, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def get_bindable_ips():
    ips = []
    for iface, addrs in psutil.net_if_addrs().items():
        stats = psutil.net_if_stats().get(iface)
        if not stats or not stats.isup:
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                ips.append(addr.address)
    return ips


def get_routed_ip():
    """Get the OS-chosen IP to reach internet (VPN-aware)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def select_bind_ip(port, requested_ip=None):
    """Pick the best IP for binding."""
    # 1) Requested IP
    if requested_ip and requested_ip != "0.0.0.0" and test_bind_ip(requested_ip, port):
        return requested_ip

    # 2) Routed IP
    routed_ip = get_routed_ip()
    if routed_ip and test_bind_ip(routed_ip, port):
        return routed_ip

    # 3) All active interfaces
    for ip in get_bindable_ips():
        if test_bind_ip(ip, port):
            return ip

    # 4) Fallback
    return "0.0.0.0"


def select_bind_port(check_requested_port):
    """Select a usable port. If requested <1024, fallback to >=1024 if non-root."""
    if check_requested_port < 1024 and os.geteuid() != 0:
        # non-root → pick first free port ≥1024
        for port in range(8080, 65535):
            for ip in get_bindable_ips() + ["0.0.0.0"]:
                if test_bind_ip(ip, port):
                    print(f"⚠️ Non-root cannot bind port {check_requested_port}, using {port} instead")
                    return port
        raise RuntimeError("No free port available >=1024")
    else:
        # requested port is valid
        for ip in get_bindable_ips() + ["0.0.0.0"]:
            if test_bind_ip(ip, check_requested_port):
                return check_requested_port
        raise RuntimeError(f"Requested port {check_requested_port} is already in use")


# -------------------------
# DualProtocolServer class
# -------------------------
class DualProtocolServer:
    """
    Starts:
      - HTTPS server (main)
      - HTTP server that redirects to HTTPS
    """

    def __init__(self, handler_cls, host="", http_port=8080, https_port=8443, username="admin",
                 password="password", server_mode="write"):
        self.handler_cls = handler_cls
        self.host = host
        self.username = username
        self.password = password
        self.server_mode = server_mode
        self.http_port = http_port
        self.https_port = https_port
        self.httpd = None  # HTTP redirect server
        self.httpsd = None  # HTTPS main server
        self.https_server = None
        self.http_redirect_server = None
        self.cert_dir, self.cert_file, self.key_file = self._init_cert_paths()
        self._ensure_certificate()

    # --------------------------------------------------
    # Certificate paths (macOS / Linux / Windows)
    # --------------------------------------------------
    def _init_cert_paths(self):
        if sys.platform.startswith("darwin"):  # macOS
            cert_dir = (Path.home() / "Library" / "Application Support" / "custom_https_server" / "certs")
        elif sys.platform.startswith("win"):  # Windows
            cert_dir = Path(os.getenv("APPDATA")) / "custom_https_server" / "certs"
        else:  # Linux
            cert_dir = Path.home() / ".custom_https_server" / "certs"

        cert_dir.mkdir(parents=True, exist_ok=True)
        return (
            cert_dir,
            cert_dir / "server.crt",
            cert_dir / "server.key",
        )

    # --------------------------------------------------
    # Self-signed cert generation
    # --------------------------------------------------
    def _ensure_certificate(self):
        if self.cert_file.exists() and self.key_file.exists():
            return

        print(f"[TLS] Generating self-signed certificate in {self.cert_dir}")
        run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:4096",
                "-keyout",
                str(self.key_file),
                "-out",
                str(self.cert_file),
                "-days",
                "365",
                "-nodes",
                "-subj",
                "/CN=localhost",
            ],
            check=True,
        )
        print("[TLS] Certificate generated")

    # --------------------------------------------------
    # HTTPS server
    # --------------------------------------------------
    def _run_https(self):
        HandlerClass = partial(
            self.handler_cls,
            auth_username=self.username,
            auth_password=self.password,
            server_mode=self.server_mode,
        )

        # Start HTTP server
        # V2 Custom-HTTPS-Server
        self.https_server = ThreadingHTTPServer(
            (self.host, self.https_port),
            HandlerClass
        )
        self.https_server.daemon_threads = True

        self.https_server.socket = ssl.wrap_socket(
            self.https_server.socket,
            keyfile=str(self.key_file),
            certfile=str(self.cert_file),
            server_side=True,
        )

        print(f"[HTTPS] Serving on {self.host}:{self.https_port}", flush=True)
        self.https_server.serve_forever()

    # --------------------------------------------------
    # HTTP -> HTTPS redirect server
    # --------------------------------------------------
    def _run_http_redirect(self):
        parent = self

        # -------------------------
        # Redirect handler
        # -------------------------
        class RedirectHandler(self.handler_cls):
            def __init__(self, *args, **kwargs):
                kwargs['auth_username'] = parent.username
                kwargs['auth_password'] = parent.password
                kwargs['server_mode'] = parent.server_mode
                super().__init__(*args, **kwargs)

            def handle_one_request(self):
                try:
                    super().handle_one_request()
                except Exception:
                    # Drop TLS-on-HTTP garbage silently
                    pass

            def do_GET(self):
                host = self.headers.get("Host", f"localhost:{parent.http_port}")
                https_host = host.replace(
                    f":{parent.http_port}", f":{parent.https_port}"
                )
                self.send_response(301)
                self.send_header("Location", f"https://{https_host}{self.path}")
                self.end_headers()

            def do_POST(self):
                self.do_GET()

        # Start HTTP server
        # V1 Custom-HTTP-Server
        self.http_redirect_server = HTTPServer(
            (self.host, self.http_port), RedirectHandler
        )

        print(f"[HTTP] Serving on {self.http_port} → redirecting to HTTPS", flush=True)
        self.http_redirect_server.serve_forever()

    # --------------------------------------------------
    # Public start()
    # --------------------------------------------------
    def start(self, redirect=False):
        """Start HTTPS server and optionally HTTP redirect server."""

        # ---------- Start HTTPS server ----------
        https_thread = threading.Thread(target=self._run_https, daemon=True)
        https_thread.start()

        # ---------- Optional HTTP redirect ----------
        http_thread = None
        if redirect:
            http_thread = threading.Thread(target=self._run_http_redirect, daemon=True)
            http_thread.start()

        # ---------- Stop event ----------
        stop_event = threading.Event()

        # ---------- Signal handler ----------
        def shutdown_handler(sig, frame):
            print(f"[SERVER] Received signal {sig}, shutting down...", flush=True)
            if self.http_redirect_server:
                self.http_redirect_server.shutdown()
                self.http_redirect_server.server_close()
            if self.https_server:
                self.https_server.shutdown()
                self.https_server.server_close()
            stop_event.set()

        # Only Unix supports signals like SIGTERM
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)

        # ---------- Wait for signal ----------
        try:
            stop_event.wait()  # Blocks main thread until set()
        except KeyboardInterrupt:
            shutdown_handler(signal.SIGINT, None)


if __name__ == '__main__':
    import getpass
    import argparse

    # -------------------------------
    # Using the log dir/files created by the installer
    # -------------------------------
    if platform.system() == 'Darwin':
        CONFIG_PATH = "/usr/local/etc/custom-https-server.conf"
    else:
        CONFIG_PATH = "/etc/custom-https-server.conf"

    # Fallback: local/current working directory
    if not os.path.isfile(CONFIG_PATH):
        CONFIG_PATH = os.path.join(os.path.dirname(__file__), "default-config.conf")
        print(f"⚠️ Config not found in /usr/local, using local config: {CONFIG_PATH}")

    # Load config
    if not os.path.isfile(CONFIG_PATH):
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")


    def load_shell_config(path):
        data = {}
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
        return data


    cfg = load_shell_config(CONFIG_PATH)
    LOG_DIR = Path(cfg["CFG_LOG_DIR"])
    LOG_FILE = Path(cfg["CFG_LOG_FILE"])
    ERR_FILE = Path(cfg["CFG_ERR_FILE"])

    if not os.path.exists(LOG_DIR) or not os.path.isfile(LOG_FILE) or not os.path.isfile(ERR_FILE):
        # Determine real home
        sudo_user = os.environ.get("SUDO_USER")
        user_name = sudo_user or os.environ.get("USER") or getpass.getuser()
        try:
            home_dir = Path(pwd.getpwnam(user_name).pw_dir)
        except KeyError:
            home_dir = Path.home()

        LOG_DIR = home_dir / "custom_https_server_log" / "logs"
        LOG_FILE = LOG_DIR / "custom_https_server.log"
        ERR_FILE = LOG_DIR / "custom_https_server.err"

    if LOG_DIR and LOG_FILE and ERR_FILE:
        # Ensure directory exists
        Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

        # Ensure files exist
        Path(LOG_FILE).touch(exist_ok=True)
        Path(ERR_FILE).touch(exist_ok=True)

        # macOS (launchd) — redirect always
        if platform.system() == 'Darwin':
            if sys.stdout.isatty():
                LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                sys.stdout = open(LOG_FILE, "a", buffering=1)
                sys.stderr = open(ERR_FILE, "a", buffering=1)

        # Linux — redirect only if running outside a terminal
        if platform.system() == 'Linux':
            if not sys.stdout.isatty():
                LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                sys.stdout = open(LOG_FILE, "a", buffering=1)
                sys.stderr = open(ERR_FILE, "a", buffering=1)

        print(f"LOG_DIR: {LOG_DIR}", flush=True)
        print(f"LOG_FILE: {LOG_FILE}", flush=True)

    print("{:#^30}".format("Script Start"), flush=True)
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument('--path', default='/Users/muthukumar', help='Directory to serve')
        parser.add_argument('--port', type=int, default=80, help='Port number')
        parser.add_argument('--bind', default='0.0.0.0', help='IP address to bind')
        parser.add_argument('--mode', choices=['read', 'write'], default='read',
                            help='Server mode: read or write')
        parser.add_argument('--user', default='admin', help='Username for authentication')
        parser.add_argument('--pass', dest='password', default='password',
                            help='Password for authentication')
        args = parser.parse_args()
        SERVER_MODE = args.mode

        print(f"{'=' * 10} Args Argument - Start {'=' * 10}", flush=True)
        print(f"{'Serving path:':15} {args.path}", flush=True)
        print(f"{'Port:':15} {args.port}", flush=True)
        print(f"{'Bind IP:':15} {args.bind}", flush=True)
        print(f"{'Mode:':15} {SERVER_MODE}", flush=True)
        print(f"{'Username:':15} {args.user}", flush=True)
        print(f"{'Password:':15} ********", flush=True)
        print(f"{'=' * 10} Args Argument - End {'=' * 10}", flush=True)

        # Determine user home
        if platform.system() == "Windows":
            user_home = os.path.expanduser("~")
        else:
            actual_user = os.environ.get("SUDO_USER") or getpass.getuser()
            user = actual_user or os.getlogin()
            user_home = pwd.getpwnam(user).pw_dir

        # Resolve serve path
        if args.path and args.path.strip():
            serve_path = args.path
        elif user_home and user_home.strip():
            serve_path = user_home
        else:
            serve_path = os.path.expanduser(f"~{getpass.getuser()}")
        print(f"Resolved serve path: {serve_path}", flush=True)
        if not os.path.isdir(serve_path):
            raise ValueError(f"Invalid directory: {serve_path}")
        os.chdir(serve_path)

        # ------------------------------
        # Determine bind IP
        # ------------------------------
        bind_port = select_bind_port(args.port)
        if platform.system() == "Darwin":
            # 🚫 Never auto-detect IP on macOS (launchd-safe)
            bind_ip = args.bind or "0.0.0.0"
            print("🍎 macOS detected — skipping bind IP auto-detection", flush=True)
        else:
            bind_ip = select_bind_ip(bind_port, args.bind)

        print(f"Binding server to: {bind_ip}:{bind_port}", flush=True)
        server_address = (bind_ip, bind_port)

        # Kill existing process on port
        killed = kill_process_on_port(bind_port)
        if killed:
            print(f"Killed processes on port {bind_port}: {killed}", flush=True)
        else:
            print(f"No process listening on port {bind_port}", flush=True)

        # ------------------------------------
        # V3 HTTPS + HTTP redirect server
        # ------------------------------------
        server = DualProtocolServer(
            handler_cls=CustomHandler,
            host=bind_ip,
            http_port=bind_port + 1,  # HTTP (redirect)
            https_port=bind_port,  # HTTPS
            username=args.user,
            password=args.password,
            server_mode=SERVER_MODE,
        )

        server.start()

        # Keep main thread alive
        threading.Event().wait()

    except Exception as Err:
        print(f"Observed exception: {Err}")
        sys.exit(1)
    print("{:#^30}".format("Script End"), flush=True)
