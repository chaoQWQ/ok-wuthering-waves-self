from dataclasses import dataclass, replace
import math
import threading
import time

from src.utils.ScreenMotionTracker import ScreenMotionTracker


@dataclass(frozen=True)
class ImageViewState:
    center: tuple
    game_scale: float
    confidence: float
    source: str
    t: float


class LogicalProjection:
    def __init__(self, projection, ratio):
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError('显示缩放比例无效')
        self.projection = projection
        self.ratio = ratio
        self.context = projection.context

    def state(self, now=None):
        if self.context != self.projection.context:
            return None
        state = self.projection.state(now=now)
        return replace(state, game_scale=state.game_scale / self.ratio) if state else None


class ImageAnchoredProjection:
    MAX_ABSOLUTE_AGE = 6.0
    MAX_IMAGE_AGE = 0.75

    def __init__(self, capture=None):
        self.capture = capture
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.error = None
        self.context = None
        self.region = None
        self.tracker = None
        self.reference_center = None
        self.reference_scale = None
        self.reference_pixel = None
        self.absolute_at = 0.0
        self.image_at = 0.0
        self.current = None
        self.pending = None
        self.frames = 0
        self.latest_frame = None
        self.latest_at = 0.0

    def start(self):
        if self.capture is not None and self.thread is None:
            self.thread = threading.Thread(target=self._run, name='MapImageProjection', daemon=True)
            self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise TimeoutError('地图图像采集线程未结束')

    def reset(self):
        with self.lock:
            self.context = None
            self.current = None
            self.tracker = None
            self.pending = None
            self.latest_frame = None

    def state(self, now=None):
        if self.error is not None:
            raise RuntimeError('地图图像采集失败') from self.error
        now = time.monotonic() if now is None else now
        with self.lock:
            if (now - self.absolute_at > self.MAX_ABSOLUTE_AGE or
                    now - self.image_at > self.MAX_IMAGE_AGE):
                return None
            return self.current

    def _crop(self, frame):
        x, y, w, h = self.region
        return frame[y:y + h, x:x + w]

    def _project(self, transform, timestamp):
        x, y, w, h = self.region
        anchor_x, anchor_y = self.reference_pixel
        mapped_x, mapped_y = transform.project(anchor_x - x, anchor_y - y)
        scale = self.reference_scale / transform.scale
        center = (self.reference_center[0] + (anchor_x - x - mapped_x) * scale,
                  self.reference_center[1] + (anchor_y - y - mapped_y) * scale)
        return ImageViewState(center, scale, 1.0, 'image', timestamp)

    def publish(self, frame, timestamp, region, center, game_scale, context, minimap=False, anchor_pixel=None):
        if self.error is not None:
            raise RuntimeError('地图图像采集失败') from self.error
        if not math.isfinite(game_scale) or game_scale <= 0:
            raise ValueError('地图图像比例无效')
        with self.lock:
            changed = context != self.context or tuple(region) != self.region
            if not changed and self.tracker is not None:
                transform = self.tracker.track(self._crop(frame), timestamp)
                if transform is not None:
                    observed = self._project(transform, timestamp)
                    radius = math.hypot(region[2], region[3]) / 2
                    residual = math.dist(observed.center, center) / game_scale
                    residual += radius * abs(observed.game_scale / game_scale - 1)
                    limit = 6.0 if minimap else 12.0
                    if residual <= limit:
                        center, game_scale = observed.center, observed.game_scale
                        moved = math.hypot(transform.dx, transform.dy)
                        if moved < (20 if minimap else 80) and abs(transform.scale - 1) < 0.04:
                            self.absolute_at = timestamp
                            self.image_at = timestamp
                            self.tracker.reference_time = timestamp
                            self.current = observed
                            self.pending = None
                            self._catch_up(timestamp)
                            return self.current
                    elif (self.pending is None or
                          math.dist(self.pending[0], center) / game_scale +
                          radius * abs(self.pending[1] / game_scale - 1) > limit):
                        self.pending = (tuple(center), game_scale)
                        return observed
            self.pending = None
            self.context = context
            self.region = tuple(region)
            self.tracker = ScreenMotionTracker(minimap=minimap, max_age=self.MAX_ABSOLUTE_AGE)
            self.tracker.anchor(self._crop(frame), timestamp)
            self.reference_center = tuple(center)
            self.reference_scale = game_scale
            self.reference_pixel = anchor_pixel or (frame.shape[1] / 2, frame.shape[0] / 2)
            self.absolute_at = timestamp
            self.image_at = timestamp
            self.current = ImageViewState(tuple(center), game_scale, 1.0, 'image', timestamp)
            self._catch_up(timestamp)
            return self.current

    def _catch_up(self, timestamp):
        if self.latest_frame is not None and self.latest_at > timestamp:
            self.observe(self.latest_frame, self.latest_at)

    def observe(self, frame, timestamp=None):
        timestamp = time.monotonic() if timestamp is None else timestamp
        with self.lock:
            self.latest_frame, self.latest_at = frame, timestamp
            if self.tracker is None or timestamp - self.absolute_at > self.MAX_ABSOLUTE_AGE:
                return None
            x, y, w, h = self.region
            if frame.shape[1] < x + w or frame.shape[0] < y + h:
                return None
            transform = self.tracker.track(self._crop(frame), timestamp)
            if transform is None:
                return None
            self.current = self._project(transform, timestamp)
            self.image_at = timestamp
            self.frames += 1
            return self.current

    def _run(self):
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()
                with self.lock:
                    active = self.context is not None
                if active and self.capture.connected():
                    frame = self.capture.get_frame()
                    if frame is not None:
                        self.observe(frame, time.monotonic())
                self.stop_event.wait(max(0.001, 1 / 30 - (time.monotonic() - started)))
        except Exception as error:
            self.error = error
            raise
