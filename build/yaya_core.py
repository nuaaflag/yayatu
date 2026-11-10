#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸭子图算法 —— Python 侧完整实现（不依赖 canvas / 浏览器像素）。

与前端 JS 的对应关系:
    KA()  -> extract_payload()
    HA()  -> parse_header()
    qA()  -> unwrap_binpng()
    pf()/yf()/q0() -> sha256 / hex / keystream

优势: 直接读 PNG 原始字节，完全不经过 canvas 的色彩管理/缩放，
      因此不受 WebView2 渲染路径影响。
"""

import hashlib
import io
import struct
import zlib

import numpy as np
from PIL import Image

REGION_X = 0.4
REGION_Y = 0.08
MAX_PAYLOAD = 1024 * 1024 * 1024   # 1 GB：放宽自原版 100MB，否则大视频被误报载荷损坏


# --------------------------------------------------------------------------
# 错误类型
# --------------------------------------------------------------------------
class DecodeError(Exception):
    """基类"""


class NeedPassword(DecodeError):
    pass


class WrongPassword(DecodeError):
    pass


class BadHeader(DecodeError):
    pass


class NoPayload(DecodeError):
    pass


# --------------------------------------------------------------------------
# 基础原语（对应 pf / yf / q0）
# --------------------------------------------------------------------------
def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def hexs(b: bytes) -> str:
    return b.hex()


def keystream(password: str, nonce: bytes, length: int) -> bytes:
    seed = (password + hexs(nonce)).encode("utf-8")
    out = bytearray()
    i = 0
    while len(out) < length:
        out += sha256(seed + str(i).encode("utf-8"))
        i += 1
    return bytes(out[:length])


# --------------------------------------------------------------------------
# KA(): 位平面抽取
# --------------------------------------------------------------------------
def extract_payload(arr_rgb: np.ndarray, bits: int):
    """
    arr_rgb: (h, w, 3) uint8
    返回 (payload_bytes | None, reason)
    """
    h, w = arr_rgb.shape[:2]
    rx, ry = int(w * REGION_X), int(h * REGION_Y)
    mask = (1 << bits) - 1

    low = (arr_rgb & mask).astype(np.uint8)
    per_pix = low.reshape(-1, 3)

    ys = np.repeat(np.arange(h), w)
    xs = np.tile(np.arange(w), h)
    keep = ~((xs < rx) & (ys < ry))
    samples = per_pix[keep].reshape(-1)

    bm = np.empty((samples.size, bits), dtype=np.uint8)
    for k in range(bits):
        bm[:, k] = (samples >> (bits - 1 - k)) & 1
    bs = bm.reshape(-1)

    if bs.size < 32:
        return None, "too few bits"

    declared = int.from_bytes(np.packbits(bs[:32]).tobytes(), "big")
    S = (w * h * 3 * bits) // 8
    if declared <= 0 or declared > MAX_PAYLOAD or declared > S:
        return None, f"bad length {declared}"
    if 32 + declared * 8 > bs.size:
        return None, f"truncated (need {declared}B)"

    payload = np.packbits(bs[32:32 + declared * 8]).tobytes()
    return payload, ""


# --------------------------------------------------------------------------
# qA(): .binpng 还原
# --------------------------------------------------------------------------
def _trim_container(flat: bytes) -> bytes:
    n = len(flat)
    # RIFF
    if n >= 12 and flat[:4] == b"RIFF":
        (riff,) = struct.unpack("<I", flat[4:8])
        total = riff + 8
        if 12 <= total <= n:
            return flat[:total]
    # MP4/MOV
    if n >= 8 and flat[4:8] == b"ftyp":
        off = 0
        while off + 8 <= n:
            (size,) = struct.unpack(">I", flat[off:off + 4])
            typ = flat[off + 4:off + 8]
            if size == 0:
                off = n
                break
            if size < 8 or off + size > n:
                break
            off += size
        if 0 < off <= n:
            return flat[:off]
        return flat
    # PNG
    if n >= 8 and flat[:8] == b"\x89PNG\r\n\x1a\n":
        off = 8
        while off + 12 <= n:
            (ln,) = struct.unpack(">I", flat[off:off + 4])
            typ = flat[off + 4:off + 8]
            off += 12 + ln
            if typ == b"IEND":
                return flat[:off]
        return flat
    # 兜底：裁尾部 0
    p = n - 1
    while p >= 0 and flat[p] == 0:
        p -= 1
    return flat[:p + 1]


def unwrap_binpng(data: bytes) -> bytes:
    """把 canvas 生成的 PNG 还原成原始字节流。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return data
    buf = data
    if not buf.endswith(b"IEND\xaeB`\x82"):
        if buf[-8:-4] == b"IEND":
            buf = buf + b"\xaeB`\x82"
        else:
            crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
            buf = buf + b"\x00\x00\x00\x00" + b"IEND" + struct.pack(">I", crc)
    im = Image.open(io.BytesIO(buf))
    im.load()
    flat = np.asarray(im.convert("RGB"), dtype=np.uint8).tobytes()
    return _trim_container(flat)


