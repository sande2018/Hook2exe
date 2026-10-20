#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hook2Exe —— 把「网址 + 油猴脚本」打包成桌面应用（exe，Edge WebView2 内核）

用法：
  GUI 模式：  python hook2exe.py
  CLI 模式：  python hook2exe.py --url https://www.goofish.com/im --script 脚本.js

  --url      目标网址（必填）
  --script   油猴脚本路径（.js / .txt，可选）
  --title    窗口标题（留空自动取网页标题，再留空用域名）
  --name     应用名 / exe 文件名（留空用标题）
  --icon     图标文件（.ico/.png/.jpg/.bmp，留空自动抓网站 favicon，抓不到用默认图标）
  --out      输出根目录（默认本工具目录下的 output）
  --no-tray  关闭窗口直接退出（默认最小化到托盘）
  --all-frames  允许脚本在 iframe 里运行（默认仅顶层窗口）

生成的应用目录：<out>/<应用名>/，含 exe + 3 个 WebView2 DLL，整体拷走即可分发。
依赖：Windows x64 + 系统自带 .NET Framework 4.x + Edge/WebView2 运行时（Win10/11 一般自带）。
可选：pip install pillow —— 装了能更好地转换任意图片图标（不装也能用）。
"""

import argparse
import hashlib
import html as html_mod
import io
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import zlib
from urllib.parse import urljoin
from urllib.request import Request, urlopen

# ───────────────────────── 路径常量 ─────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(BASE_DIR, "templates")
LIB_DIR = os.path.join(BASE_DIR, "lib")

LIB_FILES = {
    "core": os.path.join(LIB_DIR, "Microsoft.Web.WebView2.Core.dll"),
    "winforms": os.path.join(LIB_DIR, "Microsoft.Web.WebView2.WinForms.dll"),
    "loader": os.path.join(LIB_DIR, "WebView2Loader.dll"),
}

# 可选依赖 Pillow（图标转换增强）
try:
    from PIL import Image, ImageDraw, ImageFont  # noqa
    HAS_PIL = True
except Exception:
    HAS_PIL = False


def safe_print(msg):
    """控制台安全输出（避免 GBK 终端编解码崩溃）。"""
    try:
        print(msg)
    except Exception:
        try:
            sys.stdout.buffer.write((str(msg) + "\n").encode("utf-8", "replace"))
            sys.stdout.buffer.flush()
        except Exception:
            pass


def read_tpl(name):
    path = os.path.join(TPL_DIR, name)
    if not os.path.isfile(path):
        raise RuntimeError("模板文件缺失：" + path)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def cs_str(s):
    """C# 字符串字面量内容转义。"""
    s = (s or "").replace("\r", " ").replace("\n", " ")
    return s.replace("\\", "\\\\").replace('"', '\\"')


def js_str(s):
    """JS 字符串字面量内容转义。"""
    s = (s or "").replace("\\", "\\\\").replace('"', '\\"')
    return s.replace("\r", "\\r").replace("\n", "\\n")


def short_hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


# ───────────────────────── 油猴元数据 ─────────────────────────

def parse_userscript_meta(js_text):
    meta = {"name": "", "version": "", "run_at": "", "match": [], "include": []}
    if not js_text:
        return meta
    i = js_text.find("==UserScript==")
    if i < 0:
        return meta
    j = js_text.find("==/UserScript==", i)
    block = js_text[i:(j if j > i else len(js_text))]
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("//"):
            continue
        body = line[2:].strip()
        if not body.startswith("@"):
            continue
        body = body[1:].strip()
        parts = body.split(None, 1)
        key = parts[0].lower() if parts else ""
        val = parts[1].strip() if len(parts) > 1 else ""
        if key == "name" and not meta["name"]:
            meta["name"] = val
        elif key == "version" and not meta["version"]:
            meta["version"] = val
        elif key == "run-at" and not meta["run_at"]:
            meta["run_at"] = val
        elif key == "match" and val:
            meta["match"].append(val)
        elif key == "include" and val:
            meta["include"].append(val)
    return meta


