"""Guide and reference image downloader and cache for Kuro routes and map items.

Pure-logic helpers (URL validation, cache filename generation) are free of Qt,
networking, and threads, allowing safe import and testing in isolated test runners.
Network requests and image decoding are processed asynchronously on a background
worker thread without blocking the detection loop or user interface.

Feature: map-overlay-interaction
"""

from __future__ import annotations

import hashlib
import logging
import os
import posixpath
import re
from typing import Callable, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Trusted official Kurobbs hosts for guide and asset images.
ALLOWED_IMAGE_HOSTS = {
    "web-static.kurobbs.com",
    "api.kurobbs.com",
    "static.kurobbs.com",
}

# Maximum allowed file size for guide images: 5 megabytes.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Network timeout for image downloading (connect, read) in seconds.
DOWNLOAD_TIMEOUT = (5.0, 15.0)

# Default cache directory under assets.
DEFAULT_GUIDE_CACHE_DIR = "assets/stitched/guide_image_cache"

# Max dimensions for preview scaling in Description_Bubble.
MAX_BUBBLE_IMAGE_WIDTH = 280
MAX_BUBBLE_IMAGE_HEIGHT = 180


def is_allowed_image_host(host: str) -> bool:
    """Check whether the given host name is in the trusted Kurobbs domain list."""
    if not host or not isinstance(host, str):
        return False
    lower_host = host.strip().lower()
    if lower_host in ALLOWED_IMAGE_HOSTS:
        return True
    # Allow subdomains of kurobbs.com
    if lower_host.endswith(".kurobbs.com") and len(lower_host) > len(".kurobbs.com"):
        return True
    return False


def validate_image_url(url: Optional[str]) -> Optional[str]:
    """Validate that the given image URL belongs to a trusted official domain.

    Returns the cleaned URL string if valid; returns None if invalid or untrusted.
    """
    if not url or not isinstance(url, str):
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    try:
        parsed = urlparse(cleaned)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        return None
    if not is_allowed_image_host(parsed.netloc):
        logger.warning("Rejected untrusted image URL domain: %s", parsed.netloc)
        return None
    return cleaned


def build_kuro_image_url(image_path: Optional[str]) -> Optional[str]:
    """Construct an official Kurobbs static asset URL for a relative image path.

    If the image_path is already an absolute URL, validates its domain.
    If it is a relative path like 'adminConfig/61/...', joins it with the
    official static host 'https://web-static.kurobbs.com/'.
    """
    if not image_path or not isinstance(image_path, str):
        return None
    cleaned = image_path.strip()
    if not cleaned:
        return None

    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return validate_image_url(cleaned)

    # Relative path inside Kuro static assets
    url = f"https://web-static.kurobbs.com/{cleaned.lstrip('/')}"
    return validate_image_url(url)


def guide_cache_filename(node_id: str, url: str) -> str:
    """Generate a collision-resistant, filesystem-safe cache filename.

    Combines sanitized node id and a SHA-256 hash of the full URL, preserving
    a standard image extension (.png, .jpg, .webp).
    """
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "", str(node_id or "unknown"))
    if not safe_id:
        safe_id = "node"
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

    try:
        path = urlparse(url).path
        ext = posixpath.splitext(path)[1].lower()
    except Exception:
        ext = ""

    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"

    return f"{safe_id}_{url_hash}{ext}"