# --------------------------------------------------------------------------
# HA(): 头部解析
# --------------------------------------------------------------------------
def parse_header(raw: bytes, password: str):
    """返回 (data, ext)"""
    s = 0
    if len(raw) < 1:
        raise BadHeader("Header corrupted. 文件头损坏")

    encrypted = raw[s] == 1
    s += 1

    stored_hash = b""
    nonce = b""
    if encrypted:
        if len(raw) < s + 32 + 16:
            raise BadHeader("Header corrupted. 文件头损坏")
        stored_hash = raw[s:s + 32]
        s += 32
        nonce = raw[s:s + 16]
        s += 16

    if len(raw) < s + 1:
        raise BadHeader("Header corrupted. 文件头损坏")
    flen = raw[s]
    s += 1
    if len(raw) < s + flen + 4:
        raise BadHeader("Header corrupted. 文件头损坏")
    ext = raw[s:s + flen].decode("utf-8", "replace")
    s += flen

    (size,) = struct.unpack(">I", raw[s:s + 4])
    s += 4
    if size > MAX_PAYLOAD or s + size > len(raw):
        raise BadHeader("Payload corrupted or invalid data length. 有效载荷损坏或无效长度")

    body = raw[s:s + size]
    if len(body) != size:
        raise BadHeader("Data length mismatch. 数据长度不匹配")

    if encrypted:
        if not password:
            raise NeedPassword("Password required. 需要密码")
        check = sha256((password + hexs(nonce)).encode("utf-8"))
        if check != stored_hash:
            raise WrongPassword("Wrong password. 密码错误")
        ks = keystream(password, nonce, len(body))
        body = bytes(a ^ b for a, b in zip(body, ks))

    if ext.endswith(".binpng"):
        try:
            return unwrap_binpng(body), ext[: -len(".binpng")]
        except Exception:
            pass
    return body, ext


# --------------------------------------------------------------------------
# 顶层: 从 PNG 文件解密
# --------------------------------------------------------------------------
def decrypt_png(path: str, password: str = ""):
    """
    返回 dict:
        ok, ext, data, bits, size
    或抛 DecodeError(带中文说明)
    """
    try:
        im = Image.open(path)
        im.load()
        if im.mode not in ("RGB",):
            im = im.convert("RGB")
        arr = np.asarray(im, dtype=np.uint8)
    except Exception as e:
        raise NoPayload(
            "这张图无法作为 PNG 读取（文件可能损坏、不完整，或不是 PNG）。"
            "原始错误：%s" % e)

    found = None          # (bits, ext, data)
    saw_payload = []      # 读出合法长度字段的位深
    errors = []

    for bits in range(1, 9):
        raw, why = extract_payload(arr, bits)
        if raw is None:
            errors.append((bits, why))
            continue
        saw_payload.append(bits)
        try:
            data, ext = parse_header(raw, password)
            found = (bits, ext, data)
            break
        except NeedPassword:
            raise NeedPassword(
                "这张图有密码保护，请输入密码后再试。")
        except WrongPassword:
            raise WrongPassword(
                "密码错误。这张图确实有隐藏数据，但密码不对，请检查后重试。")
        except BadHeader as e:
            errors.append((bits, str(e)))
        except Exception as e:
            errors.append((bits, str(e)))

    if found:
        bits, ext, data = found
        return {"ok": True, "ext": ext, "data": data,
                "bits": bits, "size": len(data)}

    # 没找到
    if not saw_payload:
        raise NoPayload(
            "没有在这张图中找到隐藏数据。请确认选的是鸭鸭图加密后的 PNG 原文件"
            "（不要用截图、转发压缩或二次编辑的版本）。")
    raise BadHeader(
        "已找到隐藏数据，但头部解析失败。图片可能被重新压缩或裁剪过，"
        "或者不是用本工具加密的。")


def sniff_ext(data: bytes, ext: str) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:3] == b"GIF":
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "avi"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) > 8 and data[4:8] == b"ftyp":
        return "mp4"
    if data[:4] in (b"\x1aE\xdf\xa3", b"FLV\x01"):
        return "mkv"
    if data[:4] == b"\x30\x26\xb2\x75":
        return "wmv"
    return ext