def match_to_regex(pattern):
    """把油猴 @match 模式转换为 JS 正则源码（返回字符串，失败返回 None）。"""
    try:
        pattern = (pattern or "").strip()
        idx = pattern.find("://")
        if idx < 0:
            return None
        scheme = pattern[:idx]
        rest = pattern[idx + 3:]
        slash = rest.find("/")
        host_part = rest if slash < 0 else rest[:slash]
        path_part = "/" if slash < 0 else rest[slash:]

        if scheme == "*":
            scheme_re = "https?"
        elif scheme.endswith("*"):
            scheme_re = re.escape(scheme.rstrip("*"))
        else:
            scheme_re = re.escape(scheme)

        if host_part == "*":
            host_re = "[^/]+"
        elif host_part.startswith("*."):
            host_re = "(?:[a-z0-9-]+\\.)*" + re.escape(host_part[2:])
        else:
            host_re = re.escape(host_part).replace(r"\*", "[^.]*")

        path_re = re.escape(path_part).replace(r"\*", ".*")
        return "^" + scheme_re + "://" + host_re + path_re + "$"
    except Exception:
        return None


def extract_allowed_hosts(meta, target_host):
    """桥接允许域名：目标域名 + 脚本 @match/@include 涉及域名（宿主侧安全白名单）。"""
    hosts = []

    def add(h):
        h = (h or "").lower().strip().rstrip(".")
        if not h or h in hosts:
            return
        if h != "localhost" and "." not in h:
            return
        hosts.append(h)
        if h.startswith("www."):
            bare = h[4:]
            if bare and "." in bare and bare not in hosts:
                hosts.append(bare)

    add(target_host)
    for pat in list(meta["match"]) + list(meta["include"]):
        p = (pat or "").strip()
        if not p:
            continue
        host_part = None
        i = p.find("://")
        if i >= 0:
            rest = p[i + 3:]
            s = rest.find("/")
            host_part = rest if s < 0 else rest[:s]
        else:
            t = p.lstrip("*").lstrip(".")
            cut = t.find("/")
            if cut > 0:
                t = t[:cut]
            if "." in t:
                host_part = t
        if not host_part:
            continue
        if host_part == "*" or "*" in host_part:
            bare = host_part.replace("*", "").strip(".")
            if "." in bare:
                add(bare)
        else:
            add(host_part)
    return hosts


# ───────────────────────── 注入脚本组装 ─────────────────────────

def compose_wrapper(script_text, meta, top_only):
    """生成最终注入脚本：GM 兼容层 + @match 门控 + 油猴脚本本体 + run-at 调度。"""
    script_name = meta["name"] or "user-script"
    script_version = meta["version"] or "1.0"

    lines = []
    lines.append("// ==Hook2Exe 注入包装（自动生成）==")
    lines.append("(function () {")
    lines.append('  "use strict";')
    if top_only:
        lines.append("  if (window.top !== window.self) return; // 仅顶层窗口运行")
    lines.append("")
    prelude = (
        read_tpl("gm_prelude.js")
        .replace("__SCRIPT_NAME__", js_str(script_name))
        .replace("__SCRIPT_VERSION__", js_str(script_version))
    )
    lines.append(prelude)
    lines.append("")
    lines.append("  function __h2eRunUserScript() {")

    # @match 门控
    regexes = [r for r in (match_to_regex(m) for m in meta["match"]) if r]
    if regexes:
        arr = ", ".join('new RegExp("%s", "i")' % js_str(r) for r in regexes)
        lines.append("    try {")
        lines.append("      var __h2eRes = [%s];" % arr)
        lines.append("      var __h2eOk = false;")
        lines.append("      for (var __h2eI = 0; __h2eI < __h2eRes.length; __h2eI++) {")
        lines.append("        if (__h2eRes[__h2eI].test(window.location.href)) { __h2eOk = true; break; }")
        lines.append("      }")
        lines.append("      if (!__h2eOk) return;")
        lines.append("    } catch (e) { /* 匹配规则异常则放行 */ }")

    if script_text:
        lines.append(script_text)
    lines.append("  }")

    # @run-at 调度（默认 document-idle）
    run_at = (meta["run_at"] or "").strip().lower()
    if run_at == "document-start":
        lines.append("  __h2eRunUserScript();")
    elif run_at == "document-body":
        lines.append("  (function __h2eWaitBody() {")
        lines.append('    if (document.body) { __h2eRunUserScript(); }')
        lines.append("    else { setTimeout(__h2eWaitBody, 15); }")
        lines.append("  })();")
    elif run_at == "document-end":
        lines.append('  if (document.readyState === "loading") {')
        lines.append('    document.addEventListener("DOMContentLoaded", function () { __h2eRunUserScript(); }, { once: true });')
        lines.append("  } else { __h2eRunUserScript(); }")
    else:  # document-idle（默认）
        lines.append('  if (document.readyState === "complete") { setTimeout(__h2eRunUserScript, 0); }')
        lines.append('  else { window.addEventListener("load", function () { setTimeout(__h2eRunUserScript, 0); }, { once: true }); }')

    lines.append("})();")
    return "\n".join(lines) + "\n"