class GuideImageCache:
    """Asynchronous downloader and local on-disk cache for point guide images.

    Worker operations run on a background daemon thread so map detection and UI
    responsiveness are never interrupted.
    """

    def __init__(self, cache_dir: str = DEFAULT_GUIDE_CACHE_DIR) -> None:
        import queue
        import threading

        self._cache_dir = cache_dir
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
        except OSError:
            logger.exception("Failed to create guide image cache dir: %s", self._cache_dir)

        self._lock = threading.Lock()
        # cache_filename -> QImage
        self._images: dict = {}
        # cache_filename -> QPixmap (lazily converted on GUI thread or on demand)
        self._pixmaps: dict = {}
        # status per url: "loading", "ready", "failed"
        self._statuses: dict = {}
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._callbacks: dict[str, list[Callable]] = {}

        self._worker = threading.Thread(
            target=self._worker_loop, name="GuideImageCacheWorker", daemon=True
        )
        self._worker.start()

    def get_status(self, url: str) -> str:
        """Return download status: 'ready', 'loading', 'failed', or 'none'."""
        with self._lock:
            return self._statuses.get(url, "none")

    def get_pixmap(self, node_id: str, url: str):
        """Return the loaded QPixmap if ready; None otherwise."""
        valid_url = validate_image_url(url)
        if not valid_url:
            return None
        fname = guide_cache_filename(node_id, valid_url)
        with self._lock:
            pm = self._pixmaps.get(fname)
            if pm is not None:
                return pm
            img = self._images.get(fname)

        if img is None:
            cache_path = os.path.join(self._cache_dir, fname)
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "rb") as fh:
                        data = fh.read()
                    if data:
                        decoded = self._decode_and_scale(data)
                        if decoded is not None and not decoded.isNull():
                            img = decoded
                            with self._lock:
                                self._images[fname] = img
                                self._statuses[valid_url] = "ready"
                except Exception:
                    pass

        if img is not None:
            try:
                from PySide6.QtGui import QPixmap
                pm = QPixmap.fromImage(img)
                if not pm.isNull():
                    with self._lock:
                        self._pixmaps[fname] = pm
                    return pm
            except Exception:
                logger.exception("Failed to convert guide QImage to QPixmap")
        return None

    def request_image(self, node_id: str, url: str, on_ready: Optional[Callable] = None) -> None:
        """Request downloading or loading a guide image.

        If already loaded, invokes on_ready immediately if provided.
        Otherwise enqueues background download and registers the callback.
        """
        valid_url = validate_image_url(url)
        if not valid_url:
            if on_ready:
                on_ready(None, "failed")
            return

        fname = guide_cache_filename(node_id, valid_url)
        with self._lock:
            if fname in self._pixmaps or fname in self._images:
                pm = self.get_pixmap(node_id, valid_url)
                if on_ready:
                    on_ready(pm, "ready")
                return

            if self._statuses.get(valid_url) == "loading":
                if on_ready:
                    self._callbacks.setdefault(fname, []).append(on_ready)
                return

            self._statuses[valid_url] = "loading"
            if on_ready:
                self._callbacks.setdefault(fname, []).append(on_ready)

        self._queue.put((node_id, valid_url, fname))

    def _worker_loop(self) -> None:
        import queue

        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if task is None:
                self._queue.task_done()
                break

            node_id, url, fname = task
            try:
                self._process_download(node_id, url, fname)
            except Exception:
                logger.exception("Guide image processing error for %s (%s)", node_id, url)
                with self._lock:
                    self._statuses[url] = "failed"
                self._dispatch_callbacks(fname, None, "failed")
            finally:
                self._queue.task_done()

    def _process_download(self, node_id: str, url: str, fname: str) -> None:
        cache_path = os.path.join(self._cache_dir, fname)
        data = None

        if os.path.exists(cache_path):
            try:
                with open(cache_path, "rb") as fh:
                    data = fh.read()
            except OSError:
                data = None

        if not data:
            data = self._download_bytes(url)
            if data:
                try:
                    with open(cache_path, "wb") as fh:
                        fh.write(data)
                except OSError:
                    logger.exception("Failed to write guide image cache: %s", cache_path)

        if not data:
            with self._lock:
                self._statuses[url] = "failed"
            self._dispatch_callbacks(fname, None, "failed")
            return

        image = self._decode_and_scale(data)
        if image is None or image.isNull():
            with self._lock:
                self._statuses[url] = "failed"
            self._dispatch_callbacks(fname, None, "failed")
            return

        with self._lock:
            self._images[fname] = image
            self._statuses[url] = "ready"

        self._dispatch_callbacks(fname, image, "ready")

    def _download_bytes(self, url: str) -> Optional[bytes]:
        try:
            import requests

            resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
            if resp.status_code != 200:
                logger.warning("Guide image HTTP %d for %s", resp.status_code, url)
                return None

            ctype = resp.headers.get("Content-Type", "").lower()
            if not ctype.startswith("image/"):
                logger.warning("Guide image invalid Content-Type %s for %s", ctype, url)
                return None

            content = bytearray()
            for chunk in resp.iter_content(chunk_size=16384):
                if self._stop_event.is_set():
                    return None
                content.extend(chunk)
                if len(content) > MAX_IMAGE_BYTES:
                    logger.warning("Guide image exceeded maximum size (%d bytes) for %s", len(content), url)
                    return None
            return bytes(content)
        except Exception as exc:
            logger.warning("Guide image download failed for %s: %s", url, exc)
            return None

    def _decode_and_scale(self, data: bytes):
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QImage

            image = QImage()
            if image.loadFromData(data) and not image.isNull():
                if image.width() > MAX_BUBBLE_IMAGE_WIDTH or image.height() > MAX_BUBBLE_IMAGE_HEIGHT:
                    return image.scaled(
                        MAX_BUBBLE_IMAGE_WIDTH,
                        MAX_BUBBLE_IMAGE_HEIGHT,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                return image
        except Exception:
            pass

        # Fallback to Pillow if Qt imageplugins webp is unavailable
        try:
            import io
            from PIL import Image
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QImage

            with Image.open(io.BytesIO(data)) as pil_img:
                rgba = pil_img.convert("RGBA")
            width, height = rgba.size
            raw = rgba.tobytes("raw", "RGBA")
            image = QImage(raw, width, height, QImage.Format_RGBA8888).copy()
            if not image.isNull():
                if image.width() > MAX_BUBBLE_IMAGE_WIDTH or image.height() > MAX_BUBBLE_IMAGE_HEIGHT:
                    return image.scaled(
                        MAX_BUBBLE_IMAGE_WIDTH,
                        MAX_BUBBLE_IMAGE_HEIGHT,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                return image
        except Exception:
            logger.exception("Pillow decode failed for guide image")
        return None

    def _dispatch_callbacks(self, fname: str, image, status: str) -> None:
        with self._lock:
            cbs = self._callbacks.pop(fname, [])
        for cb in cbs:
            try:
                cb(image, status)
            except Exception:
                logger.exception("Guide image callback execution error")

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