# ───────────────────────── 网络抓取（favicon / 标题） ─────────────────────────

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

_RX_LINK = re.compile(r"<link\b[^>]*>", re.I)
_RX_REL = re.compile(r"rel\s*=\s*[\"']?([^\"'\s>/]+)", re.I)
_RX_HREF = re.compile(r"href\s*=\s*[\"']([^\"']+)[\"']", re.I)
_RX_SIZES = re.compile(r"sizes\s*=\s*[\"']([^\"']+)[\"']", re.I)
_RX_SIZE_NUM = re.compile(r"(\d+)\s*[x\u00d7]\s*(\d+)", re.I)
_RX_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _http_get(url, timeout=15, max_bytes=512 * 1024, accept=None):
    headers = {"User-Agent": _UA}
    if accept:
        headers["Accept"] = accept
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        return None
    return data


def extract_html_title(html_text):
    m = _RX_TITLE.search(html_text)
    if not m:
        return None
    t = re.sub(r"<[^>]+>", " ", m.group(1))
    t = html_mod.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or None


def extract_icon_hrefs(html_text):
    """按 apple-touch-icon 优先、尺寸大优先排序的图标候选列表。"""
    items = []
    for order, m in enumerate(_RX_LINK.finditer(html_text)):
        tag = m.group(0)
        rel_m = _RX_REL.search(tag)
        if not rel_m:
            continue
        rel = rel_m.group(1).lower()
        if "icon" not in rel or "mask-icon" in rel:
            continue
        href_m = _RX_HREF.search(tag)
        if not href_m:
            continue
        href = html_mod.unescape(href_m.group(1).strip())
        if not href:
            continue
        priority = 0 if "apple-touch" in rel else 1
        size = 0
        sm = _RX_SIZES.search(tag)
        if sm:
            sv = sm.group(1).strip().lower()
            if sv == "any":
                size = 999
            else:
                nm = _RX_SIZE_NUM.search(sv)
                if nm:
                    size = max(int(nm.group(1)), int(nm.group(2)))
        items.append((priority, -size, order, href))
    items.sort()
    return [h for _, _, _, h in items]


def fetch_site_icon(page_url):
    """抓网站图标，返回 (ico_bytes | None, html_title | None)。"""
    title = None
    html_text = None
    try:
        raw = _http_get(page_url, timeout=15, max_bytes=600 * 1024,
                        accept="text/html,application/xhtml+xml,*/*;q=0.5")
        if raw:
            html_text = raw.decode("utf-8", "replace")
            title = extract_html_title(html_text)
    except Exception:
        pass

    if html_text:
        for href in extract_icon_hrefs(html_text):
            try:
                absu = urljoin(page_url, href)
            except Exception:
                continue
            if not absu.lower().startswith(("http://", "https://")):
                continue
            # GDI+/Pillow 不支持 SVG，跳过
            low = absu.lower()
            if low.endswith(".svg") or ".svg?" in low:
                continue
            try:
                data = _http_get(absu, timeout=12, max_bytes=2 * 1024 * 1024)
            except Exception:
                continue
            if not data:
                continue
            ico = to_ico_bytes(data)
            if ico:
                return ico, title

    # 回退：站点根 favicon.ico
    try:
        root = "/".join(page_url.split("/")[:3])  # scheme://host
        data = _http_get(root + "/favicon.ico", timeout=10, max_bytes=1024 * 1024)
        if data:
            ico = to_ico_bytes(data)
            if ico:
                return ico, title
    except Exception:
        pass

    return None, title


# ───────────────────────── ICO 构造 ─────────────────────────

def pack_ico(frames):
    """frames: [(w, h, png_bytes), ...] -> 多尺寸 ICO 字节。"""
    out = struct.pack("<HHH", 0, 1, len(frames))
    entries = b""
    body = b""
    offset = 6 + 16 * len(frames)
    for w, h, png in frames:
        wb = 0 if w >= 256 else w
        hb = 0 if h >= 256 else h
        entries += struct.pack("<BBBBHHII", wb, hb, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        body += png
    return out + entries + body


def _png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _encode_rgba_png(w, h, pixel_fn):
    """纯 Python PNG 编码（RGBA，无压缩依赖之外的第三方库）。"""
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter: None
        for x in range(w):
            raw.extend(pixel_fn(x, y))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" +
            _png_chunk(b"IHDR", ihdr) +
            _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6)) +
            _png_chunk(b"IEND", b""))


def read_png_size(data):
    """读 PNG IHDR 宽高，失败返回 None。"""
    try:
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    except Exception:
        return None


def build_ico_from_image_pil(img):
    """Pillow 路径：任意图片 -> 多尺寸 PNG-in-ICO。"""
    max_side = max(img.width, img.height)
    sizes = [s for s in (256, 64, 48, 32, 16) if s <= max_side or s == 16]
    if not sizes:
        sizes = [16]
    frames = []
    for s in sizes:
        bmp = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        # 中心裁剪成正方形再缩放
        crop = min(img.width, img.height)
        box = ((img.width - crop) // 2, (img.height - crop) // 2,
               (img.width - crop) // 2 + crop, (img.height - crop) // 2 + crop)
        part = img.crop(box).convert("RGBA").resize((s, s), Image.LANCZOS)
        bmp.paste(part, (0, 0))
        buf = io.BytesIO()
        bmp.save(buf, "PNG")
        frames.append((s, s, buf.getvalue()))
    return pack_ico(frames)


def _find_font():
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates = [
        os.path.join(windir, "Fonts", "msyhbd.ttc"),
        os.path.join(windir, "Fonts", "msyh.ttc"),
        os.path.join(windir, "Fonts", "seguisb.ttf"),
        os.path.join(windir, "Fonts", "segoeui.ttf"),
        os.path.join(windir, "Fonts", "arialbd.ttf"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def build_letter_ico(title):
    """默认图标：渐变圆角方块 + 标题首字（无 Pillow 时为纯渐变圆）。"""
    letter = "H"
    try:
        t = (title or "").strip()
        if t:
            ch = t[0]
            if ch.isalnum() or ord(ch) >= 0x4E00:
                letter = ch
    except Exception:
        pass

    if HAS_PIL:
        try:
            size = 256
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            # 纵向渐变背景
            grad = Image.new("RGBA", (1, size))
            c1, c2 = (108, 99, 255), (72, 190, 255)
            for y in range(size):
                t = y / (size - 1)
                grad.putpixel((0, y), (
                    int(c1[0] + (c2[0] - c1[0]) * t),
                    int(c1[1] + (c2[1] - c1[1]) * t),
                    int(c1[2] + (c2[2] - c1[2]) * t),
                    255))
            grad = grad.resize((size, size))
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [8, 8, size - 8, size - 8], radius=int(size * 0.22), fill=255)
            img.paste(grad, (0, 0), mask)

            font = None
            font_path = _find_font()
            if font_path:
                font = ImageFont.truetype(font_path, int(size * 0.5))
            draw = ImageDraw.Draw(img)
            try:
                bbox = draw.textbbox((0, 0), letter, font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                pos = ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1])
            except Exception:
                pos = (size * 0.3, size * 0.25)
            draw.text(pos, letter, font=font, fill=(255, 255, 255, 255))
            return build_ico_from_image_pil(img)
        except Exception:
            pass

    # 无 Pillow 回退：纯 Python 渐变圆
    frames = []
    for s in (256, 48, 32, 16):
        png = _render_circle_png(s)
        frames.append((s, s, png))
    return pack_ico(frames)


def _render_circle_png(size):
    c1 = (108, 99, 255)
    c2 = (72, 190, 255)
    radius = size / 2 - max(1.0, size * 0.035)
    cx = cy = (size - 1) / 2
    offs = (-0.33, 0.0, 0.33)

    def px(x, y):
        r = g = b = a = 0.0
        n = 0
        for dy in offs:
            for dx in offs:
                xx, yy = x + dx, y + dy
                d = math.hypot(xx - cx, yy - cy)
                alpha = max(0.0, min(1.0, (radius - d) / 1.2))
                t = (xx + yy) / (2 * size)
                r += c1[0] + (c2[0] - c1[0]) * t
                g += c1[1] + (c2[1] - c1[1]) * t
                b += c1[2] + (c2[2] - c1[2]) * t
                a += alpha
                n += 1
        return (int(r / n), int(g / n), int(b / n), int(a / n * 255))

    return _encode_rgba_png(size, size, px)


def to_ico_bytes(data):
    """ICO/PNG/JPG 等字节 -> ICO 字节；无法处理返回 None。"""
    if not data or len(data) < 8:
        return None
    if data[:4] == b"\x00\x00\x01\x00":
        return data  # 已是 ICO
    if HAS_PIL:
        try:
            img = Image.open(io.BytesIO(data))
            return build_ico_from_image_pil(img)
        except Exception:
            return None
    # 无 Pillow：仅支持 PNG ≤256 直接嵌入
    wh = read_png_size(data)
    if wh and max(wh) <= 256:
        return pack_ico([(wh[0], wh[1], data)])
    return None


# ───────────────────────── 编译 ─────────────────────────

def find_csc():
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for c in (
        os.path.join(windir, "Microsoft.NET", "Framework64", "v4.0.30319", "csc.exe"),
        os.path.join(windir, "Microsoft.NET", "Framework", "v4.0.30319", "csc.exe"),
    ):
        if os.path.isfile(c):
            return c
    return None


def run_csc(csc, args_list, timeout=180):
    try:
        p = subprocess.run([csc] + args_list, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "编译超时"
    out = (p.stdout or b"") + (p.stderr or b"")
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = out.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = out.decode("utf-8", "replace")
    return p.returncode, text.strip()


# ───────────────────────── 主构建流程 ─────────────────────────

def sanitize_filename(s):
    if not s:
        return ""
    invalid = '<>:"/\\|?*\x00'
    out = []
    for ch in s.strip():
        if ord(ch) < 32 or ch in invalid:
            out.append("_")
        else:
            out.append(ch)
    r = "".join(out).strip().rstrip(".")
    return r


def is_reserved_device_name(name):
    n = (name or "").upper()
    if n in ("CON", "PRN", "AUX", "NUL"):
        return True
    for prefix in ("COM", "LPT"):
        if len(n) == len(prefix) + 1 and n.startswith(prefix) and n[-1].isdigit() and n[-1] != "0":
            return True
    return False


def build(cfg, progress=None):
    """执行完整构建，返回 dict(ok, exe, outdir, error, dataname)。"""
    say = progress or (lambda m: None)
    try:
        return _build_internal(cfg, say)
    except Exception as ex:
        say("[失败] %s" % ex)
        return {"ok": False, "error": str(ex), "exe": "", "outdir": "", "dataname": ""}


def _build_internal(cfg, say):
    # 1. 校验网址
    url = (cfg.get("url") or "").strip()
    if "://" not in url:
        url = "https://" + url
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError("网址无效，需以 http:// 或 https:// 开头")
    host = parsed.hostname or ""
    say("目标网址：" + url)

    # 2. 读取油猴脚本
    script_text = None
    script_path = (cfg.get("script") or "").strip()
    if script_path:
        if not os.path.isfile(script_path):
            raise RuntimeError("脚本文件不存在：" + script_path)
        with open(script_path, "r", encoding="utf-8-sig", errors="replace") as f:
            script_text = f.read()
        say("已读取脚本：%s（%d 字符）" % (os.path.basename(script_path), len(script_text)))
    meta = parse_userscript_meta(script_text)
    if script_text is not None:
        say("脚本元数据：name=%s，run-at=%s，match %d 条" % (
            meta["name"] or "-", meta["run_at"] or "(默认 idle)", len(meta["match"])))

    # 3. 图标 + 标题
    html_title = None
    icon_bytes = None
    icon_path = (cfg.get("icon") or "").strip()
    if icon_path:
        if not os.path.isfile(icon_path):
            raise RuntimeError("图标文件不存在：" + icon_path)
        with open(icon_path, "rb") as f:
            raw = f.read()
        icon_bytes = to_ico_bytes(raw)
        if icon_bytes:
            say("使用指定图标：" + os.path.basename(icon_path))
        else:
            say("图标文件无法解析，将改用默认图标")

    if icon_bytes is None:
        say("未指定图标，尝试读取网站图标…")
        icon_bytes, html_title = fetch_site_icon(url)
        if icon_bytes:
            say("已获取网站图标（%d 字节）" % len(icon_bytes))
        else:
            say("网站图标获取失败，将使用默认图标")

    title = (cfg.get("title") or "").strip()
    if not title and html_title:
        title = html_title.strip()
    if not title:
        title = host
    say("窗口标题：" + title)

    # 4. 应用名
    app_name = (cfg.get("name") or "").strip() or title
    app_name = sanitize_filename(app_name)
    if not app_name:
        app_name = "HookApp"
    if is_reserved_device_name(app_name):
        app_name += "_app"
    say("应用名称：" + app_name)

    if icon_bytes is None:
        icon_bytes = build_letter_ico(title)

    # 5. 唯一标识（按网址哈希：互斥量 + 数据目录）
    h = short_hash(url.lower())
    mutex_id = "HookApp_" + h
    data_name = h

    # 6. 桥接允许域名
    hosts = extract_allowed_hosts(meta, host)
    say("桥接允许域名：" + ", ".join(hosts))

    # 7. 组装注入脚本
    wrapper = compose_wrapper(script_text, meta, cfg.get("top_only", True))
    say("注入脚本组装完成（%d 字符）" % len(wrapper))

    # 8. 输出目录
    out_root = (cfg.get("out") or "").strip()
    if not out_root:
        out_root = os.path.join(BASE_DIR, "output")
    out_dir = os.path.join(out_root, app_name)
    os.makedirs(out_dir, exist_ok=True)
    exe_path = os.path.join(out_dir, app_name + ".exe")
    if os.path.isfile(exe_path):
        say("输出目录已有同名 exe，将覆盖")

    # 9. 临时目录渲染源码
    tmp = tempfile.mkdtemp(prefix="Hook2Exe_")
    try:
        tmp_lib = os.path.join(tmp, "lib")
        os.makedirs(tmp_lib)
        for key, path in LIB_FILES.items():
            if not os.path.isfile(path):
                raise RuntimeError("缺少依赖 DLL：" + path)
            shutil.copy2(path, os.path.join(tmp_lib, os.path.basename(path)))

        program_cs = (
            read_tpl("host_program.cs")
            .replace("__MUTEX_ID__", cs_str(mutex_id))
            .replace("__APP_TITLE__", cs_str(title))
        )
        hosts_cs = ", ".join('"%s"' % cs_str(x) for x in hosts) or '"localhost.invalid"'
        mainform_cs = (
            read_tpl("host_mainform.cs")
            .replace("__TARGET_URL__", cs_str(url))
            .replace("__APP_TITLE__", cs_str(title))
            .replace("__DATA_NAME__", cs_str(data_name))
            .replace("__ALLOWED_HOSTS__", hosts_cs)
            .replace("__TRAY_CLOSE__", "true" if cfg.get("tray", True) else "false")
        )
        httpbridge_cs = read_tpl("host_httpbridge.cs")
        manifest_xml = read_tpl("host_manifest.xml")

        def w(path, text, bom=True):
            enc = "utf-8-sig" if bom else "utf-8"
            with open(path, "w", encoding=enc, newline="\n") as f:
                f.write(text)

        w(os.path.join(tmp, "Program.cs"), program_cs)
        w(os.path.join(tmp, "MainForm.cs"), mainform_cs)
        w(os.path.join(tmp, "HttpBridge.cs"), httpbridge_cs)
        w(os.path.join(tmp, "app.manifest"), manifest_xml)
        w(os.path.join(tmp, "user.js"), wrapper, bom=False)
        with open(os.path.join(tmp, "app.ico"), "wb") as f:
            f.write(icon_bytes)

        # 10. 定位编译器
        csc = find_csc()
        if not csc:
            raise RuntimeError("未找到 .NET Framework 编译器 csc.exe（需 Windows 自带的 .NET Framework 4.x）")
        say("编译器：" + csc)

        # 11. 编译
        args = [
            "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
            "/out:" + exe_path,
            "/win32icon:" + os.path.join(tmp, "app.ico"),
            "/resource:" + os.path.join(tmp, "user.js") + ",HookApp.hook.js",
            "/win32manifest:" + os.path.join(tmp, "app.manifest"),
            "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll",
            "/r:System.Windows.Forms.dll", "/r:System.Web.Extensions.dll",
            "/r:System.Net.Http.dll",
            "/r:" + os.path.join(tmp_lib, "Microsoft.Web.WebView2.Core.dll"),
            "/r:" + os.path.join(tmp_lib, "Microsoft.Web.WebView2.WinForms.dll"),
            os.path.join(tmp, "Program.cs"),
            os.path.join(tmp, "HttpBridge.cs"),
            os.path.join(tmp, "MainForm.cs"),
        ]
        say("正在编译…")
        code, out = run_csc(csc, args)
        if code != 0:
            raise RuntimeError("编译失败（exit=%s）：\r\n%s" % (code, out))
        if out:
            say("编译器输出：" + out)

        # 12. 运行时 DLL 拷到输出目录
        for path in LIB_FILES.values():
            shutil.copy2(path, os.path.join(out_dir, os.path.basename(path)))

        say("生成成功：" + exe_path)
        say("输出目录共 4 个文件（exe + 3 个 DLL），需一起分发。")
        say("应用数据目录：%%LOCALAPPDATA%%\\HookApp\\%s" % data_name)
        return {"ok": True, "error": "", "exe": exe_path, "outdir": out_dir,
                "dataname": data_name}
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


# ───────────────────────── CLI ─────────────────────────

def run_cli(argv):
    ap = argparse.ArgumentParser(
        prog="hook2exe.py",
        description="把「网址 + 油猴脚本」打包成 Edge WebView2 桌面应用")
    ap.add_argument("--url", help="目标网址（必填）")
    ap.add_argument("--script", help="油猴脚本路径（.js/.txt，可选）")
    ap.add_argument("--title", help="窗口标题（留空自动取网页标题）")
    ap.add_argument("--name", help="应用名 / exe 文件名")
    ap.add_argument("--icon", help="图标文件（留空自动抓网站 favicon）")
    ap.add_argument("--out", help="输出根目录（默认 ./output）")
    ap.add_argument("--no-tray", action="store_true", help="关闭窗口直接退出（默认最小化到托盘）")
    ap.add_argument("--all-frames", action="store_true", help="允许脚本在 iframe 内运行")
    ap.add_argument("--gui", action="store_true", help="强制打开图形界面")
    a = ap.parse_args(argv)

    if a.gui or not a.url:
        run_gui()
        return

    cfg = {
        "url": a.url,
        "script": a.script or "",
        "title": a.title or "",
        "name": a.name or "",
        "icon": a.icon or "",
        "out": a.out or "",
        "tray": not a.no_tray,
        "top_only": not a.all_frames,
    }
    r = build(cfg, safe_print)
    sys.exit(0 if r["ok"] else 1)


# ───────────────────────── GUI（tkinter） ─────────────────────────

def run_gui():
    import queue
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    root = tk.Tk()
    root.title("Hook2Exe · 网页打包工具（Edge 内核）")
    root.geometry("720x640")
    root.minsize(680, 600)

    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except Exception:
        pass

    q = queue.Queue()
    state = {"busy": False}

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="把「网址 + 油猴脚本」打包成常驻桌面应用（exe，Edge WebView2 内核，支持 GM_* API）",
              foreground="#5a6274").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

    def add_row(row, label, browse=None, browse_dir=False, width=None):
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8))
        var = tk.StringVar()
        ent = ttk.Entry(frm, textvariable=var, width=width or 52)
        ent.grid(row=row, column=1, sticky="we", pady=3)

        def do_browse():
            if browse_dir:
                d = filedialog.askdirectory(parent=root)
                if d:
                    var.set(d)
            else:
                f = filedialog.askopenfilename(parent=root, filetypes=browse)
                if f:
                    var.set(f)

        if browse or browse_dir:
            ttk.Button(frm, text="浏览…", width=8, command=do_browse).grid(
                row=row, column=2, padx=(8, 0), pady=3)
        return var

    v_url = add_row(1, "目标网址 *")
    v_script = add_row(2, "油猴脚本（可选）",
                       browse=[("脚本文件 (*.js;*.txt)", "*.js;*.txt"), ("所有文件 (*.*)", "*.*")])
    v_title = add_row(3, "窗口标题（自动取网页标题）")
    v_name = add_row(4, "应用名 / exe 名（默认用标题）")
    v_icon = add_row(5, "图标（自动抓网站 favicon）",
                     browse=[("图标文件 (*.ico;*.png;*.jpg;*.jpeg;*.bmp)", "*.ico;*.png;*.jpg;*.jpeg;*.bmp"),
                             ("所有文件 (*.*)", "*.*")])
    v_out = add_row(6, "输出目录", browse_dir=True)
    v_out.set(os.path.join(BASE_DIR, "output"))

    opt = ttk.Frame(frm)
    opt.grid(row=7, column=1, sticky="w", pady=6)
    tray_var = tk.BooleanVar(value=True)
    top_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(opt, text="关闭窗口时最小化到托盘", variable=tray_var).pack(side="left")
    ttk.Checkbutton(opt, text="仅顶层窗口注入脚本（推荐）", variable=top_var).pack(side="left", padx=(16, 0))

    btns = ttk.Frame(frm)
    btns.grid(row=8, column=1, sticky="w", pady=(4, 10))

    log_box = scrolledtext.ScrolledText(frm, height=16, font=("Consolas", 9), state="disabled")
    log_box.grid(row=9, column=0, columnspan=3, sticky="nsew")
    frm.columnconfigure(1, weight=1)
    frm.rowconfigure(9, weight=1)

    def log(msg):
        q.put(msg)

    def poll_log():
        try:
            while True:
                msg = q.get_nowait()
                if isinstance(msg, tuple) and msg and msg[0] == "__done__":
                    on_done(msg[1])
                    continue
                log_box.configure(state="normal")
                log_box.insert("end", "[%s] %s\n" % (time_str(), msg))
                log_box.see("end")
                log_box.configure(state="disabled")
        except queue.Empty:
            pass
        root.after(120, poll_log)

    def time_str():
        import datetime
        return datetime.datetime.now().strftime("%H:%M:%S")

    def on_done(result):
        state["busy"] = False
        gen_btn.configure(state="normal", text="生成应用")
        if result["ok"]:
            log("──── 生成完成 ────")
            if messagebox.askyesno(
                    "Hook2Exe",
                    "生成成功！\n\n%s\n\nexe 需与同目录 3 个 DLL 一起分发。\n是否打开输出目录？" % result["exe"]):
                open_folder(result["outdir"])
        else:
            messagebox.showerror("Hook2Exe", "生成失败：\n\n%s" % result["error"])

    def open_folder(path):
        try:
            if path and os.path.isdir(path):
                subprocess.Popen(["explorer.exe", path])
        except Exception:
            pass

    def worker(cfg):
        try:
            r = build(cfg, log)
        except Exception as ex:
            r = {"ok": False, "error": str(ex)}
        q.put(("__done__", r))

    def generate():
        if state["busy"]:
            return
        url = v_url.get().strip()
        if not url:
            messagebox.showwarning("Hook2Exe", "请填写目标网址")
            return
        cfg = {
            "url": url,
            "script": v_script.get().strip(),
            "title": v_title.get().strip(),
            "name": v_name.get().strip(),
            "icon": v_icon.get().strip(),
            "out": v_out.get().strip(),
            "tray": tray_var.get(),
            "top_only": top_var.get(),
        }
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")
        log("开始生成…")
        state["busy"] = True
        gen_btn.configure(state="disabled", text="生成中…")
        threading.Thread(target=worker, args=(cfg,), daemon=True).start()

    gen_btn = ttk.Button(btns, text="生成应用", command=generate)
    gen_btn.pack(side="left")
    ttk.Button(btns, text="打开输出目录",
               command=lambda: open_folder(v_out.get().strip() or os.path.join(BASE_DIR, "output"))
               ).pack(side="left", padx=(10, 0))

    def on_close():
        if state["busy"]:
            if not messagebox.askyesno("Hook2Exe", "正在生成应用，退出会中断。确定退出？"):
                return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    poll_log()
    root.mainloop()


# ───────────────────────── 入口 ─────────────────────────

def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    argv = sys.argv[1:]
    if not argv:
        run_gui()
        return
    run_cli(argv)


if __name__ == "__main__":
    main()
