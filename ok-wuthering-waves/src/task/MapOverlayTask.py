import json
import math
import os
import threading
import time
from collections import deque
from typing import NamedTuple, Optional

import cv2
import numpy as np

from qfluentwidgets import FluentIcon

from ok import TriggerTask, Logger, og, get_path_relative_to_exe, Box
from src.task.BaseWWTask import BaseWWTask
from src.utils.AssetsDownloader import (
    ASSETS_URL, download_and_extract, missing_required_assets, read_asset_version,
)
from src.utils.MapItemOverlay import (
    MapItemOverlay, ITEM_COLORS, ITEM_PIXMAPS, ICON_SIZE,
    DrawCandidate, build_overlay_draw_items, select_overlay_content,
    bubble_text, opacity_for, player_ocr_to_game_units, wrap_text,
    format_status_lines, format_target_info, player_target_distance,
)
from src.utils.map_geometry import make_hitbox, bearing_degrees
from src.utils.NodeIconCache import NodeIconCache
from src.utils.PathRoute import load_path_route, build_path_layers, PathParseError, PathLayer, PATH_NODE_ICON_KEY
from src.utils.ChestRoute import load_chest_route, ChestRouteParseError
from src.utils.ChestGuidanceFilter import ChestGuidanceFilter
from src.utils.TargetTracker import (
    THRESHOLD_MIN, THRESHOLD_MAX, validate_threshold, TargetTracker,
    TargetRef, right_click_dispatch,
)

logger = Logger.get_logger(__name__)

TELEPORT_THRESHOLD = 500
TELEPORT_SETTLE_DISTANCE = 10
OUTLIER_RELATIVE_FACTOR = 2.0
OUTLIER_FIXED_THRESHOLD = 200

OVERLAY_DRAW_KEY = "map_items"

# ok overlay 的 draw(..., duration=...) 到点即过期并清除覆盖层。地图重匹配帧偶发
# >1s，若沿用 duration=1 会导致覆盖层在慢帧时闪烁/消失（问题4）。检测循环约每
# 100ms 会重画一次，用 3s 作为过期时间足以覆盖偶发慢帧而不会残留过久。
OVERLAY_DRAW_DURATION = 3.0

# Chest-search phase 1 intentionally stops at visual guidance + manual
# confirmation.  It does not generate movement input or attempt to open a
# chest.  The confirmation distance is only a guard against accidentally
# marking a chest while the player is still far away.
CHEST_CONFIRM_DISTANCE_DEFAULT = 1500
CHEST_TARGET_REFRESH_INTERVAL = 0.5
CHEST_CONFIRM_POLL_MIGRATION_KEY = '_Chest confirm polling migration v1'

# User-facing collection groups mapped to the raw ``location.type_id`` values
# shipped by map_items.db.  Labels remain stable English config values and are
# translated by gettext for the task UI.
COLLECTION_GROUP_TYPE_IDS = {
    'Chests': ('qzx_01', 'qzx_02', 'qzx_03', 'qzx_04'),
    'Sonance Caskets': ('sx', 'sx·lgn', 'sx·qq'),
    'Tidal Heritage': ('cx_01', 'cx_02', 'cx_03'),
    'Windchimers': ('Play_12',),
    'Whisperwind Butterflies': ('xsd',),
    'Flying Hunters': ('fls',),
    'Scenic Spots': ('gjd',),
    'Overflowing Palettes': ('ych',),
    'Music Fireflies': ('ylfy',),
    'Abyssal Caches': ('YHYC',),
}
DEFAULT_COLLECTION_GROUPS = ['Chests']

MAP_DIR = get_path_relative_to_exe('assets', 'stitched')

# 视图输入探针（鼠标动作 + 匹配结果配对采集）的落地目录，见
# src/utils/ViewInputProbe.py。默认关闭，由 '_View input probe' 配置开启。
VIEW_PROBE_DIR = get_path_relative_to_exe('logs', 'view_probe')

# 资源包（assets.zip）解压的根目录，即 MAP_DIR 的上一级 assets 目录。特征文件、物品
# 数据库、坐标/参数配置与 version.txt 都在其 stitched/ 子目录内。
ASSETS_DIR = os.path.dirname(MAP_DIR)

# 资源下载/解压状态与版本号在面板上的 info_set 键。
ASSETS_INFO_KEY = 'Map assets'
ASSETS_VERSION_INFO_KEY = 'Map assets version'

# 下载/解压前等待检测循环释放资源句柄（sqlite 连接、已加载特征）的最长秒数。检测循环
# 未在运行时会自然超时，此时文件也不会被占用，可直接覆盖。
ASSETS_SUSPEND_WAIT = 5.0

# Fixed route file (Path_Mode source) and completion-marks database
# (Requirements 4.2, 3.1). Resolved relative to the executable like MAP_DIR.
PATH_FILE = get_path_relative_to_exe('assets', 'path.json')
# The first video-assisted route is metadata-only until its video markers are
# calibrated to database location ids.  Keeping the default relative makes the
# setting portable between source checkout and packaged executable.
CHEST_ROUTE_FILE_DEFAULT = os.path.join(
    'assets', 'chest_routes', '1.0_yunlinggu_yulongtai_p5.json'
)
MARKS_DB_PATH = os.path.join(MAP_DIR, 'map_marks.db')

# Local cache directory for downloaded route-node icons (Requirement 11.4).
# Resolved relative to the executable like MAP_DIR / MARKS_DB_PATH so it lands
# next to the stitched map assets.
ICON_CACHE_DIR = os.path.join(MAP_DIR, 'icon_cache')

# Hit_Box outward expansion in pixels for big-map clickable icons (the Hit_Box
# definition allows 0..8px; Scheme C uses the upper bound for forgiving clicks).
HITBOX_EXPAND = 8

# 大地图图标的层级：已完成项置于底层（先绘制、命中优先级更低），未完成项在其上。
# 绘制顺序按 draw_items 列表顺序（后画的盖在上面），点击命中用 z（topmost_hit 取最大
# z），因此“置底”需要同时把已完成项排到列表最前并给更小的 z。
COMPLETED_Z = 0
ACTIVE_Z = 1


class ClickTarget(NamedTuple):
    """Resolves an InteractionOverlayWindow click index to a domain action.

    The window reports click indices aligned with the ``draw_items`` /
    ``hitboxes`` order it was last given (see ``InteractionOverlayWindow`` and
    ``topmost_hit``). The controller keeps a parallel ``ClickTarget`` list in the
    same order so a click index can be mapped back to either a DB ``location``
    (Normal_Mode) or a route ``PathNode`` (Path_Mode) and the matching
    bubble / mark / target action taken (Requirements 2, 3, 6, 7).

    - ``kind``: ``'item'`` for a DB location, ``'node'`` for a route node.
    - ``ref_id``: the completion / lookup key — ``location.id`` for items,
      ``Path_Node.position_id`` for nodes.
    - ``sx`` / ``sy``: the icon's window-local pixel anchor (for the bubble).
    - ``section_id`` / ``index``: the owning Section id and in-section index for
      nodes (``-1`` for items), used to set/cancel the Target.
    - ``name``: the node's ``position_name`` (used as its bubble text); empty for
      items (their description is fetched from the DB on click).
    """

    kind: str
    ref_id: str
    sx: int
    sy: int
    section_id: int = -1
    index: int = -1
    name: str = ""


class ChestTarget(NamedTuple):
    """The currently selected database chest for Normal_Mode guidance.

    ``location_id`` is the static ``location.id`` from ``map_items.db``.  The
    map id is kept separately by :class:`OverlayController` because the same
    location id must never be reused across a map switch without revalidation.
    """

    location_id: object
    name: str
    type_id: str
    x: float
    y: float
    distance: float


def collection_type_ids(selected_groups) -> tuple:
    """Expand configured collection-group labels into database type ids."""
    if isinstance(selected_groups, str):
        selected_groups = [selected_groups]
    selected_groups = list(selected_groups or [])
    result = []
    for group in selected_groups:
        for type_id in COLLECTION_GROUP_TYPE_IDS.get(str(group), ()):
            if type_id not in result:
                result.append(type_id)
    # query_nearby treats an empty filter as "all types".  An explicit empty
    # selection must instead draw/search nothing.
    return tuple(result) if result else ('__no_collection_type__',)


def selected_collection_type_ids(config) -> tuple:
    """Resolve the new group selector with a legacy-filter fallback."""
    groups = config.get('Collection types')
    if groups is None:
        legacy = config.get('_Item type filter', COLLECTION_GROUP_TYPE_IDS['Chests'])
        return tuple(legacy or ('__no_collection_type__',))
    return collection_type_ids(groups)


def sort_completed_to_bottom(draw_items, hitboxes, click_targets, completed_ids):
    """把已完成项排到列表最前（先绘制=底层），返回重排后的三个同序列表。

    大地图上已完成的物品/节点应该压在未完成项下面：绘制按 ``draw_items`` 顺序（后画的
    盖在上面），点击命中按 z（:func:`~src.utils.map_geometry.topmost_hit` 取最大 z），
    所以“置底”= 排到列表最前 + 给更小的 z。

    - 稳定排序：同组内（都完成 / 都未完成）保持原有相对顺序，物品的距离排序与路线节点
      的段内顺序都不被打乱；
    - draw item 第 8 位的 z 与命中区的 z 一并改写为 :data:`COMPLETED_Z` /
      :data:`ACTIVE_Z`，使重叠时点击优先落在未完成项上；
    - 完成态优先用 :class:`ClickTarget` 的 ``ref_id`` 判定，缺失时回退 draw item 的
      ``location_id``（第 7 位）；
    - 三个列表长度不一致时原样返回（防御，避免点击索引错位）。
    """
    count = len(draw_items)
    if count != len(hitboxes) or count != len(click_targets):
        return list(draw_items), list(hitboxes), list(click_targets)
    completed = completed_ids or set()
    order = []
    for index in range(count):
        target = click_targets[index]
        ref = getattr(target, 'ref_id', None)
        if ref is None:
            item = draw_items[index]
            ref = item[6] if len(item) > 6 else None
        done = ref is not None and ref in completed
        order.append((COMPLETED_Z if done else ACTIVE_Z, index))
    order.sort(key=lambda entry: (entry[0], entry[1]))
    items, boxes, targets = [], [], []
    for z, index in order:
        item = draw_items[index]
        if len(item) > 7:
            item = tuple(item[:7]) + (z,)
        items.append(item)
        box = hitboxes[index]
        if isinstance(box, tuple) and len(box) == 2:
            box = (box[0], z)
        boxes.append(box)
        targets.append(click_targets[index])
    return items, boxes, targets


MAP_REGION_SIZE = 1000
FRAME_CROP_SIZE = 400
FALLBACK_MAX_FAILURES = 4
BIG_MAP_SEARCH_MULTIPLIER = 10
SLOW_MAP_IDS = {'8'}


def _parse_position(text):
    parts = text.split(',')
    if len(parts) == 3:
        try:
            return tuple(int(p) for p in parts)
        except ValueError:
            pass
    return None


def _dist2d(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _load_coords_dict(stitched_dir):
    coords_path = os.path.join(stitched_dir, 'map_coords.json')
    try:
        with open(coords_path, 'r', encoding='utf-8') as f:
            coords_dict = json.load(f)
    except Exception:
        logger.warning(f"Failed to load coords: {coords_path}")
        coords_dict = {}
    return coords_dict


CONFIDENCE_THRESHOLD = 0.8

BIG_MAP_COLOR_CHECKS = [
    ((0.030, 0.094), (98, 150, 166)),
    ((0.890, 0.059), (249, 249, 238)),
    ((0.939, 0.931), (255, 255, 255)),
    ((0.035, 0.891), (236, 237, 235)),
]
BIG_MAP_COLOR_TOLERANCE = 15


def _map_region_size(coords_d, match_scale=None):
    """搜索区域边长（大地图像素）。

    区域只需覆盖「查询图投影到大地图上的尺寸」+ OCR 坐标误差余量，**不需要**随
    ``feature_upscale`` 等比放大：放大后地图像素变多，但 OCR 给的中心点精度不变，
    ``MAP_REGION_SIZE`` 在 ``us=2`` 下仍相当于原图 500 px（约 400 OCR 单位）的余量。
    实测（906，``us=2``）把区域按放大系数放到 2000 只会让匹配从 ~40 ms 涨到 ~110 ms，
    准确率反而略降（小地图 7/15 → 5/15），因此这里保持与 ``us=1`` 相同的基线。

    ``match_scale`` 是上一次匹配得到的 ``map_scale``（大地图像素 / 查询像素，已经
    包含放大系数），> 1 说明查询图比地图粗、投影后占更多地图像素，此时才需要放大区域。
    """
    size = MAP_REGION_SIZE
    if match_scale is not None and match_scale > 1:
        size = max(size, int(MAP_REGION_SIZE * match_scale))
    return size


def _filter_candidate_maps(ocr_pos, coords_dict):
    game_x = ocr_pos[0] * 100
    game_y = ocr_pos[1] * 100
    candidates = []
    for map_id, d in coords_dict.items():
        mn = d.get('min', [0, 0])
        mx = d.get('max', [0, 0])
        if mn[0] <= game_x <= mx[0] and mn[1] <= game_y <= mx[1]:
            candidates.append((map_id, d))
    return candidates


class OverlayController:
    """Three-state coordination between the detection loop and the render layer.

    This collaborator translates the ``run()`` detection loop's three states
    (minimap / big map / idle) into render + interaction actions, wiring the
    Qt-free pure-logic modules (``PathRoute`` / ``map_geometry`` /
    ``TargetTracker`` / ``MapMarksDB`` and the ``MapItemOverlay`` draw-item
    builders) to the render layer (the ``ok`` ``OverlayWindow`` for the minimap
    and the self-built ``InteractionOverlayWindow`` for the big map).

    It owns the *coordination* state (display mode, parsed route, target
    tracker, completed-mark set, the interaction window handle); the heavy IO /
    Qt work is delegated to the owning :class:`MapOverlayTask` and the window
    adapter. The existing positioning / denoise / teleport / map-lock logic in
    ``run()`` is left untouched -- this controller is only invoked from inside
    the three already-existing branches.

    Mouse-event wiring (task 11.3), the global advance hotkey + auto-advance
    (task 11.4) and the full create-failure / DB-failure fallbacks (task 11.5)
    are layered on. The hotkey is registered at initialization and only mutates
    target state on its listener thread; auto-advance is evaluated from the
    detection loop's player-coordinate update.

    Feature: map-overlay-interaction
    Requirements: 1.2, 1.3, 1.7, 1.8, 4.1, 4.2, 4.3, 4.5, 4.6, 5.1
    """

    def __init__(self, task):
        self.task = task
        # Display mode mirrors the 'Path mode' config switch (Requirement 4.1).
        self._path_mode = bool(task.config.get('Path mode', False))
        # Parsed route + target tracker, populated lazily on entering Path_Mode.
        self._route = None
        self._tracker = None
        self._route_load_failed = False
        # Route-node icon downloader/cache (Requirement 11). Created lazily on
        # first entering Path_Mode (starts a background worker thread) and
        # stopped in close(); ``None`` until then / when unavailable, in which
        # case node rendering simply falls back to the qzx_04 icon.
        self._icon_cache = None
        self._icon_cache_failed = False
        # Completion marks (Requirement 3.5/3.6); loaded lazily, best-effort.
        self._marks_db = None
        self._completed_ids = set()
        self._marks_loaded = False
        self._marks_account_id = None
        # Self-built clickable big-map window (Scheme C); created lazily.
        self._interaction_window = None
        self._interaction_unavailable = False
        # Dedicated click-through Qt window for the minimap direction cue. It
        # bypasses the native GDI overlay's unreliable z-order on some Windows
        # DPI configurations while keeping all input handling unchanged.
        self._minimap_direction_window = None
        self._minimap_direction_unavailable = False
        self._last_minimap_direction_at = 0.0
        # Global Advance_Hotkey listener (pynput GlobalHotKeys) on a background
        # thread; started on entering Path_Mode, stopped on leaving Path_Mode or
        # task destroy (Requirements 9.3, 9.9). Its callback only mutates target
        # state -- never touches Qt (task 11.4).
        self._hotkey_listener = None
        # At most one Description_Bubble at a time (Requirement 2.2). Stored as a
        # plain ``(x, y, text)`` tuple consumed by InteractionOverlayWindow.
        self._bubble = None
        # Player position captured when the bubble was opened, so a subsequent
        # big-map pan/move can close it (Requirement 2.5).
        self._bubble_player_pos = None
        # Most recent big-map player position (refreshed each on_bigmap frame).
        self._current_player_pos = None
        # Click index -> ClickTarget map for the current big-map frame, kept in
        # the same order as the hit-boxes handed to the window (task 11.3).
        self._click_targets: list = []
        # Last content rendered to the big-map window, so a click can re-render
        # (e.g. to show/hide a bubble or refresh completion opacity) without
        # rebuilding from scratch on the task thread.
        self._last_draw_items: list = []
        self._last_path_layers: tuple = ()
        # 与 _last_draw_items / _click_targets 同序的命中区，以及最近一帧的状态面板
        # 文本。点击后重排（已完成项置底）需要三者一起换序，因此重绘走 render_frame
        # 把内容 + 命中区 + 面板一次性下发，避免索引错位或面板丢失。
        self._last_hitboxes: list = []
        self._last_status_lines: tuple = ()
        # 大地图路线目标高亮点 (sx, sy) 或 None（无目标 / 找不到投影点）。每帧在
        # _build_bigmap_content 中根据 tracker.target 重算，传给交互窗口画 3px 红圈
        # （问题3）；右键取消目标后 tracker.target 变 None，下一帧此值即变 None，红圈消失。
        self._last_target_marker = None
        # Low-rate diagnostics for the selected chest -> minimap projection.
        # This is deliberately separate from the normal overlay frame counter
        # so a projection failure is visible instead of being silently hidden.
        self._chest_visual_log_counter = 0
        # 上一次设置到交互窗口的几何 (x, y, w, h)，未变化则跳过 set_window_geometry，
        # 减少多余 GUI 事件（额外优化）。
        self._last_geometry = None
        # Status_Panel 最近一次的有效字段值（Requirement 12.10）：当玩家 OCR 坐标不可用
        # 时，坐标显示「未知」而其余字段（地图 id / 比例 / 模式 / 目标信息）保留这些
        # 最近有效值。地图 id / 比例仅在取到有效值（非 None）时更新；模式与目标信息在
        # 玩家坐标可用时每帧更新（目标距离依赖玩家坐标）。
        self._status_last_map_id = None
        self._status_last_scale = None
        self._status_last_mode = None
        self._status_last_target_info = None
        # Normal_Mode chest-search state.  This is deliberately separate from
        # Path_Mode's TargetTracker: database locations are static map items,
        # not route nodes, and phase 1 only guides the player until manual
        # confirmation is received.
        self._chest_target = None
        self._chest_target_map_id = None
        # The opening hint is the exact same location.description used by the
        # clickable big-map bubble. Cache it per target so the minimap overlay
        # never opens SQLite on every detection frame and the text cannot
        # flicker while the selected target remains unchanged.
        self._chest_hint_target_id = None
        self._chest_hint_text = None
        self._chest_guidance_filter = ChestGuidanceFilter()
        self._chest_last_query_at = 0.0
        self._chest_confirm_requested = False
        # Collection confirmation uses the same read-only Win32 key-state
        # polling as the display toggle.  It avoids the focus-dependent
        # ``pynput.GlobalHotKeys`` delivery seen in the game while adding no
        # hook and consuming no key input.
        self._chest_confirm_key_down = False
        self._chest_confirm_poll_warned = False
        self._collection_filter_signature = None
        # Set by the read-only hotkey sampler and consumed on the task thread.
        # Keeping config/UI mutation on this thread avoids races with rendering.
        self._display_toggle_requested = False
        # The display toggle is sampled with the same read-only Win32 key-state
        # API already used by movement stabilization.  A rising-edge latch
        # makes one physical press produce exactly one toggle.
        self._display_toggle_key_down = False
        self._display_toggle_poll_warned = False
        # Optional video-route metadata.  A metadata-only manifest is useful
        # for validating the source/map/count pipeline, but never changes
        # nearest-chest selection until calibrated nodes are added.
        self._chest_route = None
        self._chest_route_path = None
        self._chest_route_load_failed = False
        # Connect the window's mouse signals to the controller exactly once.
        self._signals_connected = False
        # One-shot warning latches so the per-frame error paths surface their
        # info_set message only once instead of every detection frame (task
        # 11.5). Reset where a recovery makes a fresh warning meaningful again
        # (e.g. a later successful marks write re-arms the save warning).
        self._interaction_warned = False
        self._marks_read_warned = False
        self._marks_save_warned = False
        # Register the global advance hotkey up front so a single keypress
        # advances the target whenever one is set; with no target the callback
        # is a no-op (Requirements 9.3, 9.4). Stopped in close() on task destroy.
        self._start_hotkey()

    # ------------------------------------------------------------------
    # Mode handling (Requirements 4.1, 4.2, 4.3, 4.5, 4.6)
    # ------------------------------------------------------------------
    @property
    def path_mode(self) -> bool:
        return self._path_mode

    def toggle_mode(self) -> bool:
        """Toggle between Normal_Mode and Path_Mode (Requirements 4.1, 4.3)."""
        return self.set_path_mode(not self._path_mode)

    def set_path_mode(self, enabled: bool) -> bool:
        """Activate Path_Mode or Normal_Mode, keeping the config in sync.

        Entering Path_Mode loads ``assets/path.json`` (Requirement 4.2). When
        the route cannot be loaded the controller stays in Normal_Mode and
        surfaces a message (Requirement 4.4; the richer fallback handling is
        task 11.5). The two modes are mutually exclusive (Requirement 4.1) and a
        short status update is emitted so the interface reflects the active mode
        (Requirement 4.3).
        """
        enabled = bool(enabled)
        if enabled:
            if not self._ensure_route_loaded():
                # Load failed -> remain in Normal_Mode and persist that.
                self._path_mode = False
                self._sync_config_mode(False)
                return self._path_mode
            self._path_mode = True
        else:
            self._path_mode = False
        # Path_Mode and chest-search targets are mutually exclusive.  Keeping
        # a hidden DB target while a route is active would make the confirmation
        # hotkey ambiguous after switching modes.
        if self._path_mode:
            self._clear_chest_target()
        self._sync_config_mode(self._path_mode)
        self.task.info_set('Path mode', '路线模式' if self._path_mode else '普通模式')
        return self._path_mode

    def _sync_config_mode(self, enabled: bool) -> None:
        try:
            if bool(self.task.config.get('Path mode', False)) != enabled:
                self.task.config['Path mode'] = enabled
        except Exception as exc:  # pragma: no cover - config backend specific
            logger.warning(f"[Overlay] failed to sync Path mode config: {exc}")

    def _sync_mode_from_config(self) -> None:
        """Pick up a 'Path mode' change made through the settings UI."""
        cfg = bool(self.task.config.get('Path mode', False))
        if cfg != self._path_mode:
            self.set_path_mode(cfg)

    def _ensure_route_loaded(self) -> bool:
        """Load + parse ``assets/path.json`` once (Requirement 4.2)."""
        if self._route is not None:
            return True
        try:
            self._route = load_path_route(PATH_FILE)
        except PathParseError as exc:
            logger.warning(f"[Overlay] path route load failed: {exc}")
            self._route = None
            self._tracker = None
            self._route_load_failed = True
            self.task.info_set('Path mode', f'路线加载失败，保持普通模式：{exc}')
            return False
        threshold = self.task.config.get('_Arrival threshold (game units)', 1000)
        if not validate_threshold(threshold):
            threshold = 1000
        self._tracker = TargetTracker(self._route, arrival_threshold=float(threshold))
        self._route_load_failed = False
        # Route loaded successfully -> kick off node-icon prefetch for every
        # node so their downloaded icons are ready (or being fetched) by the
        # time they are rendered (Requirements 11.7, 5.2). Best-effort: any
        # failure just leaves nodes on the qzx_04 fallback.
        self._prefetch_node_icons()
        return True

    # ------------------------------------------------------------------
    # Route-node icons (Requirement 11): download/cache + per-node pixmap.
    # ------------------------------------------------------------------
    def _ensure_icon_cache(self):
        """Lazily create the :class:`NodeIconCache`, failure-safe (Req 11).

        The cache spins up a single background download thread on construction;
        it is created only once, on first use in Path_Mode. If construction ever
        fails the controller marks it unavailable and node rendering falls back
        to the ``qzx_04`` icon everywhere -- the detection loop is never
        interrupted (Requirement 11.12).
        """
        if self._icon_cache is not None:
            return self._icon_cache
        if self._icon_cache_failed:
            return None
        try:
            self._icon_cache = NodeIconCache(cache_dir=ICON_CACHE_DIR)
        except Exception as exc:
            logger.warning(f"[Overlay] node icon cache create failed: {exc}")
            self._icon_cache = None
            self._icon_cache_failed = True
            return None
        return self._icon_cache

    def _prefetch_node_icons(self) -> None:
        """Enqueue a prefetch for every route node's ``positionImg`` (Req 11.7).

        Gathers each node's ``position_img`` across all sections (blank/dupe
        handling is done inside :meth:`NodeIconCache.prefetch`) and hands them to
        the background downloader. Best-effort and non-fatal.
        """
        if self._route is None:
            return
        cache = self._ensure_icon_cache()
        if cache is None:
            return
        position_imgs = [
            node.position_img
            for section in self._route.sections
            for node in section.nodes
        ]
        try:
            cache.prefetch(position_imgs)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"[Overlay] node icon prefetch failed: {exc}")

    def _node_pixmap(self, position_img):
        """Return the downloaded icon pixmap for ``position_img`` or ``None``.

        Per-node lookup used by both the big-map node draw items and the minimap
        route layers: returns the ready downloaded icon when available, else
        ``None`` so callers fall back to the ``qzx_04`` icon (Requirements 5.2,
        11.8, 11.9). Never raises -- a lookup failure yields ``None``.
        """
        cache = self._icon_cache
        if cache is None:
            return None
        try:
            return cache.get_pixmap(position_img)
        except Exception:  # pragma: no cover - defensive, get_pixmap is safe
            return None

    # ------------------------------------------------------------------
    # Completion marks (Requirements 3.5, 3.6, 3.7).
    # ------------------------------------------------------------------
    def _collection_account_id(self) -> str:
        """Return the explicit local profile used for completion records."""
        from src.utils.MapMarksDB import normalize_account_id
        return normalize_account_id(
            self.task.config.get('Collection account', 'default')
        )

    def _ensure_marks(self) -> None:
        """Load the completed-mark set once, failure-safe (Requirement 3.7).

        If the marks DB cannot be opened or read (missing/corrupt DB, locked
        file, broken connection) the controller keeps rendering with an empty
        completed set and surfaces a one-time read-failure message via
        ``info_set`` -- the background detection loop and map matching are never
        interrupted (Requirements 3.7, 1.6, 10.3).
        """
        account_id = self._collection_account_id()
        if self._marks_loaded and self._marks_account_id == account_id:
            return
        account_changed = (
            self._marks_account_id is not None
            and self._marks_account_id != account_id
        )
        self._marks_loaded = True
        self._marks_account_id = account_id
        from src.utils.MapMarksDB import MapMarksDB, load_completed_or_empty
        # 1) Opening the DB may itself raise (Requirement 3.7) -> empty + warn.
        if self._marks_db is None:
            try:
                self._marks_db = MapMarksDB(MARKS_DB_PATH)
            except Exception as exc:
                logger.warning(f"[Overlay] marks db open failed: {exc}")
                self._marks_db = None
                self._completed_ids = set()
                self._warn_marks_read()
                return
        # 2) Reading may fail even with an open handle; load_completed_or_empty
        # yields an empty set on failure (best-effort), and we probe to know
        # whether to surface the read-failure message.
        try:
            self._completed_ids = self._marks_db.load_completed(account_id)
            if account_changed:
                self._clear_chest_target()
                self._chest_last_query_at = 0.0
            self.task.info_set('Collection account', account_id)
            logger.info(
                f"[CollectionAccount] profile={account_id!r} "
                f"completed={len(self._completed_ids)}"
            )
        except Exception as exc:
            logger.warning(f"[Overlay] marks db read failed: {exc}")
            self._completed_ids = load_completed_or_empty(
                self._marks_db, account_id
            )
            self._warn_marks_read()

    # ------------------------------------------------------------------
    # Phase-1 chest search: target selection + manual confirmation.
    # ------------------------------------------------------------------
    def _chest_search_enabled(self) -> bool:
        """Whether Normal_Mode should keep a nearest chest target."""
        return bool(self.task.config.get('Chest search', False)) and not self._path_mode

    @staticmethod
    def _same_id(left, right) -> bool:
        """Compare SQLite ids defensively across integer/text representations."""
        if left == right:
            return True
        try:
            return str(left) == str(right)
        except Exception:
            return False

    def _is_completed_id(self, location_id) -> bool:
        return any(self._same_id(location_id, completed)
                   for completed in (self._completed_ids or set()))

    def _clear_chest_target(self) -> None:
        self._chest_target = None
        self._chest_target_map_id = None
        self._chest_hint_target_id = None
        self._chest_hint_text = None
        self._chest_guidance_filter.reset()

    def _chest_hint_for_target(self, target) -> str:
        """Return and cache the big-map description for a chest target.

        The big-map click handler and this minimap hint deliberately share the
        same ``get_location_description`` + ``bubble_text`` path.  This keeps
        both views consistent, including the standard empty-description
        placeholder, while limiting the short-lived SQLite lookup to once per
        selected chest.
        """
        if target is None:
            self._chest_hint_target_id = None
            self._chest_hint_text = None
            return ''
        if (self._chest_hint_text is not None and
                self._same_id(self._chest_hint_target_id,
                              target.location_id)):
            return self._chest_hint_text

        description = None
        overlay = getattr(self.task, '_overlay', None)
        if overlay is not None:
            description = overlay.get_location_description(target.location_id)
        text = bubble_text(description)
        self._chest_hint_target_id = target.location_id
        self._chest_hint_text = str(text)
        logger.info(
            f"[ChestHint] target={target.location_id} "
            f"length={len(self._chest_hint_text)}"
        )
        return self._chest_hint_text

    @staticmethod
    def _resolve_chest_route_path(raw_path) -> Optional[str]:
        """Resolve a user setting without making assumptions about CWD."""
        if raw_path is None:
            return None
        path = str(raw_path).strip()
        if not path:
            return None
        if os.path.isabs(path):
            return os.path.normpath(path)
        parts = [part for part in path.replace('\\', '/').split('/') if part]
        return get_path_relative_to_exe(*parts) if parts else None

    def _ensure_chest_route_loaded(self):
        """Load optional video metadata once, failure-safe.

        The loader is intentionally independent from the game/OCR loop.  A
        malformed or missing manifest only affects the status line; nearest
        database chest search continues as before.
        """
        if not self._chest_search_enabled():
            return None
        raw_path = self.task.config.get(
            'Chest route file', CHEST_ROUTE_FILE_DEFAULT
        )
        path = self._resolve_chest_route_path(raw_path)
        if path == self._chest_route_path:
            return self._chest_route
        self._chest_route_path = path
        self._chest_route = None
        self._chest_route_load_failed = False
        if path is None:
            return None
        try:
            self._chest_route = load_chest_route(path)
        except ChestRouteParseError as exc:
            self._chest_route_load_failed = True
            logger.warning(f"[ChestSearch] video route load failed: {exc}")
            self.task.info_set('Chest search', f'宝箱路线加载失败：{exc}')
        return self._chest_route

    def _chest_distance(self, player_pos, target) -> Optional[float]:
        if not self._valid_player_pos(player_pos) or target is None:
            return None
        try:
            px, py = player_ocr_to_game_units(player_pos)
            return math.hypot(float(target.x) - px, float(target.y) - py)
        except (TypeError, ValueError):
            return None

    def _update_chest_search(self, player_pos, state_id) -> None:
        """Refresh the nearest uncompleted chest target without producing input.

        The query is throttled because ``run()`` can execute at 10 Hz.  Once a
        target exists only its distance is refreshed; the target is replaced
        after a confirmation or when the map changes.  This keeps the overlay
        stable while the player moves between frames.
        """
        if not self._chest_search_enabled():
            self._clear_chest_target()
            return
        self._ensure_chest_route_loaded()
        if state_id is None:
            # Minimap feature matching can briefly lose the map lock while the
            # player turns, fights, or crosses detailed terrain.  The target
            # was selected from a positively identified big map, so retain it
            # through this *unknown* state.  It will still be replaced as soon
            # as a different concrete map id is identified.  Clearing here made
            # the direction cue flash for only a few frames after closing the
            # big map, which was unusable for manual navigation.
            if self._chest_target is not None:
                distance = self._chest_distance(player_pos, self._chest_target)
                if distance is not None:
                    self._chest_target = self._chest_target._replace(
                        distance=distance
                    )
            return
        selected_type_ids = selected_collection_type_ids(self.task.config)
        if selected_type_ids != self._collection_filter_signature:
            self._collection_filter_signature = selected_type_ids
            self._clear_chest_target()
            self._chest_last_query_at = 0.0
            logger.info(
                f"[CollectionSearch] selected types={selected_type_ids!r}"
            )
        if not self._valid_player_pos(player_pos):
            return

        map_id = str(state_id)
        if self._chest_target is not None and self._chest_target_map_id != map_id:
            self._clear_chest_target()
        self._ensure_marks()

        if self._chest_target is not None:
            if self._is_completed_id(self._chest_target.location_id):
                self._clear_chest_target()
            else:
                distance = self._chest_distance(player_pos, self._chest_target)
                if distance is not None:
                    self._chest_target = self._chest_target._replace(distance=distance)
                return

        now = time.monotonic()
        if now - self._chest_last_query_at < CHEST_TARGET_REFRESH_INTERVAL:
            return
        self._chest_last_query_at = now

        overlay = getattr(self.task, '_overlay', None)
        if overlay is None:
            try:
                self.task._init_overlay()
            except Exception as exc:
                logger.warning(f"[ChestSearch] overlay init failed: {exc}")
                return
            overlay = getattr(self.task, '_overlay', None)
        if overlay is None:
            return

        try:
            px, py = player_ocr_to_game_units(player_pos)
            radius = float(self.task.config.get(
                '_Search radius (world units)', 10000
            ))
            if radius <= 0:
                return
            rows = overlay.query_nearby(
                px, py, radius,
                type_filter=selected_type_ids,
                state_id=state_id,
                with_location_id=True,
            )
        except Exception as exc:
            logger.warning(f"[ChestSearch] query failed: {exc}")
            return

        for location_id, name, type_id, ix, iy, dist in rows:
            if self._is_completed_id(location_id):
                continue
            self._chest_target = ChestTarget(
                location_id, str(name or '收集物'), str(type_id or ''),
                float(ix), float(iy), float(dist),
            )
            self._chest_target_map_id = map_id
            logger.info(
                f"[ChestSearch] target id={location_id} name={name!r} "
                f"map_id={map_id} distance={float(dist):.0f}"
            )
            return

        self._chest_target = None
        self._chest_target_map_id = map_id
        self._chest_guidance_filter.reset()

    def on_chest_confirm_hotkey(self) -> None:
        """Request confirmation; the detection loop performs the DB write.

        The read-only Win32 key-state poll calls this on the task thread.  It
        only flips a flag; the normal position-update path validates distance
        and performs the database write.
        """
        if self._chest_search_enabled():
            self._chest_confirm_requested = True

    def on_map_display_toggle_hotkey(self) -> None:
        """Request a complete high-value-map display toggle."""
        self._display_toggle_requested = True

    def _process_map_display_toggle(self) -> bool:
        """Toggle both minimap and world-map high-value-item overlays."""
        self._poll_map_display_toggle_hotkey()
        if not self._display_toggle_requested:
            return False
        self._display_toggle_requested = False
        currently_enabled = bool(
            self.task.config.get('_Overlay enabled', True)
            or self.task.config.get('World map overlay', True)
        )
        enabled = not currently_enabled
        self.task.config['_Overlay enabled'] = enabled
        self.task.config['World map overlay'] = enabled
        if enabled:
            message = '地图高价值物品显示：已开启'
        else:
            # Hide every presentation surface immediately.  The selected chest
            # and completion marks remain intact so enabling resumes the same
            # search state instead of losing user progress.
            self.task._clear_overlay()
            self._hide_interaction_window()
            self._hide_minimap_direction_window()
            message = '地图高价值物品显示：已关闭'
        self.task.info_set('High-value items display', message)
        logger.info(f"[MapDisplay] toggle hotkey enabled={enabled}")
        return True

    def _process_chest_confirm(self, player_pos, state_id=None) -> None:
        """Mark the selected chest only after the user confirms it manually."""
        if not self._chest_confirm_requested:
            return
        self._chest_confirm_requested = False
        if not self._chest_search_enabled():
            return

        target = self._chest_target
        if (target is not None and state_id is not None
                and self._chest_target_map_id != str(state_id)):
            # A map switch can happen between the hotkey event and this frame;
            # never apply a confirmation to a target from the previous map.
            self._clear_chest_target()
            self.task.info_set('Chest search', '地图已切换，请等待新的收集目标')
            return
        distance = self._chest_distance(player_pos, target)
        if target is None or distance is None:
            self.task.info_set('Chest search', '未找到可确认的收集目标')
            return

        try:
            limit = float(self.task.config.get(
                '_Chest confirm distance (world units)',
                CHEST_CONFIRM_DISTANCE_DEFAULT,
            ))
        except (TypeError, ValueError):
            limit = CHEST_CONFIRM_DISTANCE_DEFAULT
        limit = max(1.0, limit)
        if distance > limit:
            self.task.info_set(
                'Chest search',
                f'请先靠近收集物（当前距离 {distance:.0f}，确认范围 {limit:.0f}）',
            )
            return

        self._ensure_marks()
        old_completed = set(self._completed_ids or set())
        new_completed = set(old_completed)
        new_completed.add(target.location_id)
        self._completed_ids = self._persist_mark_change(
            old_completed, new_completed
        )
        if not self._is_completed_id(target.location_id):
            self.task.info_set('Chest search', '收集物标记失败，请重试')
            return

        self.task.info_set('Chest search', f'已确认领取：{target.name}')
        self._clear_chest_target()
        self._chest_last_query_at = 0.0

    def _chest_status_info(self) -> str:
        """Return a compact status-panel line for the current chest target."""
        if not self._chest_search_enabled():
            return '收集物搜寻：未启用'
        target = self._chest_target
        if target is None:
            return '收集物搜寻：当前地图无未确认目标'
        return f'收集目标：{target.name} 距离{int(round(target.distance))} 待确认'

    def _chest_route_status_info(self) -> Optional[str]:
        """Return the optional video-route validation status for the panel."""
        if not self._chest_search_enabled():
            return None
        if 'Chests' not in self.task.config.get(
                'Collection types', DEFAULT_COLLECTION_GROUPS):
            return None
        route = self._ensure_chest_route_loaded()
        if route is None:
            if self._chest_route_load_failed:
                return '宝箱路线：加载失败（已回退地图数据）'
            return '宝箱路线：未配置'
        if route.calibrated:
            state = f'已校准{sum(bool(node.location_id) for node in route.nodes)}点'
        else:
            state = '点位待校准'
        current_map = self.task._locked_map_id
        try:
            if current_map is not None and int(current_map) != route.state_id:
                state += f'，当前地图{current_map}不匹配'
        except (TypeError, ValueError):
            pass
        return f'宝箱路线：{route.route_label}（{state}）'

    def _chest_minimap_visual(self, player_pos, state_id=None):
        """Build the minimap marker and edge arrow for the selected chest."""
        target = self._chest_target
        if target is None or not self._valid_player_pos(player_pos):
            return None, None
        minimap_box = self.task.get_box_by_name('box_minimap')
        if minimap_box is None:
            return None, None
        try:
            scale_per_1000 = self.task._minimap_scale_per_1000()
            scale = float(scale_per_1000) / 1000.0
            if scale <= 0:
                return None, None
            sample = self._chest_guidance_filter.update(
                player_pos, target.location_id, target.x, target.y,
                map_confirmed=state_id is not None,
                movement_active=self._manual_movement_active(),
            )
            px, py = sample.player_x, sample.player_y
            center_x = minimap_box.x + minimap_box.width / 2
            center_y = minimap_box.y + minimap_box.height / 2
            marker = self.task._overlay.project_to_minimap(
                target.x, target.y, px, py, scale, center_x, center_y
            )
            bearing = sample.bearing
            self._chest_target = target._replace(distance=sample.distance)
            # The target may be outside the visible minimap search circle;
            # keep only the edge direction indicator in that case, otherwise a
            # native full-screen canvas could paint a red box elsewhere on the
            # game window.
            radius = min(minimap_box.width, minimap_box.height) / 2
            if math.hypot(marker[0] - center_x, marker[1] - center_y) > radius:
                marker = None
            self._chest_visual_log_counter += 1
            if (self._chest_visual_log_counter <= 3 or
                    self._chest_visual_log_counter % 50 == 0):
                logger.info(
                    f"[ChestMinimapDirection] target={target.location_id} "
                    f"player=({px:.0f},{py:.0f}) "
                    f"target_pos=({target.x:.0f},{target.y:.0f}) "
                    f"bearing={bearing:.1f} distance={sample.distance:.0f} "
                    f"nearby={sample.nearby} rejected={sample.rejected} "
                    f"marker={marker} box=("
                    f"{minimap_box.x},{minimap_box.y},"
                    f"{minimap_box.width},{minimap_box.height})"
                )
            return marker, (
                bearing, minimap_box, sample.distance, sample.nearby
            )
        except (TypeError, ValueError, AttributeError) as error:
            logger.warning(f"[ChestMinimapDirection] projection failed: {error!r}")
            return None, None

    # ------------------------------------------------------------------
    # Three-state entry points (called from run())
    # ------------------------------------------------------------------
    def on_minimap(self, player_pos, state_id) -> None:
        """Minimap state: render via ok OverlayWindow, hide the big-map window.

        Normal_Mode keeps the existing DB-item minimap rendering unchanged;
        Path_Mode renders only the route layers (Requirements 1.3, 4.5, 4.6,
        5.1). The interaction window is hidden so nothing interactive lingers
        outside the big map (Requirement 1.8).
        """
        self._sync_mode_from_config()
        self._hide_interaction_window()
        if not self.task.config.get('_Overlay enabled'):
            self._hide_minimap_direction_window()
            return
        if self._path_mode and self._route is not None:
            self._hide_minimap_direction_window()
            # Player coordinate update: evaluate auto-advance before drawing so
            # the edge direction arrow reflects the (possibly) advanced target
            # (Requirements 8.2, 9.1, 9.2). No target -> no-op (Requirement 9.4).
            self.maybe_auto_advance(player_pos)
            # Status_Panel text built after auto-advance so target info reflects
            # the (possibly) advanced target; drawn on the ok OverlayWindow
            # (Requirements 12.3, 12.9).
            status_lines = self.compute_status_lines(player_pos, minimap=True)
            self._draw_minimap_path(player_pos, state_id, status_lines=status_lines)
        else:
            # 普通模式：先加载完成集合再绘制，使小地图排除已完成物品（问题1a）。
            # build_draw_items(minimap=True) 会剔除 completed_ids 中的项。
            self._ensure_marks()
            self._process_chest_confirm(player_pos, state_id)
            self._update_chest_search(player_pos, state_id)
            target_marker, edge_arrow = self._chest_minimap_visual(
                player_pos, state_id
            )
            # Build status after guidance filtering so its distance agrees with
            # the visible arrow/banner instead of exposing the raw OCR jump.
            status_lines = self.compute_status_lines(player_pos, minimap=True)
            self.task._draw_overlay(
                player_pos, state_id=state_id, completed_ids=self._completed_ids,
                status_lines=status_lines, target_marker=target_marker,
                edge_arrow=edge_arrow,
            )
            self._draw_minimap_direction_window(edge_arrow, target_marker)

    def on_bigmap(self, player_pos, game_scale) -> None:
        """Big-map state: render via the clickable InteractionOverlayWindow.

        Builds mode-aware content (icons with completion opacity, or route
        layers), shows the window, repaints it and refreshes its ``setMask``
        hit region (Requirements 1.2, 1.7, 4.5, 4.6, 5.1). If the interaction
        window is unavailable, falls back to the existing ok-overlay big-map
        rendering so the feature degrades gracefully (the richer fallback +
        message is task 11.5).
        """
        self._hide_minimap_direction_window()
        if not self.task.config.get('World map overlay', True):
            self._hide_interaction_window()
            self.task._clear_overlay()
            return
        # 游戏窗口不可见/不在前台时的可见性门控（问题3a / 关键 bug 修复）。
        # ok 的 hwnd.visible == is_foreground()，仅当游戏窗口为前台时才为 True；
        # 点击我们自己的交互窗口会让游戏失去前台焦点，使 visible 变为 False，若此时
        # 直接隐藏窗口会把气泡清掉并导致点击查看描述“死锁”。因此当 visible 为 False
        # 时，进一步判断前台是否是“我们自己 app 的窗口”（用户点了交互窗口）：
        #   - our_active 为 True 表示我们 app 的某个窗口是活动窗口（前台是我们自己），
        #     此时不隐藏，继续正常显示与渲染；
        #   - 仅当 not visible 且 not our_active（前台是别的外部应用）时才隐藏并返回。
        # 截屏按 hwnd 抓取、与前台无关，故点击覆盖层不会导致 OCR/匹配失败，不会误触
        # 发 run() 的 on_idle 清除逻辑。
        hwnd = getattr(self.task, 'hwnd', None)
        visible = getattr(hwnd, 'visible', True) if hwnd is not None else True
        if not visible:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            our_active = app is not None and app.activeWindow() is not None
            if not our_active:
                self._hide_interaction_window()
                return
        self._sync_mode_from_config()
        if not self._path_mode:
            self._process_chest_confirm(player_pos, self.task._locked_map_id)
            self._update_chest_search(player_pos, self.task._locked_map_id)
        window = self._ensure_interaction_window()
        if window is None:
            # Create-failure fallback (Requirement 1.9): render the big map with
            # the ok OverlayWindow (not clickable) and surface a one-time message,
            # without blocking map drag/zoom or background matching. The
            # Status_Panel still renders on this fallback overlay (Requirement 12.9).
            self._warn_interaction()
            status_lines = self.compute_status_lines(
                player_pos, minimap=False, game_scale=game_scale
            )
            self.task._draw_overlay_screen_center(
                player_pos, game_scale, status_lines=status_lines
            )
            return
        # A big-map pan/move closes any open Description_Bubble (Requirement 2.5).
        self._close_bubble_if_panned(player_pos)
        if self._path_mode and self._route is not None:
            # Player coordinate update: auto-advance before rebuilding content so
            # the rendered target reflects the advance (Requirements 9.1, 9.2);
            # no target -> no-op (Requirement 9.4).
            self.maybe_auto_advance(player_pos)
        window_draw_items, line_layers, hitboxes = self._build_bigmap_content(
            player_pos, game_scale
        )
        # 连线分流到穿透式 ok OverlayWindow 绘制（路线模式画线、普通模式清线）；交互
        # 窗口不再画线，因此其 setMask 掩码只剩节点命中区(+气泡)，轻量、跟手、不挡拖动。
        self._draw_bigmap_lines(line_layers)
        # Cache the rendered content so a click can re-render without rebuilding.
        # 交互窗口不再画线，故缓存的 path_layers 恒为空，_rerender_bigmap 也不带线。
        self._last_draw_items = list(window_draw_items)
        self._last_path_layers = ()
        self._last_hitboxes = list(hitboxes)
        # 几何仅在变化时才重设，减少多余 GUI 事件（额外优化）。
        geometry = self._client_geometry()
        if geometry != self._last_geometry:
            self._last_geometry = geometry
            window.set_window_geometry(*geometry)
        window.show_overlay()
        # Status_Panel text for the big map (Requirements 12.3, 12.9), built after
        # the (possible) auto-advance above so target info reflects the current
        # target; rendered on the InteractionOverlayWindow and its box unioned
        # into setMask so it is not clipped (Requirement 12.8).
        status_lines = self.compute_status_lines(
            player_pos, minimap=False, game_scale=game_scale
        )
        self._last_status_lines = tuple(status_lines) if status_lines else ()
        # 合并为单次渲染帧：内容 + 气泡 + 命中区 + 状态面板在同一个 GUI 事件里原子更新，
        # 避免 render_items / update_mask 两次排队信号产生的半更新中间帧。连线已分流到
        # ok 覆盖层，故 path_layers 传空 () —— 交互窗口 _compose_mask_region 里路线
        # stroker 部分为空，掩码只剩节点命中区(+气泡+状态面板)。
        window.render_frame(
            window_draw_items, bubble=self._bubble, path_layers=(),
            hitboxes=hitboxes, target_marker=self._last_target_marker,
            status_lines=status_lines,
        )

    def on_idle(self) -> None:
        """Idle state: hide the interaction window (Requirement 1.8).

        The ok OverlayWindow is cleared by the task's own ``_clear_overlay``;
        here we only ensure the clickable big-map window leaves no interactive
        content behind.
        """
        self._hide_interaction_window()
        hwnd = getattr(self.task, 'hwnd', None)
        visible = getattr(hwnd, 'visible', True) if hwnd is not None else True
        if (not visible or
                time.monotonic() - self._last_minimap_direction_at > 5.0):
            self._hide_minimap_direction_window()

    # ------------------------------------------------------------------
    # Mouse-event actions (task 11.3): Requirements 2, 3, 6, 7
    # ------------------------------------------------------------------
    def _click_target(self, idx):
        """Return the :class:`ClickTarget` for a window click index, or None."""
        if idx is None or not (0 <= idx < len(self._click_targets)):
            return None
        return self._click_targets[idx]

    def on_left_click(self, idx) -> None:
        """Left single click: show the clicked item/node Description_Bubble.

        Switches the single allowed bubble to the clicked icon (Requirements
        2.1, 2.2, 2.3, 6.6, 7.2). For a DB item the bubble text is the location's
        ``description`` (with the empty-description placeholder applied); for a
        route node it is the node's ``position_name``.
        """
        target = self._click_target(idx)
        if target is None:
            return
        if target.kind == 'item':
            description = None
            overlay = getattr(self.task, '_overlay', None)
            if overlay is not None and target.ref_id is not None:
                description = overlay.get_location_description(target.ref_id)
            text = bubble_text(description)
        else:
            text = bubble_text(target.name)
        self._set_bubble(target.sx, target.sy, text)
        self._rerender_bigmap()

    def on_double_click(self, idx) -> None:
        """Left double click: set/replace the Target on a route node.

        Only meaningful in Path_Mode; sets the double-clicked node as the current
        Target, replacing any previous one (Requirements 7.1, 7.5). A double click
        also dismisses any open bubble. DB items have no target concept and are
        ignored.
        """
        target = self._click_target(idx)
        if target is None or target.kind != 'node':
            return
        if self._tracker is None or target.section_id < 0 or target.index < 0:
            return
        try:
            self._tracker.set_target(target.section_id, target.index)
        except ValueError as exc:
            logger.warning(f"[Overlay] set_target failed: {exc}")
            return
        # 便于确认目标已设定（问题3）；下一帧 on_bigmap 会据此画出红圈高亮。
        logger.info(
            f"[Overlay] target set: section={target.section_id} index={target.index}"
        )
        # A target change supersedes a pending bubble.
        self._bubble = None
        self._bubble_player_pos = None
        self._rerender_bigmap()

    def on_right_click(self, idx) -> None:
        """Right click: cancel the Target if it is the clicked node, else toggle
        the completion mark (Requirements 3.2, 3.3, 6.1, 6.2, 6.3, 7.3, 7.4).

        The unified priority (target-cancel beats mark-toggle) is delegated to
        the pure-logic :func:`right_click_dispatch`. In Normal_Mode there is no
        target, so the dispatch always falls through to toggling the item's mark.
        """
        target = self._click_target(idx)
        if target is None or target.ref_id is None:
            return
        self._ensure_marks()
        current_target = self._tracker.target if self._tracker is not None else None
        if target.kind == 'node':
            clicked_ref = TargetRef(section_id=target.section_id, index=target.index)
        else:
            # Items have no target identity; a ref that never equals the current
            # target keeps the dispatch on the mark-toggle branch.
            clicked_ref = TargetRef(section_id=-1, index=-1)

        new_target, new_completed = right_click_dispatch(
            current_target, clicked_ref, target.ref_id, self._completed_ids
        )

        if new_target is None and current_target is not None:
            # Priority 1: the clicked node was the Target -> cancel it, marks
            # left untouched (Requirements 6.3, 7.3).
            if self._tracker is not None:
                self._tracker.clear_target()
            self._rerender_bigmap()
            return

        # Priority 2: persist the toggled completion mark and refresh opacity.
        # On a write/delete failure the in-memory set is rolled back to match the
        # DB's existing records (Requirements 3.8, 6.5); the authoritative set is
        # returned so the cached opacity + re-render reflect the rollback.
        self._completed_ids = self._persist_mark_change(
            self._completed_ids, new_completed
        )
        self._refresh_completion_opacity()
        # 标记/取消标记后立即把已完成项压到底层（含命中区同步换序），无需等下一帧。
        self._reorder_completed_to_bottom()
        self._rerender_bigmap()

    def close_bubble(self) -> None:
        """Close the current Description_Bubble (Requirements 2.4, 2.5).

        Invoked when the player clicks empty space inside the mask, when the map
        pans, or when leaving Bigmap_Mode.
        """
        if self._bubble is None and self._bubble_player_pos is None:
            return
        self._bubble = None
        self._bubble_player_pos = None
        self._rerender_bigmap()

    # ------------------------------------------------------------------
    # Advance: global hotkey (manual) + arrival-based (auto). Task 11.4.
    # Requirements 8.2, 9.1, 9.2, 9.3, 9.4.
    # ------------------------------------------------------------------
    def on_advance_hotkey(self) -> None:
        """Manual target advance via the global Advance_Hotkey.

        Runs on the pynput listener thread, so it performs only a lightweight
        state change -- advancing the target one node within its Section -- and
        never touches Qt or triggers a repaint directly (design.md "线程与事件
        模型"). The next detection-loop frame (``on_minimap`` / ``on_bigmap``)
        rebuilds the render to reflect the new target (edge arrow / highlight).
        With no target set, :meth:`TargetTracker.advance` is a no-op so the
        keypress changes nothing (Requirements 9.3, 9.4).
        """
        tracker = self._tracker
        if tracker is None:
            return
        try:
            tracker.advance()
        except Exception as exc:  # pragma: no cover - defensive on listener thread
            logger.warning(f"[Overlay] advance hotkey failed: {exc}")

    def _confirm_hotkey(self) -> str:
        """Return the configured manual chest-confirm hotkey."""
        return str(self.task.config.get('Chest confirm hotkey', '<ctrl>+<f10>') or '').strip()

    def _map_display_toggle_hotkey(self) -> str:
        """Return the configured map-overlay show/hide hotkey."""
        return str(self.task.config.get(
            'Map display toggle hotkey', '<ctrl>+<f8>'
        ) or '').strip()

    @staticmethod
    def _hotkey_vk_codes(hotkey):
        """Translate the supported pynput-style hotkey into Win32 VK codes."""
        text = str(hotkey or '').strip().lower()
        if not text:
            return None
        aliases = {
            'ctrl': 0x11, 'control': 0x11,
            'ctrl_l': 0x11, 'ctrl_r': 0x11,
            'shift': 0x10, 'shift_l': 0x10, 'shift_r': 0x10,
            'alt': 0x12, 'alt_l': 0x12, 'alt_r': 0x12,
            'space': 0x20, 'tab': 0x09, 'enter': 0x0D,
        }
        result = []
        for raw_part in text.split('+'):
            part = raw_part.strip()
            if part.startswith('<') and part.endswith('>'):
                part = part[1:-1].strip()
            code = aliases.get(part)
            if code is None and len(part) == 1 and part.isalpha():
                code = ord(part.upper())
            if code is None and len(part) == 1 and part.isdigit():
                code = ord(part)
            if code is None and part.startswith('f') and part[1:].isdigit():
                number = int(part[1:])
                if 1 <= number <= 24:
                    code = 0x70 + number - 1
            if code is None:
                return None
            if code not in result:
                result.append(code)
        return tuple(result) if result else None

    @staticmethod
    def _win32_hotkey_down(vk_codes):
        if os.name != 'nt':
            return False
        import ctypes
        user32 = ctypes.windll.user32
        return all(user32.GetAsyncKeyState(code) & 0x8000
                   for code in vk_codes)

    def _poll_map_display_toggle_hotkey(self) -> None:
        """Detect the display key's rising edge without installing a new hook."""
        hotkey = self._map_display_toggle_hotkey()
        vk_codes = self._hotkey_vk_codes(hotkey)
        if vk_codes is None:
            self._display_toggle_key_down = False
            if hotkey and not self._display_toggle_poll_warned:
                self._display_toggle_poll_warned = True
                logger.warning(
                    f"[MapDisplay] unsupported toggle hotkey: {hotkey!r}"
                )
            return
        try:
            is_down = bool(self._win32_hotkey_down(vk_codes))
        except Exception as exc:
            is_down = False
            if not self._display_toggle_poll_warned:
                self._display_toggle_poll_warned = True
                logger.warning(
                    f"[MapDisplay] toggle hotkey polling failed: {exc}"
                )
        if is_down and not self._display_toggle_key_down:
            self.on_map_display_toggle_hotkey()
            logger.info(f"[MapDisplay] toggle hotkey detected: {hotkey!r}")
        self._display_toggle_key_down = is_down

    def _poll_chest_confirm_hotkey(self) -> None:
        """Detect the collection-confirm key's rising edge without a hook."""
        hotkey = self._confirm_hotkey()
        vk_codes = self._hotkey_vk_codes(hotkey)
        if vk_codes is None:
            self._chest_confirm_key_down = False
            if hotkey and not self._chest_confirm_poll_warned:
                self._chest_confirm_poll_warned = True
                logger.warning(
                    f"[ChestConfirm] unsupported confirm hotkey: {hotkey!r}"
                )
            return
        try:
            is_down = bool(self._win32_hotkey_down(vk_codes))
        except Exception as exc:
            is_down = False
            if not self._chest_confirm_poll_warned:
                self._chest_confirm_poll_warned = True
                logger.warning(
                    f"[ChestConfirm] hotkey polling failed: {exc}"
                )
        if is_down and not self._chest_confirm_key_down:
            if self._chest_search_enabled():
                self.on_chest_confirm_hotkey()
                self.task.info_set(
                    'Chest search', '已检测确认快捷键，正在校验目标距离'
                )
                logger.info(
                    f"[ChestConfirm] hotkey detected: {hotkey!r}"
                )
            else:
                self.task.info_set(
                    'Chest search', '确认快捷键已检测，但收集物搜寻未启用'
                )
                logger.info(
                    f"[ChestConfirm] hotkey ignored while search disabled: "
                    f"{hotkey!r}"
                )
        self._chest_confirm_key_down = is_down

    def maybe_auto_advance(self, player_pos) -> bool:
        """Auto-advance the target when the player reaches it.

        Called from the detection loop's player-coordinate update (the minimap
        ``result`` and big-map ``self._last_valid`` branches). The player's OCR
        coordinates are converted to game units (x100) via
        :func:`player_ocr_to_game_units` before the distance check
        (Requirement 9.10); arrival within the configured Arrival_Threshold
        advances a single node, debounced so a stationary player only advances
        once (Requirements 9.1, 9.2). With no target this is a no-op
        (:meth:`TargetTracker.maybe_auto_advance` returns ``False``).
        """
        tracker = self._tracker
        if tracker is None or player_pos is None:
            return False
        gx, gy = player_ocr_to_game_units(player_pos)
        advanced = tracker.maybe_auto_advance(gx, gy)
        if advanced:
            # 诊断日志（问题3）：确认每帧都在评估、且到达阈值时确实推进了目标。
            # 不改变阈值默认值与推进逻辑，仅在发生推进时记录新目标。
            logger.info(f"[Overlay] auto-advance -> {tracker.target}")
        return advanced

    def _start_hotkey(self) -> None:
        """Register the global Advance_Hotkey listener on a background thread.

        Called once at controller initialization (and idempotent if called
        again). Uses pynput's ``GlobalHotKeys`` (a daemon listener thread). The
        hotkey string is read from the ``Advance hotkey`` config in pynput format
        (default ``<ctrl>+<f9>``). The callback (:meth:`on_advance_hotkey`) only
        mutates tracker state; rendering is refreshed by the next detection
        frame (Requirements 9.3, 9.9). A registration failure is logged and left
        non-fatal so the rest of the feature keeps working.
        """
        if self._hotkey_listener is not None:
            return
        hotkey = self.task.config.get('Advance hotkey', '<ctrl>+<f9>')
        toggle_hotkey = self._map_display_toggle_hotkey()
        callbacks = {hotkey: self.on_advance_hotkey}
        # Map display and collection confirmation deliberately use read-only
        # GetAsyncKeyState polling in the task loop. GlobalHotKeys did not
        # reliably deliver every configured combination while the game owned
        # focus. The historical listener remains only for route advancement.
        if toggle_hotkey and toggle_hotkey in callbacks:
            logger.warning(
                f"[Overlay] map display hotkey conflicts with another hotkey: "
                f"{toggle_hotkey!r}"
            )
        try:
            from pynput import keyboard
            listener = keyboard.GlobalHotKeys(callbacks)
            listener.daemon = True
            listener.start()
            logger.info(
                f"[Overlay] keyboard hotkeys registered: advance={hotkey!r}; "
                f"confirm_poll={self._confirm_hotkey()!r}; "
                f"map_display_poll={toggle_hotkey!r}"
            )
        except Exception as exc:  # pragma: no cover - pynput/runtime specific
            logger.warning(
                f"[Overlay] advance hotkey register failed ({hotkey!r}): {exc}"
            )
            self._hotkey_listener = None
            return
        self._hotkey_listener = listener

    def _stop_hotkey(self) -> None:
        """Stop the global Advance_Hotkey listener (on task destroy)."""
        listener = self._hotkey_listener
        self._hotkey_listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception as exc:  # pragma: no cover - pynput/runtime specific
                logger.warning(f"[Overlay] advance hotkey stop failed: {exc}")

    # -- bubble / render helpers -------------------------------------------
    def _restore_game_focus(self) -> None:
        """[已弃用，不再调用] 点击后把前台焦点还给游戏窗口（问题2）。

        原用于在每次点击后 ``hwnd.bring_to_front()`` 把游戏抬回前台，但该抬前台与置顶
        交互窗口互抢层级，导致点击后气泡瞬灭、叠加层闪烁。现改为让交互窗口根本不夺取
        焦点（``WA_ShowWithoutActivating`` + ``WS_EX_NOACTIVATE``，见
        :class:`InteractionOverlayWindow`），游戏始终保持前台，因此**不再需要**本方法，
        所有点击处理路径均已移除对它的调用。保留方法体仅为兼容性与历史参考，不应再被调用。
        """
        hwnd = getattr(self.task, 'hwnd', None)
        if hwnd is None:
            return
        bring = getattr(hwnd, 'bring_to_front', None)
        if not callable(bring):
            return
        try:
            bring()
        except Exception as e:  # pragma: no cover - Qt/win32 runtime specific
            logger.warning(f"[Overlay] restore game focus failed: {e}")

    def _set_bubble(self, sx, sy, text) -> None:
        """Store the single allowed bubble anchored next to the clicked icon."""
        # 问题2：描述每 25 字硬换行为多行气泡后再存储，交由交互窗口按行绘制。
        text = wrap_text(text, 25)
        half = ICON_SIZE // 2
        self._bubble = (int(sx) + half, int(sy) - half, text)
        self._bubble_player_pos = self._current_player_pos

    def _close_bubble_if_panned(self, player_pos) -> None:
        """Close the bubble when the big map has panned (Requirement 2.5)."""
        self._current_player_pos = player_pos
        if self._bubble is None:
            return
        prev = self._bubble_player_pos
        if prev is None:
            return
        if int(prev[0]) != int(player_pos[0]) or int(prev[1]) != int(player_pos[1]):
            self._bubble = None
            self._bubble_player_pos = None

    def _rerender_bigmap(self) -> None:
        """Repaint the big-map window with the cached content + current bubble.

        Used by click handlers (running on the GUI thread) to reflect a bubble
        or completion-opacity change immediately, without rebuilding content on
        the task thread.

        走 ``render_frame`` 下发完整一帧（内容 + 气泡 + 命中区 + 目标高亮 + 状态面板）：
        标记完成/取消完成后已完成项会被排到底层，绘制顺序与命中区必须同步换序，否则
        点击索引会错位；同时状态面板也不会因为重绘而消失。
        """
        window = self._interaction_window
        if window is None:
            return
        window.render_frame(
            self._last_draw_items, bubble=self._bubble,
            path_layers=self._last_path_layers,
            hitboxes=self._last_hitboxes,
            target_marker=self._last_target_marker,
            status_lines=self._last_status_lines,
        )

    def _refresh_completion_opacity(self) -> None:
        """Recompute cached draw-item opacity from the completed set (3.5)."""
        refreshed = []
        for item in self._last_draw_items:
            if len(item) > 6:
                location_id = item[6]
                opacity = opacity_for(location_id, self._completed_ids)
                item = item[:5] + (opacity,) + item[6:]
            refreshed.append(item)
        self._last_draw_items = refreshed

    def _reorder_completed_to_bottom(self) -> None:
        """把缓存帧里的已完成项排到底层（点击后即时生效）。

        对 ``_last_draw_items`` / ``_last_hitboxes`` / ``_click_targets`` 三个同序列表
        一起做稳定重排（见 :func:`sort_completed_to_bottom`），保证绘制顺序、命中掩码与
        点击索引到 :class:`ClickTarget` 的映射始终对应。
        """
        items, boxes, targets = sort_completed_to_bottom(
            self._last_draw_items, self._last_hitboxes, self._click_targets,
            self._completed_ids,
        )
        self._last_draw_items = items
        self._last_hitboxes = boxes
        self._click_targets = targets

    def _persist_mark_change(self, old_completed, new_completed) -> set:
        """Write the add/remove implied by a completion-set change to Marks_DB.

        Returns the authoritative completed set the caller should adopt: the
        optimistic ``new_completed`` on success, or -- when the write/delete
        fails -- the set reconciled against the DB's existing records so the
        in-memory state never diverges from what is persisted (Requirements 3.8,
        6.5). A save failure surfaces a one-time message via ``info_set`` and
        never crashes the GUI thread or interrupts the detection loop.

        When no marks DB is available (its open/read failed earlier, see
        :meth:`_ensure_marks`) there is nothing to persist to; the optimistic
        in-memory toggle is kept so the UI still responds, and the read-failure
        message has already been surfaced.
        """
        if self._marks_db is None:
            return set(new_completed)
        added = new_completed - old_completed
        removed = old_completed - new_completed
        try:
            for location_id in added:
                self._marks_db.add(location_id, self._marks_account_id)
            for location_id in removed:
                self._marks_db.remove(location_id, self._marks_account_id)
        except Exception as exc:
            logger.warning(f"[Overlay] mark persist failed: {exc}")
            from src.utils.MapMarksDB import reconcile_completed
            reconciled = reconcile_completed(
                new_completed, self._marks_db, self._marks_account_id
            )
            self._warn_marks_save()
            return reconciled
        # A successful write re-arms the save warning for any future failure.
        self._marks_save_warned = False
        return set(new_completed)

    # ------------------------------------------------------------------
    # One-shot user-visible warnings (task 11.5). Each latch keeps the
    # per-frame / per-click error paths from spamming info_set every time;
    # messages are routed through the task's info_set, consistent with the
    # rest of the task, and never interrupt the detection loop.
    # ------------------------------------------------------------------
    def _warn_interaction(self) -> None:
        if self._interaction_warned:
            return
        self._interaction_warned = True
        self.task.info_set('Overlay', '交互窗口创建失败，已回退到不可点击的覆盖层')

    def _warn_marks_read(self) -> None:
        if self._marks_read_warned:
            return
        self._marks_read_warned = True
        self.task.info_set('Overlay', '标记读取失败，已以空完成集合继续')

    def _warn_marks_save(self) -> None:
        if self._marks_save_warned:
            return
        self._marks_save_warned = True
        self.task.info_set('Overlay', '标记保存失败，已回滚至库内现有记录')

    # ------------------------------------------------------------------
    # Content builders
    # ------------------------------------------------------------------
    def _draw_bigmap_lines(self, line_layers) -> None:
        """把大地图路线连线分流到穿透式 ok OverlayWindow 绘制。

        ok 的 ``OverlayWindow``（``task.get_overlay_view()``）是穿透式
        （``WindowTransparentForInput``）、可自由绘制、无需 mask，因此细连线不会挡拖动，
        与宝箱/小地图共用同一覆盖层机制。

        - ``line_layers`` 非空（路线模式）：用 :meth:`MapItemOverlay.make_paint_callback`
          以 ``draw_path_nodes=False`` 只画连线 + 箭头、跳过节点图标（节点图标由交互
          窗口绘制），注册到 ok 覆盖层。``overlay_view`` 为 None 时跳过。
        - ``line_layers`` 为空（普通模式）：清掉 ok 覆盖层的线（``_clear_overlay``）。
        """
        if line_layers:
            overlay_view = self.task.get_overlay_view()
            if overlay_view is None:
                return
            callback = MapItemOverlay.make_paint_callback(
                [], path_layers=line_layers, draw_path_nodes=False, clip_box=None,
            )
            overlay_view.draw(
                OVERLAY_DRAW_KEY, callback, duration=OVERLAY_DRAW_DURATION
            )
            self.task._overlay_registered = True
        else:
            self.task._clear_overlay()

    def _build_bigmap_content(self, player_pos, game_scale):
        """Build ``(window_draw_items, line_layers, hitboxes)`` for the big map.

        - ``window_draw_items``：交互窗口要绘制的图标（普通模式=db 宝箱项含完成态
          不透明度；路线模式=可见路线节点图标）。
        - ``line_layers``：要在穿透式 ok 覆盖层绘制的连线（仅路线模式非空；普通模式为
          空 ``()``）。
        - ``hitboxes``：交互窗口命中区（与 ``window_draw_items`` / ``click_targets``
          同序）。

        用显式 if/else 实现 Normal_Mode / Path_Mode 互斥（Requirements 4.5, 4.6）：路线
        模式只出节点图标 + 连线，普通模式只出 db 项、无连线。完成态不透明度由
        :func:`build_overlay_draw_items` / :func:`opacity_for` 施加（Requirement 3.5）。
        db 项可见性矩形判定（view_bounds）保持不变。
        """
        task = self.task
        task._init_overlay()
        overlay = task._overlay

        radius = task.config.get('_Search radius (world units)') * BIG_MAP_SEARCH_MULTIPLIER
        if game_scale is not None and 0.01 < game_scale:
            scale = 1.0 / game_scale
        else:
            scale = task._get_default_scale_per_1000() / 1000.0

        type_filter = selected_collection_type_ids(task.config)
        player_x = player_pos[0] * 100
        player_y = player_pos[1] * 100

        _, _, w, h = self._client_geometry()
        center_x = w // 2
        center_y = h // 2
        # 可见区改用“矩形”判定：游戏画面是矩形，用圆判定会漏掉左右两侧（问题3）。
        # 外扩 margin 让进出视野的连线/图标自然过渡；宝箱与路线共用同一矩形边界。
        margin = ICON_SIZE * 2
        view_bounds = (-margin, -margin, w + margin, h + margin)

        self._ensure_marks()
        completed = self._completed_ids

        rows = overlay.query_nearby(
            player_x, player_y, radius, type_filter,
            state_id=task._locked_map_id, with_location_id=True,
        )
        candidates = []
        for location_id, name, type_id, ix, iy, dist in rows:
            sx, sy = overlay.project_to_minimap(
                ix, iy, player_x, player_y, scale, center_x, center_y
            )
            vx_min, vy_min, vx_max, vy_max = view_bounds
            if not (vx_min <= sx <= vx_max and vy_min <= sy <= vy_max):
                continue
            pixmap = ITEM_PIXMAPS.get(type_id)
            color = ITEM_COLORS.get(type_id)
            candidates.append(
                DrawCandidate(sx, sy, pixmap, name, color, location_id, 0)
            )
        db_items = build_overlay_draw_items(
            candidates, minimap=False, completed_ids=completed
        )

        # 两模式互斥（显式 if/else，不再经 select_overlay_content）：
        # - 路线模式：交互窗口画可见节点图标 + 命中区，连线走 ok 覆盖层；
        # - 普通模式：交互窗口画 db 宝箱项 + 命中区，无连线。
        if self._path_mode and self._route is not None:
            clipped_layers, node_draw_items, node_hitboxes, node_click_targets = \
                self._build_visible_path(
                    player_x, player_y, scale, center_x, center_y, view_bounds
                )
            window_draw_items = node_draw_items
            hitboxes = node_hitboxes
            click_targets = node_click_targets
            line_layers = clipped_layers
        else:
            hitboxes, click_targets = self._build_clickables(db_items)
            window_draw_items = db_items
            line_layers = ()

        # 初次/每帧渲染都把已完成项压到底层（绘制顺序 + 命中 z 同步换序）。
        window_draw_items, hitboxes, click_targets = sort_completed_to_bottom(
            window_draw_items, hitboxes, click_targets, completed
        )
        self._click_targets = click_targets
        self._last_target_marker = self._compute_target_marker(click_targets)
        return window_draw_items, line_layers, hitboxes

    def _build_visible_path(self, player_x, player_y, scale,
                            center_x, center_y, view_bounds):
        """按大地图宝箱套路把路线裁剪到可见区（问题3）。

        返回 ``(clipped_layers, node_draw_items, node_hitboxes, node_click_targets)``：

        - ``clipped_layers``：只含可见附近短折线的 :class:`PathLayer` 序列，供在穿透式
          ok 覆盖层绘制连线（掩码开销小、无远处乱线、随缩放抖动的远段被丢弃）；
        - ``node_draw_items``：每个可见节点的扩展 8-tuple
          ``(sx, sy, pixmap, name, color, opacity, location_id, z)``，供**交互窗口**绘制
          节点图标（qzx_04）；顺序与 ``node_hitboxes`` / ``node_click_targets`` 完全对应
          （同一遍循环产出）；完成路线节点随之变暗（opacity 0.4）；

        - 可见判定用**矩形**边界 ``view_bounds=(x_min,y_min,x_max,y_max)``（游戏画面为
          矩形，圆判定会漏掉左右两侧）；与 db 项共用同一矩形，投影用
          ``overlay.project_to_minimap`` 以相同的 player/scale/center 上下文，与 db 项
          投影完全一致，因此稳定、不随远处抖动。
        - 对每个 section 按**原始节点顺序**投影，按“节点是否可见”切分为若干极大连续
          可见 run（相邻可见节点组成一段折线）。每个 run 生成一个 :class:`PathLayer`
          （``color`` 为该 section 颜色，``points`` 为 run 内节点屏幕点，``node_ids``
          对应）。这样 path_layers 只含可见附近的短折线，掩码 stroker 开销小、无远处
          乱线、随缩放抖动的远段被丢弃；不固定缩放（继续用传入 ``scale``）、不丢弃近处
          可见节点。
        - 命中区与 ClickTarget 只为**可见节点**生成，且使用节点在 section 内的**原始
          index**（下面的 ``node_index``），保证双击 ``set_target(section_id, index)``
          正确、并让 :meth:`_compute_target_marker` 能按 section_id/index 找回目标。

        路线 state 门控与 build_path_layers 一致：仅当路线 ``state_id`` 与当前上下文
        ``_context_state_id`` 匹配时才产出，否则返回空（Requirements 5.5, 5.6）。
        """
        clipped_layers = []
        node_draw_items = []
        node_hitboxes = []
        node_click_targets = []
        if self._route is None:
            return (), node_draw_items, node_hitboxes, node_click_targets
        context_state_id = self._context_state_id()
        if context_state_id is None or self._route.state_id != context_state_id:
            return (), node_draw_items, node_hitboxes, node_click_targets

        overlay = self.task._overlay
        # 节点图标：优先使用该节点从网络下载的专属图标（NodeIconCache），未就绪 / 无
        # positionImg / 下载失败时回退统一的 qzx_04（Requirements 5.2, 11.8, 11.9）。
        # 颜色从 MapItemOverlay 常量取，opacity 依完成集合决定（完成路线节点变暗 0.4）。
        fallback_pixmap = ITEM_PIXMAPS.get(PATH_NODE_ICON_KEY)
        node_color = ITEM_COLORS.get(PATH_NODE_ICON_KEY)
        for section in self._route.sections:
            nodes = section.nodes
            # 与 db 项相同的投影；可见判定用同一屏幕圆，仅多一点外扩 margin。
            projected = [
                overlay.project_to_minimap(
                    node.x, node.y, player_x, player_y, scale, center_x, center_y
                )
                for node in nodes
            ]
            visible = [
                view_bounds[0] <= px <= view_bounds[2]
                and view_bounds[1] <= py <= view_bounds[3]
                for (px, py) in projected
            ]
            n = len(nodes)
            i = 0
            while i < n:
                if not visible[i]:
                    i += 1
                    continue
                # [i..j] 为极大连续可见 run。
                j = i
                while j + 1 < n and visible[j + 1]:
                    j += 1
                run_points = tuple(projected[k] for k in range(i, j + 1))
                run_node_ids = tuple(nodes[k].position_id for k in range(i, j + 1))
                clipped_layers.append(
                    PathLayer(
                        color=section.color,
                        points=run_points,
                        node_ids=run_node_ids,
                    )
                )
                for k in range(i, j + 1):
                    px, py = projected[k]
                    node = nodes[k]
                    node_hitboxes.append(
                        (make_hitbox(px, py, ICON_SIZE, HITBOX_EXPAND), 0)
                    )
                    node_click_targets.append(
                        ClickTarget(
                            kind='node', ref_id=node.position_id,
                            sx=int(px), sy=int(py),
                            section_id=section.section_id, index=k,
                            name=node.position_name,
                        )
                    )
                    # 可见节点的绘制项（交互窗口画节点图标）：8-tuple 顺序与命中区/
                    # ClickTarget 完全一致；opacity 依完成集合（完成路线节点变暗 0.4）。
                    # 每节点优先用下载图标，未就绪回退 qzx_04（Requirements 5.2, 11.8）。
                    node_pixmap = self._node_pixmap(node.position_img)
                    if node_pixmap is None:
                        node_pixmap = fallback_pixmap
                    node_draw_items.append(
                        (int(px), int(py), node_pixmap, node.position_name,
                         node_color,
                         opacity_for(node.position_id, self._completed_ids),
                         node.position_id, 0)
                    )
                i = j + 1
        return tuple(clipped_layers), node_draw_items, node_hitboxes, node_click_targets

    def _compute_target_marker(self, click_targets):
        """计算当前路线目标节点在大地图上的投影屏幕点 ``(sx, sy)`` 或 None（问题3）。

        仅路线模式且 tracker 存在目标时才可能非 None：在与目标同序构建的
        ``click_targets`` 中找到 ``kind=='node'`` 且 ``section_id`` / ``index`` 与
        ``tracker.target`` 一致的节点，取其 ``(sx, sy)`` 作为红圈圆心。无目标、非路线
        模式、或该目标节点不在当前帧可见节点中（找不到）时返回 None，使红圈消失。
        """
        if not self._path_mode and self._chest_search_enabled():
            chest = self._chest_target
            if chest is None:
                return None
            for ct in click_targets:
                if ct.kind == 'item' and self._same_id(ct.ref_id, chest.location_id):
                    return (ct.sx, ct.sy)
            return None
        tracker = self._tracker
        if not self._path_mode or tracker is None:
            return None
        target = tracker.target
        if target is None:
            return None
        for ct in click_targets:
            if (ct.kind == 'node'
                    and ct.section_id == target.section_id
                    and ct.index == target.index):
                return (ct.sx, ct.sy)
        return None

    def _build_clickables(self, db_items):
        """Compose Hit_Boxes and a parallel :class:`ClickTarget` list for DB items.

        仅处理 db 项（宝箱）：路线节点的命中区/ClickTarget 现由
        :meth:`_build_visible_path` 按可见区裁剪产出（问题3），不再在此处理 path_layers。
        本方法产出的 db 命中区在合并时排在最前（db 在前、path 在后），与掩码/索引顺序
        一致；调用方保证 Normal_Mode / Path_Mode 两组互斥、只有一组非空。
        """
        boxes = []
        targets = []
        for item in db_items:
            sx, sy = item[0], item[1]
            location_id = item[6] if len(item) > 6 else None
            z = item[7] if len(item) > 7 else 0
            boxes.append((make_hitbox(sx, sy, ICON_SIZE, HITBOX_EXPAND), z))
            targets.append(
                ClickTarget(kind='item', ref_id=location_id, sx=sx, sy=sy)
            )
        return boxes, targets

    def _draw_minimap_path(self, player_pos, state_id, status_lines=None) -> None:
        """Draw route layers on the minimap via the ok OverlayWindow.

        ``status_lines`` (optional) is the Status_Panel text drawn at the client
        area's bottom-left corner, outside the round-minimap clip
        (Requirements 12.3, 12.9).
        """
        task = self.task
        overlay_view = task.get_overlay_view()
        if overlay_view is None:
            return
        task._init_overlay()
        minimap_box = task.get_box_by_name('box_minimap')
        if minimap_box is None:
            return
        scale_per_1000 = task._minimap_scale_per_1000()
        scale = scale_per_1000 / 1000.0
        center_x = minimap_box.x + minimap_box.width / 2
        center_y = minimap_box.y + minimap_box.height / 2
        player_x = player_pos[0] * 100
        player_y = player_pos[1] * 100

        layers = ()
        # 路线层不再要求已锁定地图/上下文 state 匹配：只要 self._route 存在，就用路线
        # 自身的 state_id 构建层（问题1b/1c）。这样只要 OCR 坐标匹配即可绘制路线，
        # 远处的点投影后会被下方 clip_box 的小地图圆形裁剪自然隐藏。
        if self._route is not None:
            # 每节点传入下载图标（NodeIconCache），未就绪回退 qzx_04：build_path_layers
            # 会对每个节点调用 getter 填充 PathLayer.node_pixmaps，paint_path_layers 优先
            # 用之、否则回退 node_icon（Requirements 5.2, 11.8, 11.9）。
            layers = build_path_layers(
                self._route, self._route.state_id,
                player_x, player_y, scale, center_x, center_y,
                node_pixmap_getter=self._node_pixmap,
            )
        edge_arrow = self._edge_arrow(player_pos, minimap_box)
        # 目标红圈（问题1b）：tracker 有目标时取目标节点，投影到小地图坐标作为
        # target_marker=(sx,sy) 传入；无目标传 None。圈若落在小地图外会被 clip 裁掉。
        target_marker = None
        node = self._target_node()
        if node is not None:
            target_marker = task._overlay.project_to_minimap(
                node.x, node.y, player_x, player_y, scale, center_x, center_y
            )
        # 将路线连线/箭头裁剪到小地图圆形区域内，避免覆盖整个屏幕（问题4）。
        # minimap_box 即上面通过 get_box_by_name('box_minimap') 获取的小地图框。
        callback = MapItemOverlay.make_paint_callback(
            [], path_layers=layers, edge_arrow=edge_arrow, clip_box=minimap_box,
            target_marker=target_marker, status_lines=status_lines,
        )
        overlay_view.draw(OVERLAY_DRAW_KEY, callback, duration=OVERLAY_DRAW_DURATION)
        task._overlay_registered = True

    def _edge_arrow(self, player_pos, minimap_box):
        """Return ``(bearing_deg, minimap_box)`` for the target arrow, or None.

        When either the player's current position or the Target position cannot
        be obtained, the arrow is hidden (returns ``None``) while the Target
        itself is preserved -- it is never cleared here (Requirement 8.4).
        """
        node = self._target_node()
        if node is None:
            # No valid Target position -> hide the arrow, keep the Target.
            return None
        if not self._valid_player_pos(player_pos):
            # No valid player position -> hide the arrow, keep the Target.
            return None
        player_x = player_pos[0] * 100
        player_y = player_pos[1] * 100
        bearing = bearing_degrees(player_x, player_y, node.x, node.y)
        return (bearing, minimap_box)

    @staticmethod
    def _valid_player_pos(player_pos) -> bool:
        """Whether ``player_pos`` carries usable numeric x/y coordinates."""
        if player_pos is None:
            return False
        try:
            float(player_pos[0])
            float(player_pos[1])
        except (TypeError, ValueError, IndexError):
            return False
        return True

    def _target_node(self):
        if self._route is None or self._tracker is None or self._tracker.target is None:
            return None
        ref = self._tracker.target
        for section in self._route.sections:
            if section.section_id == ref.section_id:
                if 0 <= ref.index < len(section.nodes):
                    return section.nodes[ref.index]
        return None

    def _context_state_id(self):
        """Map the locked map id to the route ``state_id`` namespace.

        ``_locked_map_id`` is the items DB / coords map key (a string such as
        ``'906'``) while ``PathRoute.state_id`` (and ``path.json`` ``stateId``)
        is an integer. They share the same numeric identity, so the route is
        drawn only when ``int(_locked_map_id) == route.state_id``
        (Requirements 5.5, 5.6).
        """
        lid = self.task._locked_map_id
        if lid is None:
            return None
        try:
            return int(lid)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Status_Panel text (Requirement 12): built each frame and dispatched to
    # both render targets (minimap ok OverlayWindow / big-map InteractionWindow).
    # ------------------------------------------------------------------
    def compute_status_lines(self, player_pos, *, minimap, game_scale=None):
        """Build the left-bottom Status_Panel text lines for the current frame.

        Assembles the player coordinates, locked map id, map scale (pixels per
        1000 game units), mode (大地图/小地图 + 普通/路线) and tracking-target info
        into the fixed line order via :func:`format_status_lines`
        (Requirement 12.4). When phase-1 chest search is enabled in Normal_Mode,
        one additional line reports the selected chest and confirmation state.
        Called every frame from ``on_minimap`` / ``on_bigmap``
        and handed to the matching render target (Requirement 12.9).

        Field availability follows Requirements 12.10 / 12.11:

        - when the player's OCR coordinates are unavailable the coordinate field
          shows "未知" and the remaining fields keep their most recent valid
          values (Requirement 12.10);
        - when the map is not locked / the scale is unavailable those fields show
          "未知" (Requirement 12.11).

        面板开关（配置项 ``Show status panel``）关闭时直接返回空序列，两个渲染目标
        （小地图 ok 覆盖层 / 大地图交互窗口）都会因此跳过面板绘制。
        """
        if not self.task.config.get('Show status panel', True):
            return ()

        map_mode = '小地图' if minimap else '大地图'
        path_desc = '路线模式' if self._path_mode else '普通模式'
        mode_desc = f'{map_mode} / {path_desc}'

        map_id = self.task._locked_map_id
        scale_per_1000 = self._status_scale_per_1000(minimap, game_scale)
        target_info = self._compute_target_info(player_pos)

        if self._valid_player_pos(player_pos):
            # Player coordinates available -> reflect current values (an unlocked
            # map / unavailable scale renders "未知" via format_status_lines,
            # Requirement 12.11) and refresh the last-valid cache.
            if map_id is not None:
                self._status_last_map_id = map_id
            if scale_per_1000 is not None:
                self._status_last_scale = scale_per_1000
            self._status_last_mode = mode_desc
            self._status_last_target_info = target_info
            lines = format_status_lines(
                player_pos, map_id, scale_per_1000, mode_desc, target_info
            )
            if not self._path_mode and self.task.config.get('Chest search', False):
                lines.append(self._chest_status_info())
                route_line = self._chest_route_status_info()
                if route_line:
                    lines.append(route_line)
            return lines

        # Player coordinates unavailable -> coordinate "未知", keep the other
        # fields' last valid values (Requirement 12.10).
        last_mode = self._status_last_mode if self._status_last_mode is not None else mode_desc
        last_target = (
            self._status_last_target_info
            if self._status_last_target_info is not None
            else target_info
        )
        lines = format_status_lines(
            None, self._status_last_map_id, self._status_last_scale,
            last_mode, last_target,
        )
        if not self._path_mode and self.task.config.get('Chest search', False):
            lines.append(self._chest_status_info())
            route_line = self._chest_route_status_info()
            if route_line:
                lines.append(route_line)
        return lines

    def _status_scale_per_1000(self, minimap, game_scale):
        """Resolve the map scale (pixels per 1000 game units) for the panel.

        Uses the same metric each render path already relies on so the panel
        matches what is drawn: the minimap reuses
        :meth:`MapOverlayTask._minimap_scale_per_1000`; the big map derives it
        from ``game_scale`` (pixels per game unit -> ``1000 / game_scale``),
        falling back to the default when ``game_scale`` is unavailable (mirroring
        ``on_bigmap`` / ``_draw_overlay_screen_center``). Returns ``None`` only on
        a genuine failure so the panel shows "未知" (Requirement 12.11).
        """
        try:
            if minimap:
                scale = self.task._minimap_scale_per_1000()
            elif game_scale is not None and 0.01 < game_scale:
                scale = 1000.0 / game_scale
            else:
                scale = self.task._get_default_scale_per_1000()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"[Overlay] status scale unavailable: {exc}")
            return None
        if scale is None or scale <= 0:
            return None
        return scale

    def _compute_target_info(self, player_pos):
        """Build the tracking-target info string for the Status_Panel.

        Delegates the three-way formatting to :func:`format_target_info`
        (Requirements 12.5, 12.6, 12.7): "无" outside Path_Mode, "无目标" in
        Path_Mode without a Target, and the ``目标：段.. 第../..  距离..`` string
        when a Target is set. ``sectionIndex`` is the owning Section's 1-based
        position in ``route.sections``; ``nodeIndex`` is ``tracker.target.index +
        1`` (1-based); ``total`` is the Section's node count; the distance is the
        player-to-target distance in game units (player OCR coords x100).
        """
        if not self._path_mode:
            return format_target_info(False, None, 0, 0, 0, "", 0)
        tracker = self._tracker
        target = tracker.target if tracker is not None else None
        if target is None:
            return format_target_info(True, None, 0, 0, 0, "", 0)
        node = self._target_node()
        if node is None or self._route is None:
            # Target set but its node can't be resolved -> treat as no target.
            return format_target_info(True, None, 0, 0, 0, "", 0)
        section_index = None
        total = 0
        for idx, section in enumerate(self._route.sections, start=1):
            if section.section_id == target.section_id:
                section_index = idx
                total = len(section.nodes)
                break
        if section_index is None:
            return format_target_info(True, None, 0, 0, 0, "", 0)
        node_index = target.index + 1
        if self._valid_player_pos(player_pos):
            distance = player_target_distance(player_pos, node.x, node.y)
        else:
            distance = 0
        return format_target_info(
            True, target, section_index, node_index, total,
            node.position_name, distance,
        )

    # ------------------------------------------------------------------
    # Interaction window lifecycle (cross-thread safe)
    # ------------------------------------------------------------------
    def _client_geometry(self):
        """Return the game client area as logical ``(x, y, w, h)`` pixels.

        Mirrors ``ok`` ``OverlayWindow.update_overlay`` which divides by the
        window scaling. Falls back to the captured frame size when the hwnd
        geometry is unavailable.
        """
        hwnd = getattr(self.task, 'hwnd', None)
        if hwnd is not None:
            try:
                scaling = getattr(hwnd, 'scaling', 1) or 1
                x = getattr(hwnd, 'x', 0)
                y = getattr(hwnd, 'y', 0)
                w = getattr(hwnd, 'width', 0)
                h = getattr(hwnd, 'height', 0)
                if w and h:
                    return (int(x / scaling), int(y / scaling),
                            int(w / scaling), int(h / scaling))
            except Exception as exc:  # pragma: no cover - runtime specific
                logger.warning(f"[Overlay] hwnd geometry unavailable: {exc}")
        return 0, 0, self.task.screen_width, self.task.screen_height

    def _ensure_interaction_window(self):
        """Lazily create the InteractionOverlayWindow on the GUI thread.

        The window is a ``QWidget`` and must be constructed on the GUI thread,
        while ``run()`` executes on the ok task thread. Construction is therefore
        marshalled to the GUI thread (the same thread the ok OverlayWindow lives
        on) with a blocking queued invocation. Any failure marks the window
        permanently unavailable so the caller falls back gracefully (the full
        create-failure message is task 11.5).
        """
        if self._interaction_unavailable:
            return None
        if self._interaction_window is not None:
            return self._interaction_window
        try:
            window = self._create_interaction_window()
        except Exception as exc:
            logger.warning(f"[Overlay] interaction window create failed: {exc}")
            window = None
        if window is None:
            # Mark permanently unavailable and surface the one-time create
            # failure message; the big-map caller falls back to the ok
            # OverlayWindow rendering (Requirement 1.9).
            self._interaction_unavailable = True
            self._warn_interaction()
            return None
        self._interaction_window = window
        self._connect_window_signals(window)
        return window

    def _connect_window_signals(self, window) -> None:
        """Wire the window's mouse signals to controller actions, once.

        ``leftClicked`` / ``leftDoubleClicked`` / ``rightClicked`` /
        ``emptyClicked`` carry the hit draw-item index (aligned with
        ``_click_targets``) and are routed to the bubble / target / mark logic
        (task 11.3). Connections are made with the default (auto) connection
        type; the signals are emitted on the GUI thread where the slots then run.
        """
        if self._signals_connected:
            return
        try:
            window.leftClicked.connect(self.on_left_click)
            window.leftDoubleClicked.connect(self.on_double_click)
            window.rightClicked.connect(self.on_right_click)
            window.emptyClicked.connect(self.close_bubble)
        except Exception as exc:  # pragma: no cover - Qt runtime specific
            logger.warning(f"[Overlay] failed to connect window signals: {exc}")
            return
        self._signals_connected = True

    def _create_interaction_window(self):
        from PySide6.QtCore import QMetaObject, QObject, Qt, Slot
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return None

        geometry = self._client_geometry()

        class _Creator(QObject):
            def __init__(self):
                super().__init__()
                self.window = None
                self.error = None

            @Slot()
            def build(self):
                try:
                    from src.utils.InteractionOverlayWindow import (
                        InteractionOverlayWindow,
                    )
                    x, y, w, h = geometry
                    self.window = InteractionOverlayWindow(x, y, w, h)
                except Exception as exc:  # pragma: no cover - Qt runtime
                    self.error = exc

        creator = _Creator()
        gui_thread = app.thread()
        from PySide6.QtCore import QThread
        if QThread.currentThread() is gui_thread:
            creator.build()
        else:
            creator.moveToThread(gui_thread)
            QMetaObject.invokeMethod(creator, "build", Qt.BlockingQueuedConnection)
        if creator.error is not None:
            raise creator.error
        return creator.window

    def _hide_interaction_window(self) -> None:
        # Leaving the big map clears any interactive content + bubble state
        # (Requirements 1.8, 2.6: no bubbles outside Bigmap_Mode).
        self._bubble = None
        self._bubble_player_pos = None
        self._click_targets = []
        if self._interaction_window is not None:
            self._interaction_window.hide_overlay()

    def _draw_minimap_direction_window(self, edge_arrow, target_marker=None):
        """Render the selected chest direction through the Qt topmost layer."""
        if edge_arrow is None:
            self._hide_minimap_direction_window()
            return
        hwnd = getattr(self.task, 'hwnd', None)
        if hwnd is not None and not getattr(hwnd, 'visible', True):
            self._hide_minimap_direction_window()
            return
        window = self._ensure_minimap_direction_window()
        if window is None:
            return
        bearing, minimap_box = edge_arrow[0], edge_arrow[1]
        distance = edge_arrow[2] if len(edge_arrow) > 2 else None
        nearby = bool(edge_arrow[3]) if len(edge_arrow) > 3 else False
        window.render_direction(
            self._client_geometry(), bearing, minimap_box,
            distance=distance, target_marker=target_marker, nearby=nearby,
            hint_text=self._chest_hint_for_target(self._chest_target),
        )
        self._last_minimap_direction_at = time.monotonic()

    @staticmethod
    def _manual_movement_active():
        """Read common movement keys without installing another input hook."""
        if os.name != 'nt':
            return True
        try:
            import ctypes
            user32 = ctypes.windll.user32
            # W/A/S/D, Space, Shift and arrow keys cover the default movement
            # controls and common traversal modifiers. GetAsyncKeyState is a
            # read-only snapshot and does not generate or intercept input.
            keys = (0x57, 0x41, 0x53, 0x44, 0x20, 0x10,
                    0x25, 0x26, 0x27, 0x28)
            return any(user32.GetAsyncKeyState(key) & 0x8000 for key in keys)
        except Exception:
            # Preserve normal tracking if the platform query is unavailable.
            return True

    def _ensure_minimap_direction_window(self):
        if self._minimap_direction_unavailable:
            return None
        if self._minimap_direction_window is not None:
            return self._minimap_direction_window
        try:
            window = self._create_minimap_direction_window()
        except Exception as exc:
            logger.warning(f"[Overlay] minimap direction window create failed: {exc}")
            window = None
        if window is None:
            self._minimap_direction_unavailable = True
            return None
        self._minimap_direction_window = window
        logger.info("[MinimapDirectionQt] window created")
        return window

    def _create_minimap_direction_window(self):
        from PySide6.QtCore import QMetaObject, QObject, Qt, Slot
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return None

        class _Creator(QObject):
            def __init__(self):
                super().__init__()
                self.window = None
                self.error = None

            @Slot()
            def build(self):
                try:
                    from src.utils.MinimapDirectionWindow import (
                        MinimapDirectionWindow,
                    )
                    self.window = MinimapDirectionWindow()
                except Exception as exc:  # pragma: no cover - Qt runtime
                    self.error = exc

        creator = _Creator()
        gui_thread = app.thread()
        from PySide6.QtCore import QThread
        if QThread.currentThread() is gui_thread:
            creator.build()
        else:
            creator.moveToThread(gui_thread)
            QMetaObject.invokeMethod(creator, "build", Qt.BlockingQueuedConnection)
        if creator.error is not None:
            raise creator.error
        return creator.window

    def _hide_minimap_direction_window(self):
        if self._minimap_direction_window is not None:
            self._minimap_direction_window.hide_overlay()

    def close(self) -> None:
        """Release the interaction window, node icon cache and marks DB."""
        self._stop_hotkey()
        self._chest_confirm_requested = False
        self._chest_confirm_key_down = False
        self._display_toggle_requested = False
        self._display_toggle_key_down = False
        self._clear_chest_target()
        if self._icon_cache is not None:
            try:
                self._icon_cache.stop()
            except Exception:  # pragma: no cover - thread/runtime specific
                pass
            self._icon_cache = None
        if self._interaction_window is not None:
            try:
                self._interaction_window.hide_overlay()
            except Exception:  # pragma: no cover - Qt runtime
                pass
            self._interaction_window = None
        if self._minimap_direction_window is not None:
            try:
                self._minimap_direction_window.hide_overlay()
            except Exception:  # pragma: no cover - Qt runtime
                pass
            self._minimap_direction_window = None
        if self._marks_db is not None:
            try:
                self._marks_db.close()
            except Exception:  # pragma: no cover
                pass
            self._marks_db = None


class MapOverlayTask(TriggerTask, BaseWWTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "High-value items display"
        self.description = "Get player position and display nearby high value items"
        self.icon = FluentIcon.GLOBE
        self.default_config.update({
            # 覆盖层会持续重绘全屏透明窗口。地图功能默认关闭，只有用户主动启用后
            # 才创建覆盖层，避免普通自动化期间占用额外的 CPU/GDI 资源。
            '_enabled': False,
            'Detect interval (ms)': 100,
            '_Overlay enabled': True,
            '_Search radius (world units)': 10000,
            '_Auto map scale': True,
            '_Map scale (pixels per 1000 units)': 11.45,
            'World map overlay': True,
            # 下划线前缀 = 不在面板显示（保留可配置能力，默认全选四类物品）
            '_Item type filter': ['qzx_01', 'qzx_02', 'qzx_03', 'qzx_04'],
            '_Feature algorithm': 'SIFTGZ',
            # Path Mode 开关：Normal_Mode(False) <-> Path_Mode(True)（需求 4.1）
            'Path mode': False,
            # 到达阈值（游戏单位），默认 1000，有效范围 1..100000（需求 9.7、9.8）
            # 下划线前缀 = 不在面板显示
            '_Arrival threshold (game units)': 1000,
            # 视图输入探针：把鼠标拖动/滚轮与匹配结果配对写入 logs/view_probe/*.jsonl，
            # 供"渲染层自行响应拖动缩放（降低匹配频率）"方案做参数标定。默认关闭，
            # 只读不干预（pynput 非侵入监听），见 src/utils/ViewInputProbe.py
            '_View input probe': False,
            # 视图航位推算：用鼠标拖动/滚轮在两次匹配之间推算视图状态，匹配降级为
            # 纠偏。解决两个实测问题：匹配间隔造成的渲染滞后（中位 67px / p90 106px）
            # 与操作瞬态下的定位丢失（76~90% 的匹配失败集中在鼠标操作 0.5s 内）。
            # 匹配成功即吸附，最坏情况不差于关闭时。见 src/utils/ViewStateEstimator.py
            '_View dead reckoning': True,
            # 推进热键，pynput GlobalHotKeys 格式，默认 Ctrl+F9（需求 9.9）
            'Advance hotkey': '<ctrl>+<f9>',
            # 左下角信息面板（Status_Panel）开关，默认显示
            'Show status panel': True,
            # 阶段 1：自动选择最近未确认宝箱并显示导航提示；不自动移动或开箱。
            'Chest search': False,
            # 地图显示与最近目标搜寻共用这一组分类。
            'Collection types': list(DEFAULT_COLLECTION_GROUPS),
            # 本地领取记录档案；从下拉框选择游戏 UID 或自定义账号别名。
            'Collection account': 'default',
            # 仅用于把“添加账号”按钮排列在账号下拉框之后；按钮不会修改此值。
            'Collection account management': False,
            # 控制全部地图高价值物品覆盖层，按一次隐藏、再按一次显示。
            'Map display toggle hotkey': '<ctrl>+<f8>',
            # 使用 Win32 只读按键状态轮询，不注册新的全局钩子。
            'Chest confirm hotkey': '<ctrl>+<f10>',
            '_Chest confirm distance (world units)': CHEST_CONFIRM_DISTANCE_DEFAULT,
            CHEST_CONFIRM_POLL_MIGRATION_KEY: False,
            # 可选的视频路线清单；首个清单只验证来源/区域/数量，点位待校准。
            'Chest route file': CHEST_ROUTE_FILE_DEFAULT,
        })
        self.config_type['_Item type filter'] = {'type': 'multi_selection', 'options': [
            'qzx_01', 'qzx_02', 'qzx_03', 'qzx_04',
        ]}
        self.config_type['Collection types'] = {
            'type': 'multi_selection',
            'options': list(COLLECTION_GROUP_TYPE_IDS),
        }
        self.config_type['Collection account'] = {
            'type': 'drop_down',
            'options': ['default'],
        }
        self.config_type['_Feature algorithm'] = {'type': 'drop_down', 'options': [
            'SURF', 'SIFT', 'SIFTGZ',
        ]}
        # SpinBox 控件按此范围限制输入；越界值在 validate_config 中由
        # validate_threshold 再次校验并拒绝（需求 9.7、9.8）。
        self.config_type['_Arrival threshold (game units)'] = {
            'min': THRESHOLD_MIN, 'max': THRESHOLD_MAX,
        }
        # 资源下载按钮：点击后后台下载 assets.zip 并覆盖解压到 assets 目录。该键不写入
        # default_config，因此不会被持久化，仅作为面板上的一个操作按钮。
        self.config_type['Download map assets'] = {
            'type': 'button',
            'text': 'Download Map Assets',
            'icon': FluentIcon.CLOUD_DOWNLOAD,
            'callback': self.start_assets_download,
        }
        self.config_type['Collection account management'] = {
            'type': 'button',
            'text': 'Add account',
            'icon': FluentIcon.ADD,
            'callback': self.add_collection_account,
        }
        # 面板可见文案统一用英文源串，由 i18n/<locale>/LC_MESSAGES/ok.po 提供翻译。
        self.config_description = {
            'Path mode': 'Show only the route from assets/path.json, hide high-value items',
            'Advance hotkey': 'Hotkey to advance the target manually (pynput format, e.g. <ctrl>+<f9>)',
            'Show status panel': 'Show the info panel at the bottom-left corner of the game window',
            'Download map assets': 'Download the latest map assets and extract them into the assets folder',
            'Chest search': 'Select the nearest unconfirmed collectible and show its direction; movement and collection remain manual',
            'Collection types': 'Choose which collectible groups are displayed and used for nearest-target guidance',
            'Collection account': 'Choose the local completion profile used to store collected-item progress',
            'Collection account management': 'Add a game UID or custom alias and switch to the new profile',
            'Map display toggle hotkey': 'Hotkey to show or hide all map high-value-item overlays (pynput format, e.g. <ctrl>+<f8>)',
            'Chest confirm hotkey': 'Press after manually collecting the selected target to mark it as completed',
            '_Chest confirm distance (world units)': 'Only allow confirmation when the player is close to the selected chest',
            'Chest route file': 'Optional video-route manifest; metadata-only routes do not change movement or chest selection',
        }
        self.config_type['_Chest confirm distance (world units)'] = {
            'min': 1, 'max': 100000,
        }
        self._window = None
        self._last_valid = None
        self._consecutive_far = 0
        self._overlay = None
        self._overlay_registered = False
        # Low-rate minimap render diagnostics.  This makes it possible to
        # distinguish "no nearby database items" from a native overlay that
        # failed to present, without logging every 100 ms frame.
        self._minimap_draw_log_counter = 0
        self._fallback_position_log_counter = 0
        self._overlay_controller = None
        self._fallback_failures = 0
        self._locked_map_id = None
        self._last_match_scale = None
        self._engines = {}
        self._coords_dict = None
        self._engine_settings = None
        self._in_team_failures = 0
        self._minimap_match_counter = 0
        self._recheck_counter = 0
        self._minimap_match_scale = None
        # 问题4：小地图缩放缓存（pixels per 1000 game units）。地图锁定态在
        # “匹配缩放 <-> 默认缩放”间瞬断会导致远处路线节点随缩放剧烈抖动。缓存最近一次
        # 基于匹配算得的缩放；当前无匹配缩放但已有缓存时返回缓存值，避免瞬断跳变。
        # 仅在从未获得过匹配缩放时才回退默认值。
        self._last_minimap_scale_per_1000 = None
        self._lock_ocr_pos = None
        self._second_check_done = False
        self._fallback_log_counter = 0
        # 视图输入探针：仅在 '_View input probe' 开启时创建，记录鼠标动作与匹配结果
        # 的配对数据，供"渲染层航位推算"方案标定用。默认 None，不影响任何现有逻辑。
        self._view_probe = None
        self._view_probe_in_big_map = False
        # 视图状态推算（航位推算）：鼠标拖动/滚轮驱动视图状态，匹配降级为纠偏。
        # 与探针共用同一个全局鼠标监听（MouseWatcher）。
        self._mouse_watcher = None
        self._view_est = None
        self._ocr_noresult_counter = 0
        self._position_detector = None
        # 资源下载：后台线程 + 与检测循环的挂起握手。
        # ``_assets_suspend``：下载线程置位，要求检测循环释放对资源文件的占用；
        # ``_assets_released``：检测循环释放完成后置位，下载线程据此开始写文件。
        self._assets_lock = threading.Lock()
        self._assets_thread = None
        self._assets_suspend = threading.Event()
        self._assets_released = threading.Event()
        # 资源下载的 GUI 助手（进度弹窗 + 手动下载提示），归属 GUI 线程，惰性创建。
        self._assets_gui = None
        self._assets_gui_unavailable = False
        # 启用附加功能后的一次性检查（展示版本号 + 资源缺失时自动下载）是否已做过。
        self._feature_started = False

    def on_create(self):
        super().on_create()
        # ``use_overlay`` 会持久化在 configs/_ok.json；旧版本可能留下 True。
        # 以地图任务的实际启用状态为准同步一次，确保普通自动化启动时不会创建覆盖层。
        self._set_global_overlay_enabled(self.enabled)
        self._migrate_chest_confirm_config()
        # Convert the former free-text value into a durable profile, then build
        # the dropdown before the task card is created.  This preserves custom
        # aliases that users entered in earlier builds.
        self._refresh_collection_account_options(
            self.config.get('Collection account', 'default'),
            ensure=True,
        )
        # 程序启动后模式总是重置回普通模式，避免上次退出时残留在路线模式。
        if self.config.get('Path mode', False):
            self.config['Path mode'] = False
        # 用户点“停止”只会让执行器暂停（不会走 disable/on_destroy），run() 随之停摆，
        # 交互窗口若不主动隐藏就会残留在游戏画面上，因此在这里挂一次暂停回调。
        try:
            from ok.gui.Communicate import communicate

            communicate.executor_paused.connect(self._on_executor_paused)
        except Exception as e:  # pragma: no cover - Qt 运行时相关
            logger.warning(f'[Overlay] connect executor_paused failed: {e}')

    def _migrate_chest_confirm_config(self):
        """One-time migration from the old hook and 250-unit distance guard."""
        if self.config.get(CHEST_CONFIRM_POLL_MIGRATION_KEY, False):
            return
        if not str(self.config.get('Chest confirm hotkey', '') or '').strip():
            self.config['Chest confirm hotkey'] = '<ctrl>+<f10>'
        try:
            old_limit = float(self.config.get(
                '_Chest confirm distance (world units)', 250
            ))
        except (TypeError, ValueError):
            old_limit = 250
        if old_limit <= 250:
            self.config['_Chest confirm distance (world units)'] = (
                CHEST_CONFIRM_DISTANCE_DEFAULT
            )
        self.config[CHEST_CONFIRM_POLL_MIGRATION_KEY] = True
        logger.info(
            f"[ChestConfirm] migrated hotkey="
            f"{self.config.get('Chest confirm hotkey')!r} distance="
            f"{self.config.get('_Chest confirm distance (world units)')}"
        )

    def _refresh_collection_account_options(self, selected=None, ensure=False):
        """Reload the persisted profile list used by the account dropdown."""
        from src.utils.MapMarksDB import (
            DEFAULT_ACCOUNT_ID, MapMarksDB, normalize_account_id,
        )

        selected = normalize_account_id(selected)
        accounts = [DEFAULT_ACCOUNT_ID]
        db = None
        try:
            os.makedirs(os.path.dirname(MARKS_DB_PATH), exist_ok=True)
            db = MapMarksDB(MARKS_DB_PATH)
            if ensure:
                db.ensure_account(selected)
            accounts = db.list_accounts()
        except Exception as exc:
            logger.warning(f'[Overlay] collection account list failed: {exc}')
        finally:
            if db is not None:
                db.close()

        if selected not in accounts:
            accounts.append(selected)
        self.config_type['Collection account']['options'] = accounts
        return accounts

    def add_collection_account(self, *args):
        """Prompt for a profile, persist it, select it, and rebuild the card."""
        from src.utils.CollectionAccountDialog import prompt_collection_account

        parent = getattr(og, 'main_window', None)
        account_id = prompt_collection_account(parent)
        if not account_id:
            return

        self._refresh_collection_account_options(account_id, ensure=True)
        self.config['Collection account'] = account_id
        self.info_set('Collection account', account_id)

        # Task cards copy dropdown options when they are built.  Rebuild on the
        # next GUI turn so the new option is visible immediately and the button
        # that invoked this callback is not deleted while its signal runs.
        try:
            from PySide6.QtCore import QTimer
            from ok.core.events import communicate

            QTimer.singleShot(0, communicate.task_list_updated.emit)
        except Exception as exc:  # pragma: no cover - headless compatibility
            logger.warning(f'[Overlay] refresh account dropdown failed: {exc}')

    def _on_executor_paused(self, paused):
        """暂停时关闭覆盖层；恢复且地图任务启用时再创建。"""
        if paused:
            self._force_clear_overlay()
            self._set_global_overlay_enabled(False)
        elif self.enabled:
            self._set_global_overlay_enabled(True)

    @staticmethod
    def _set_global_overlay_enabled(enabled):
        """让全局识别框/覆盖层只跟随地图任务生命周期启用。"""
        app = getattr(og, 'app', None)
        setter = getattr(app, 'set_overlay_setting', None)
        if not callable(setter):
            return
        try:
            setter('boxes', bool(enabled))
        except Exception as e:  # pragma: no cover - Qt 运行时相关
            logger.warning(f'[Overlay] set global overlay enabled={enabled} failed: {e}')

    def enable(self):
        super().enable()
        self._set_global_overlay_enabled(True)
        # enable() 会 info_clear()，因此版本号等信息在其之后重新写入。
        self._ensure_feature_started()

    def disable(self):
        super().disable()
        # 下次启用时重新检查资源与版本号。
        self._feature_started = False
        # 停用附加功能时强制清一遍附加层，避免最后一帧残留在游戏画面上。
        self._force_clear_overlay()
        self._set_global_overlay_enabled(False)

    def validate_config(self, key, value):
        """校验配置项；返回非空消息表示拒绝该值。

        框架的 ``Config.__setitem__`` 仅在校验通过时写入新值，因此返回错误消息会
        让到达阈值保留上一个有效值（需求 9.8）。这里复用纯函数
        ``validate_threshold`` 做范围判定（1..100000 游戏单位），并经 ``info_set``
        额外提示一条配置无效信息。
        """
        if key == '_Arrival threshold (game units)':
            if not validate_threshold(value):
                message = (f"到达阈值无效：{value!r}，须为 "
                           f"{THRESHOLD_MIN}..{THRESHOLD_MAX} 游戏单位，已保留上一有效值")
                self.info_set('Config error', message)
                return message
        return None

    # ------------------------------------------------------------------
    # 资源包（assets.zip）下载 / 覆盖解压
    # ------------------------------------------------------------------
    def _ensure_feature_started(self):
        """启用附加功能后的一次性处理：展示资源版本号，缺资源时自动下载。

        版本号取自资源包内的 ``assets/stitched/version.txt``；当特征文件、物品数据库
        等关键资源缺失时，等价于自动点击一次“下载资源”按钮。
        """
        if self._feature_started:
            return
        self._feature_started = True
        self.info_set(ASSETS_VERSION_INFO_KEY, read_asset_version(ASSETS_DIR) or '未知')
        missing = missing_required_assets(ASSETS_DIR)
        if missing:
            logger.warning(f'[MapAssets] missing resources: {missing}')
            self.info_set(ASSETS_INFO_KEY,
                          f'资源缺失（{", ".join(missing)}），开始自动下载')
            self.start_assets_download()

    def start_assets_download(self, *args):
        """下载资源按钮的回调（GUI 线程）：在后台线程下载并覆盖解压资源包。

        ``*args`` 吸收 Qt ``clicked`` 信号可能带的 checked 参数。防重复：线程句柄的
        创建与 ``is_alive()`` 判定都在 ``_assets_lock`` 内完成，重复点击（或点击时自动
        下载已在跑）只会提示“已在进行中”，同一时间最多一个下载任务。进度与解压状态通过
        :meth:`_assets_progress` 写入面板。
        """
        with self._assets_lock:
            if self._assets_thread is not None and self._assets_thread.is_alive():
                self.info_set(ASSETS_INFO_KEY, '资源下载已在进行中')
                return
            thread = threading.Thread(
                target=self._download_assets, name='MapAssetsDownload', daemon=True
            )
            self._assets_thread = thread
            self.info_set(ASSETS_INFO_KEY, '准备下载资源包')
            # 唤出带进度条的弹窗（GUI 线程执行；无 GUI 时静默跳过，只走面板状态）。
            helper = self._ensure_assets_gui()
            if helper is not None:
                helper.started.emit()
            thread.start()

    def _download_assets(self):
        """后台线程：挂起检测循环 -> 下载 -> 覆盖解压 -> 恢复检测循环。

        解压会覆盖 ``map_items.db`` / ``*_siftgz.npz`` 等正在被使用的文件，因此先请求
        检测循环释放这些句柄（见 :meth:`_release_map_resources`）；检测循环未运行时
        等待超时后直接继续，此时文件本就没有被占用。进度、解压状态与最终结果通过
        :class:`~src.utils.AssetsProgressDialog.AssetsGuiHelper` 的信号同步到进度弹窗。
        """
        self._assets_suspend.set()
        self._assets_released.wait(ASSETS_SUSPEND_WAIT)
        helper = self._assets_gui
        try:
            version = download_and_extract(
                ASSETS_DIR, progress=self._assets_progress
            )
            self.info_set(ASSETS_VERSION_INFO_KEY, version or '未知')
            if helper is not None:
                helper.finished.emit(f'资源更新完成，版本 {version or "未知"}', True)
        except Exception as e:
            # 不做自动重试：弹窗提示用户手动下载并解压（需求：失败即提示）。
            logger.error(f'[MapAssets] download failed: {e}')
            self.info_set(ASSETS_INFO_KEY, f'资源下载失败：{e}')
            if helper is not None:
                helper.finished.emit(f'资源下载失败：{e}', False)
            self._alert_manual_download(e)
        finally:
            self._assets_released.clear()
            self._assets_suspend.clear()

    def _assets_progress(self, stage, message, percent=-1.0):
        """把下载进度 / 解压状态写到面板（``info_set``）并同步到进度弹窗。"""
        self.info_set(ASSETS_INFO_KEY, message)
        if stage in ('done', 'extract'):
            logger.info(f'[MapAssets] {message}')
        helper = self._assets_gui
        if helper is not None:
            helper.progress.emit(message, float(percent))

    def _alert_manual_download(self, error):
        """下载失败时弹窗提示手动下载：给出下载链接与要解压到的目录。

        文案经 :meth:`tr` 走 gettext 翻译；弹窗必须在 GUI 线程弹出，因此通过
        :class:`~src.utils.AssetsProgressDialog.AssetsGuiHelper` 的 ``alert`` 信号投递。
        """
        title = self.tr('Map assets download failed')
        template = self.tr(
            'Failed to download map assets: {error}\n\n'
            'Please download it manually:\n{url}\n\n'
            'Then extract it into this folder (overwrite):\n{path}'
        )
        try:
            message = template.format(error=error, url=ASSETS_URL, path=ASSETS_DIR)
        except (KeyError, IndexError):  # pragma: no cover - 翻译占位符错误时的兜底
            message = (f'Failed to download map assets: {error}\n\n{ASSETS_URL}\n\n'
                       f'{ASSETS_DIR}')
        helper = self._ensure_assets_gui()
        if helper is None:
            logger.warning('[MapAssets] no GUI available for manual download alert')
            return
        helper.alert.emit(title, message)

    def _ensure_assets_gui(self):
        """惰性创建资源下载的 GUI 助手（归属 GUI 线程），无 Qt 时返回 ``None``。"""
        if self._assets_gui is not None:
            return self._assets_gui
        if self._assets_gui_unavailable:
            return None
        try:
            from src.utils.AssetsProgressDialog import create_gui_helper

            helper = create_gui_helper()
        except Exception as e:  # pragma: no cover - Qt 运行时相关
            logger.warning(f'[MapAssets] create gui helper failed: {e}')
            helper = None
        if helper is None:
            self._assets_gui_unavailable = True
            return None
        self._assets_gui = helper
        return helper

    def _release_map_resources(self):
        """释放对资源文件的占用，供覆盖解压使用。

        关闭覆盖层控制器（含标记数据库、节点图标缓存、交互窗口）与物品数据库连接，
        丢弃已加载的特征引擎与坐标缓存，并解除地图锁定；解压完成后这些对象都会在下一
        帧按需重新创建，从而用上新资源。
        """
        self._clear_overlay()
        if self._overlay_controller is not None:
            try:
                self._overlay_controller.close()
            except Exception as e:  # pragma: no cover - 运行时相关
                logger.warning(f'[MapAssets] close overlay controller failed: {e}')
            self._overlay_controller = None
        if self._overlay is not None:
            try:
                self._overlay.close()
            except Exception as e:  # pragma: no cover - 运行时相关
                logger.warning(f'[MapAssets] close overlay db failed: {e}')
            self._overlay = None
        self._engines.clear()
        self._coords_dict = None
        self._engine_settings = None
        self._locked_map_id = None
        self._minimap_match_scale = None
        self._last_minimap_scale_per_1000 = None
        self._last_match_scale = None
        self._lock_ocr_pos = None
        self._second_check_done = False

    def _get_position_detector(self):
        if self._position_detector is None:
            from src.utils.positionDetector import PositionDetector
            self._position_detector = PositionDetector()
        return self._position_detector

    def _detections_per_second(self):
        interval = self.config.get('Detect interval (ms)')
        return math.ceil(1000 / interval)

    def _window_size(self):
        return max(5, self._detections_per_second())

    def _teleport_confirm_count(self):
        return math.ceil(self._detections_per_second() / 4) + 1

    def _ensure_window(self):
        size = self._window_size()
        if self._window is None or self._window.maxlen != size:
            old = list(self._window) if self._window else []
            self._window = deque(old[-size:], maxlen=size)

    def _check_teleport(self, pos):
        if self._last_valid is None:
            return False
        if _dist2d(pos, self._last_valid) > TELEPORT_THRESHOLD:
            self._consecutive_far += 1
        else:
            self._consecutive_far = 0
            return False

        if self._consecutive_far <= self._teleport_confirm_count():
            return False

        positions = list(self._window)
        if len(positions) >= 2 and _dist2d(positions[-1], positions[-2]) < TELEPORT_SETTLE_DISTANCE:
            return True
        return False

    def _filter_outliers(self):
        if len(self._window) < 3:
            return list(self._window)

        positions = list(self._window)
        xs = [p[0] for p in positions]
        ys = [p[1] for p in positions]
        cx = sorted(xs)[len(xs) // 2]
        cy = sorted(ys)[len(ys) // 2]
        center = (cx, cy)

        distances = [_dist2d(p, center) for p in positions]
        sorted_dists = sorted(distances)
        median_dist = sorted_dists[len(sorted_dists) // 2]
        threshold = max(median_dist * OUTLIER_RELATIVE_FACTOR, OUTLIER_FIXED_THRESHOLD)

        filtered = [p for p, d in zip(positions, distances) if d <= threshold]
        return filtered if filtered else [positions[-1]]

    def _denoise(self, position_text):
        pos = _parse_position(position_text)
        if pos is None:
            return self._last_valid

        self._ensure_window()
        self._window.append(pos)

        if self._last_valid is None:
            self._last_valid = pos
            return pos

        if self._check_teleport(pos):
            self._window.clear()
            self._window.append(pos)
            self._last_valid = pos
            self._consecutive_far = 0
            if self._locked_map_id is not None:
                logger.info(f"[MapFallback] teleport detected, unlocking map_id={self._locked_map_id}")
                self._locked_map_id = None
                self._minimap_match_scale = None
                self._lock_ocr_pos = None
                self._second_check_done = False
            return pos

        if self._consecutive_far > 0:
            return self._last_valid

        filtered = self._filter_outliers()
        self._last_valid = filtered[-1]
        return self._last_valid

    def _ensure_coords(self):
        if self._coords_dict is not None:
            return
        self._coords_dict = _load_coords_dict(MAP_DIR)
        logger.info(f"[MapFallback] loaded {len(self._coords_dict)} coords from {MAP_DIR}: {list(self._coords_dict.keys())}")

    def _load_engine_settings(self):
        if self._engine_settings is not None:
            return self._engine_settings
        settings_path = os.path.join(MAP_DIR, 'setting.json')
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                self._engine_settings = json.load(f)
        except Exception as e:
            logger.warning(f"[MapFallback] failed to load setting.json: {e}")
            self._engine_settings = {}
        return self._engine_settings

    def _ensure_mouse_watcher(self):
        """全局鼠标监听（探针与视图推算共用一个监听线程）。"""
        if self._mouse_watcher is None:
            try:
                from src.utils.MouseWatcher import MouseWatcher
                self._mouse_watcher = MouseWatcher()
            except Exception as e:
                logger.warning(f"[MouseWatcher] init failed: {e}")
        return self._mouse_watcher

    def _ensure_view_probe(self):
        """按配置惰性创建/销毁视图输入探针，返回探针或 None。"""
        want = bool(self.config.get('_View input probe'))
        if not want:
            if self._view_probe is not None:
                try:
                    self._view_probe.stop()
                except Exception as e:
                    logger.warning(f"[ViewProbe] stop failed: {e}")
                self._view_probe = None
                self._view_probe_in_big_map = False
            return None
        if self._view_probe is None:
            try:
                from src.utils.ViewInputProbe import ViewInputProbe
                probe = ViewInputProbe(VIEW_PROBE_DIR)
                frame = self.frame
                extra = {'algo': self.config.get('_Feature algorithm', 'SIFTGZ'),
                         'interval_ms': self.config.get('Detect interval (ms)', 500)}
                if frame is not None:
                    extra['frame'] = [int(frame.shape[1]), int(frame.shape[0])]
                if probe.start(extra, watcher=self._ensure_mouse_watcher()):
                    self._view_probe = probe
            except Exception as e:
                logger.warning(f"[ViewProbe] init failed: {e}")
                self._view_probe = None
        return self._view_probe

    def _ensure_view_estimator(self):
        """按配置惰性创建/销毁视图状态推算器，返回推算器或 None。"""
        want = bool(self.config.get('_View dead reckoning'))
        if not want:
            if self._view_est is not None:
                watcher = self._mouse_watcher
                if watcher is not None:
                    try:
                        watcher.unsubscribe(self._view_est)
                    except Exception as e:
                        logger.warning(f"[ViewEst] unsubscribe failed: {e}")
                logger.info(f"[ViewEst] 关闭，{self._view_est.summary()}")
                self._view_est = None
            return None
        if self._view_est is None:
            try:
                from src.utils.ViewStateEstimator import ViewStateEstimator
                est = ViewStateEstimator()
                watcher = self._ensure_mouse_watcher()
                if watcher is None:
                    return None
                watcher.subscribe(est)
                if watcher.available is False:
                    watcher.unsubscribe(est)
                    logger.warning('[ViewEst] 鼠标监听不可用，视图推算不生效')
                    return None
                self._view_est = est
                logger.info('[ViewEst] 视图推算已启用')
            except Exception as e:
                logger.warning(f"[ViewEst] init failed: {e}")
                self._view_est = None
        return self._view_est

    def _view_est_sync_screen_center(self, est):
        """把游戏窗口客户区中心的屏幕绝对坐标同步给推算器（缩放锚点要用）。"""
        if est is None or self.frame is None:
            return
        h, w = self.frame.shape[:2]
        try:
            sx, sy = self.executor.interaction.capture.get_abs_cords(w // 2, h // 2)
            est.set_screen_center(sx, sy)
        except Exception as e:
            logger.warning(f"[ViewEst] 获取窗口中心失败，缩放锚点将不补偿: {e}")

    def _view_probe_mark_big_map(self, on):
        """大地图进入/离开的边沿事件，用于切分采集会话段。"""
        probe = self._view_probe
        if probe is None or on == self._view_probe_in_big_map:
            return
        self._view_probe_in_big_map = on
        probe.log_bigmap(on)

    #: 纯匹配期参数：改了**不需要**重新生成 npz 缓存，因此不能由资产包里的
    #: ``setting.json`` 决定——否则旧资产包一覆盖，代码里的调优结果就被冲掉
    #: （实机上就发生过：ratio 调到 0.60 后被资源包的 ``_r75_`` 覆盖回 0.75）。
    #: 这里把它们从资产包参数里剔除，交给引擎的代码默认值。
    #: 其余参数（ct/ol/sigma/et/grid/mpc/ds/ts/md/upscale…）必须与 npz 绑定，仍取资产包的值。
    MATCH_TIME_PARAMS = ('ratio', 'max_dist')

    def _resolve_engine_kwargs(self, algo, map_id):
        from src.match_engine.params import ParamSet, params_to_engine_kwargs

        settings = self._load_engine_settings()
        algo_key = algo.lower()
        algo_cfg = settings.get(algo_key, {})

        param_name = None
        maps_cfg = algo_cfg.get('maps', {})
        if map_id in maps_cfg:
            param_name = maps_cfg[map_id]
        elif 'default' in algo_cfg:
            param_name = algo_cfg['default']

        if param_name:
            try:
                ps = ParamSet.from_name(param_name)
                kwargs = params_to_engine_kwargs(ps)
                dropped = {k: kwargs.pop(k) for k in self.MATCH_TIME_PARAMS
                           if k in kwargs}
                logger.info(f"[MapFallback] setting.json resolved {algo_key}/{map_id} → {param_name}"
                            + (f"（忽略资产包里的匹配期参数 {dropped}，改用代码默认值）"
                               if dropped else ""))
                return kwargs
            except Exception as e:
                logger.warning(f"[MapFallback] failed to parse param name {param_name!r}: {e}")

        return None

    def _get_engine(self, map_id):
        if map_id in self._engines:
            return self._engines[map_id]

        algo = self.config.get('_Feature algorithm', 'SIFTGZ').upper()
        algo_lower = algo.lower()
        npz_path = os.path.join(MAP_DIR, f"{map_id}_{algo_lower}.npz")
        map_path = os.path.join(MAP_DIR, f"{map_id}.png")

        if not os.path.exists(npz_path) and not os.path.exists(map_path):
            logger.warning(f"[MapFallback] no npz cache or png for map {map_id}")
            return None

        kwargs = self._resolve_engine_kwargs(algo, map_id) or {}
        dummy_coords = os.path.join(MAP_DIR, f"__no_coords_{map_id}")

        if algo == 'SIFT':
            from src.match_engine import SiftEngine
            engine = SiftEngine(map_id=map_id, map_path=map_path,
                                assets_dir=MAP_DIR,
                                coords_path=dummy_coords, **kwargs)
        elif algo == 'SIFTGZ':
            from src.match_engine import SiftGzEngine
            engine = SiftGzEngine(map_id=map_id, map_path=map_path,
                                  assets_dir=MAP_DIR,
                                  coords_path=dummy_coords, **kwargs)
        else:
            from src.match_engine import SurfEngine
            engine = SurfEngine(map_id=map_id, map_path=map_path,
                                assets_dir=MAP_DIR,
                                coords_path=dummy_coords, **kwargs)

        self._attach_coords(engine, map_id)
        self._engines[map_id] = engine
        return engine

    def _attach_coords(self, engine, map_id):
        from src.match_engine.common import CoordsRef
        self._ensure_coords()
        d = self._coords_dict.get(map_id)
        if d is None:
            return
        try:
            engine.coords = CoordsRef(
                offset=(float(d['offset'][0]), float(d['offset'][1])),
                scale=(float(d['scale'][0]), float(d['scale'][1])),
                min_xy=(float(d.get('min', [0, 0])[0]), float(d.get('min', [0, 0])[1])),
                max_xy=(float(d.get('max', [0, 0])[0]), float(d.get('max', [0, 0])[1])),
            )
        except Exception as e:
            logger.warning(f"[MapFallback] failed to load coords for map {map_id}: {e}")

    def _try_map_match(self, player_pos, full_map=False, crop_size=FRAME_CROP_SIZE):
        self._ensure_coords()
        if not self._coords_dict:
            logger.warning("[MapFallback] no coords dict loaded")
            return None

        game_x = player_pos[0] * 100
        game_y = player_pos[1] * 100

        if full_map and self._locked_map_id is None:
            candidates = list(self._coords_dict.items())
        elif self._locked_map_id is not None:
            candidates = [(self._locked_map_id, self._coords_dict[self._locked_map_id])]
        else:
            candidates = _filter_candidate_maps(player_pos, self._coords_dict)
            if not candidates:
                logger.warning(f"[MapFallback] no candidate maps for game=({game_x},{game_y})")
                return None
            candidates.sort(key=lambda c: c[0] in SLOW_MAP_IDS)

        self._fallback_log_counter += 1
        verbose = self._fallback_log_counter <= 3 or self._fallback_log_counter % 20 == 0
        if verbose:
            logger.info(
                f"[MapFallback] OCR=({player_pos[0]},{player_pos[1]}) "
                f"{'full-map' if full_map else 'region'} "
                f"candidates={[c[0] for c in candidates]}"
            )

        h, w = self.frame.shape[:2]
        cx, cy = w // 2, h // 2
        half = crop_size // 2
        cropped = self.frame[cy - half:cy + half, cx - half:cx + half]

        algo = self.config.get('_Feature algorithm', 'SIFTGZ')

        for map_id, coords_d in candidates:
            try:
                engine = self._get_engine(map_id)
            except Exception as e:
                logger.warning(f"[MapFallback] failed to init engine for map {map_id}: {e}")
                continue

            if full_map:
                region = None
                region_str = "full-map"
            else:
                scale = coords_d['scale'][0]
                offset_x = coords_d['offset'][0]
                offset_y = coords_d['offset'][1]
                pixel_x = (game_x - offset_x) / scale
                pixel_y = (game_y - offset_y) / scale

                region_size = _map_region_size(coords_d, self._last_match_scale)
                half_r = region_size // 2
                region = (int(pixel_x - half_r), int(pixel_y - half_r), region_size, region_size)
                region_str = f"region=({region[0]},{region[1]},{region[2]}x{region[3]})"

            try:
                result = engine.match_array(cropped, region=region, crop_size=0)
            except Exception as e:
                logger.warning(f"[MapFallback] match error map={map_id}: {e}")
                continue

            if result.success and result.confidence >= CONFIDENCE_THRESHOLD and result.match_count >= 10:
                gc = result.game_center
                gc_str = f"({gc[0]:.0f},{gc[1]:.0f})" if gc else "None"
                center_str = f"({result.center[0]:.0f},{result.center[1]:.0f})" if result.center else "None"
                if self._locked_map_id is None:
                    self._locked_map_id = map_id
                logger.info(
                    f"[MapFallback] map={map_id} {region_str} algo={algo} "
                    f"matches={result.match_count} inliers={result.inlier_count} "
                    f"conf={result.confidence:.3f} scale={result.map_scale:.4f} "
                    f"center={center_str} game={gc_str} "
                    f"elapsed={result.elapsed_ms:.0f}ms"
                )
                return result

            if verbose:
                logger.info(
                    f"[MapFallback] map={map_id} {region_str} algo={algo} "
                    f"matches={result.match_count} inliers={result.inlier_count} "
                    f"conf={result.confidence:.3f} "
                    f"{'rejected (conf<%.1f or matches<10)' % CONFIDENCE_THRESHOLD if not result.success else 'rejected'} "
                    f"elapsed={result.elapsed_ms:.0f}ms"
                )

        logger.warning("[MapFallback] all candidates tried, none with sufficient confidence")
        return None

    def _is_minimap_dark(self):
        minimap_box = self.get_box_by_name('box_minimap')
        if minimap_box is None:
            return True
        x, y, w, h = minimap_box.x, minimap_box.y, minimap_box.width, minimap_box.height
        if x < 0 or y < 0 or x + w > self.frame.shape[1] or y + h > self.frame.shape[0]:
            return True
        minimap_img = self.frame[y:y + h, x:x + w]
        gray = cv2.cvtColor(minimap_img, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray)) < 60

    def _match_minimap_map_id(self, player_pos):
        minimap_box = self.get_box_by_name('box_minimap')
        if minimap_box is None:
            return None

        x, y, w, h = minimap_box.x, minimap_box.y, minimap_box.width, minimap_box.height
        if x < 0 or y < 0 or x + w > self.frame.shape[1] or y + h > self.frame.shape[0]:
            return None

        minimap_img = self.frame[y:y + h, x:x + w]
        center = (w // 2, h // 2)
        radius = min(w, h) // 2
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(mask, center, radius, 255, -1)
        masked = cv2.bitwise_and(minimap_img, minimap_img, mask=mask)
        gray = cv2.cvtColor(masked, cv2.COLOR_BGR2GRAY)

        self._ensure_coords()
        if not self._coords_dict:
            return None

        candidates = _filter_candidate_maps(player_pos, self._coords_dict)
        if not candidates:
            return None

        matched = []
        for map_id, coords_d in candidates:
            engine = self._get_engine(map_id)
            if engine is None:
                continue

            game_x = player_pos[0] * 100
            game_y = player_pos[1] * 100
            scale = coords_d['scale'][0]
            offset_x = coords_d['offset'][0]
            offset_y = coords_d['offset'][1]
            pixel_x = (game_x - offset_x) / scale
            pixel_y = (game_y - offset_y) / scale

            region_size = _map_region_size(coords_d, self._minimap_match_scale)
            half_r = region_size // 2
            region = (int(pixel_x - half_r), int(pixel_y - half_r), region_size, region_size)

            try:
                result = engine.match_array(gray, region=region, crop_size=0, constrained=True)
            except Exception as e:
                logger.warning(f"[MinimapMatch] match error for map {map_id}: {e}")
                continue

            logger.info(
                f"[MinimapMatch] map={map_id} conf={result.confidence:.3f} "
                f"matches={result.match_count} inliers={result.inlier_count}"
            )

            if result.success and result.confidence >= CONFIDENCE_THRESHOLD and result.match_count >= 10:
                matched.append((map_id, result.map_scale))

        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            logger.info(f"[MinimapMatch] ambiguous: {len(matched)} maps matched {[m[0] for m in matched]}")
        return None

    def _verify_locked_map(self, player_pos):
        if self._locked_map_id is None:
            return False

        minimap_box = self.get_box_by_name('box_minimap')
        if minimap_box is None:
            return False

        x, y, w, h = minimap_box.x, minimap_box.y, minimap_box.width, minimap_box.height
        if x < 0 or y < 0 or x + w > self.frame.shape[1] or y + h > self.frame.shape[0]:
            return False

        minimap_img = self.frame[y:y + h, x:x + w]
        center = (w // 2, h // 2)
        radius = min(w, h) // 2
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(mask, center, radius, 255, -1)
        masked = cv2.bitwise_and(minimap_img, minimap_img, mask=mask)
        gray = cv2.cvtColor(masked, cv2.COLOR_BGR2GRAY)

        self._ensure_coords()
        if not self._coords_dict:
            return False

        coords_d = self._coords_dict.get(self._locked_map_id)
        if coords_d is None:
            return False

        engine = self._get_engine(self._locked_map_id)
        if engine is None:
            return False

        game_x = player_pos[0] * 100
        game_y = player_pos[1] * 100
        scale = coords_d['scale'][0]
        offset_x = coords_d['offset'][0]
        offset_y = coords_d['offset'][1]
        pixel_x = (game_x - offset_x) / scale
        pixel_y = (game_y - offset_y) / scale

        region_size = _map_region_size(coords_d, self._minimap_match_scale)
        half_r = region_size // 2
        region = (int(pixel_x - half_r), int(pixel_y - half_r), region_size, region_size)

        try:
            result = engine.match_array(gray, region=region, crop_size=0, constrained=True)
        except Exception as e:
            logger.warning(f"[MinimapVerify] match error for map {self._locked_map_id}: {e}")
            return False

        logger.info(
            f"[MinimapVerify] map={self._locked_map_id} conf={result.confidence:.3f} "
            f"matches={result.match_count} inliers={result.inlier_count}"
        )

        if result.success and result.confidence >= CONFIDENCE_THRESHOLD and result.match_count >= 10:
            self._minimap_match_scale = result.map_scale
            return True
        return False

    def _in_big_map(self):
        if self.frame is None:
            return False
        h, w = self.frame.shape[:2]
        matches = 0
        for (rx, ry), (b, g, r) in BIG_MAP_COLOR_CHECKS:
            px = int(rx * w)
            py = int(ry * h)
            pixel = self.frame[py, px]
            pb, pg, pr = int(pixel[0]), int(pixel[1]), int(pixel[2])
            if (abs(pb - b) <= BIG_MAP_COLOR_TOLERANCE and
                    abs(pg - g) <= BIG_MAP_COLOR_TOLERANCE and
                    abs(pr - r) <= BIG_MAP_COLOR_TOLERANCE):
                matches += 1
        return matches >= 3

    def run(self):
        # 资源下载/解压期间挂起检测：先释放数据库与特征文件句柄再让解压覆盖它们。
        if self._assets_suspend.is_set():
            if not self._assets_released.is_set():
                self._release_map_resources()
                self._assets_released.set()
            self.sleep(0.5)
            return

        # Poll before every map/display guard.  If the overlay is currently
        # hidden, on_minimap/on_bigmap are intentionally not called, so polling
        # only inside those methods would make the hotkey able to turn display
        # off but unable to turn it back on.
        controller = self._overlay_ctl()
        controller._process_map_display_toggle()
        controller._poll_chest_confirm_hotkey()

        # 启用附加功能后的一次性检查（版本号展示 + 资源缺失自动下载）。配置里已启用、
        # 程序启动后直接进入循环的情况也会在这里覆盖到。
        self._ensure_feature_started()

        # ``in_team`` is a useful guard for combat tasks, but it is not a
        # reliable prerequisite for map rendering: the team portraits/text can
        # be hidden during normal-world transitions (or fail template matching
        # at a different UI scale) while the coordinate OCR and minimap remain
        # valid.  Keep the original guard for the normal path, then allow a
        # strictly read-only coordinate fallback when the frame is not the big
        # map.  No input is generated by this fallback and AutoCombatTask still
        # uses its unchanged ``in_team`` gate.
        in_team = self.scene.in_team(self.in_team_and_world)
        fallback_minimap = False
        in_big_map = None
        raw_position = None
        if not in_team:
            in_big_map = self._in_big_map()
            if not in_big_map and self.config.get('_Overlay enabled'):
                raw_position = self._get_position_detector().detect_position(self.frame)
                fallback_minimap = bool(raw_position)
                if fallback_minimap:
                    self._fallback_position_log_counter += 1
                    if (self._fallback_position_log_counter <= 3 or
                            self._fallback_position_log_counter % 50 == 0):
                        logger.info(
                            f"[MinimapFallback] in_team=False, position={raw_position}"
                        )

        if in_team or fallback_minimap:
            self._fallback_failures = 0
            self._in_team_failures = 0
            self._last_match_scale = None

            if raw_position is None:
                start = time.time()
                raw_position = self._get_position_detector().detect_position(self.frame)
                elapsed = (time.time() - start) * 1000
            else:
                elapsed = 0.0

            if raw_position:
                result = self._denoise(raw_position)
                if result:
                    self._ocr_noresult_counter = 0
                    if result[0] % 25 == 0:
                        pos_text = f'{result[0]},{result[1]},{result[2]}'
                        self.info_set('OCR check', f'position: {pos_text} took {elapsed:.0f}ms')

                    if self._is_minimap_dark():
                        if self._locked_map_id is not None:
                            logger.info("[MinimapMatch] minimap too dark, unlocking")
                            self._locked_map_id = None
                            self._minimap_match_scale = None
                            self._lock_ocr_pos = None
                            self._second_check_done = False
                        self.sleep(2)
                        return

                    self._recheck_counter += 1
                    interval = self.config.get('Detect interval (ms)', 500)
                    recheck_interval = math.ceil(30 * 1000 / interval)

                    if self._locked_map_id is not None and self._recheck_counter >= recheck_interval:
                        self._recheck_counter = 0
                        if self._verify_locked_map(result):
                            logger.info(f"[MinimapVerify] recheck passed, map_id={self._locked_map_id}")
                        else:
                            logger.info(f"[MinimapVerify] recheck failed, unlocking map_id={self._locked_map_id}")
                            self._locked_map_id = None
                            self._minimap_match_scale = None
                            self._lock_ocr_pos = None
                            self._second_check_done = False
                    elif self._locked_map_id is None:
                        self._recheck_counter = 0

                    match_interval = max(1, math.ceil(1000 / interval))
                    self._minimap_match_counter += 1

                    if self._locked_map_id is None and self._minimap_match_counter >= match_interval:
                        self._minimap_match_counter = 0
                        match_result = self._match_minimap_map_id(result)
                        if match_result:
                            self._locked_map_id, self._minimap_match_scale = match_result
                            self._lock_ocr_pos = (result[0], result[1])
                            self._second_check_done = False
                            logger.info(f"[MinimapMatch] locked map_id={self._locked_map_id} map_scale={self._minimap_match_scale:.4f}")

                    if self._locked_map_id is not None and not self._second_check_done and self._lock_ocr_pos is not None:
                        dx = abs(result[0] - self._lock_ocr_pos[0])
                        dy = abs(result[1] - self._lock_ocr_pos[1])
                        if dx + dy >= 20:
                            if self._verify_locked_map(result):
                                self._second_check_done = True
                                self._lock_ocr_pos = (result[0], result[1])
                                logger.info(f"[MinimapVerify] second check passed, map_id={self._locked_map_id}")
                            else:
                                logger.info(f"[MinimapVerify] second check failed, unlocking map_id={self._locked_map_id}")
                                self._locked_map_id = None
                                self._minimap_match_scale = None
                                self._lock_ocr_pos = None
                                self._second_check_done = False

                    if self.config.get('_Overlay enabled'):
                        self._overlay_ctl().on_minimap(result, self._locked_map_id)
                else:
                    self._ocr_noresult_counter += 1
                    if self._ocr_noresult_counter <= 3 or self._ocr_noresult_counter % 20 == 0:
                        logger.info(f"[ocr check] ocr noresult ({self._ocr_noresult_counter})")
                    # 当前帧去噪后无有效定位（丢失定位）：清理交互窗口绘制（问题3b）。
                    self._overlay_ctl().on_idle()
            else:
                self._ocr_noresult_counter += 1
                if self._ocr_noresult_counter <= 3 or self._ocr_noresult_counter % 20 == 0:
                    logger.info(f"[ocr check] no raw_position ({self._ocr_noresult_counter})")
                # 当前帧无原始定位（丢失 OCR）：清理交互窗口绘制（问题3b）。
                self._overlay_ctl().on_idle()

            interval = self.config.get('Detect interval (ms)', 500)
            self.sleep(interval / 1000)
            return

        if in_big_map is None:
            in_big_map = self._in_big_map()
        self._ensure_view_probe()
        self._view_probe_mark_big_map(in_big_map)
        est = self._ensure_view_estimator()
        if not in_big_map and est is not None and est.is_valid:
            # 离开大地图后视图状态失效（下次进入会重新以匹配结果播种）
            est.invalidate('离开大地图')

        if in_big_map:
            if self.config.get('World map overlay') and self._last_valid is not None:
                self._view_est_sync_screen_center(est)
                attempt = self._fallback_failures + 1
                is_third = (self._fallback_failures == 2)
                is_fourth = (self._fallback_failures == 3)

                use_full_map = is_third or is_fourth
                if is_fourth and self._locked_map_id is not None:
                    logger.info(f"[MapFallback] attempt {attempt}/{FALLBACK_MAX_FAILURES}, unlocking map_id={self._locked_map_id} for full-map all")
                    self._locked_map_id = None
                    self._minimap_match_scale = None
                    self._lock_ocr_pos = None
                    self._second_check_done = False

                crop_size = FRAME_CROP_SIZE + 100 * self._fallback_failures

                verbose = self._fallback_log_counter <= 3 or self._fallback_log_counter % 20 == 0
                if verbose:
                    logger.info(
                        f"[MapFallback] big map attempt {attempt}/{FALLBACK_MAX_FAILURES}"
                        f"{', full-map' if use_full_map else ''}, crop={crop_size}"
                    )
                # region 中心优先用推算值：拖动/缩放瞬态下它比"上次匹配结果"准得多，
                # 实测 76~90% 的匹配失败都发生在鼠标操作 0.5s 内。
                est_state = est.state() if est is not None else None
                search_pos = self._last_valid
                if est_state is not None and est_state.source != 'match':
                    ex, ey = est_state.ocr_pos
                    z = self._last_valid[2] if len(self._last_valid) > 2 else 0
                    search_pos = (ex, ey, z)

                result = self._try_map_match(search_pos, full_map=use_full_map, crop_size=crop_size)
                if result is None or not result.success:
                    if self._view_probe is not None:
                        self._view_probe.log_match_failed(
                            crop_size=crop_size, full_map=use_full_map,
                            reason='no result' if result is None else 'not success')
                    # 匹配失败但推算状态还新鲜 -> 用推算值继续渲染，避免叠加层在
                    # 拖动/缩放瞬态里闪断（原有失败计数与兜底清理逻辑保持不变）
                    if est_state is not None and est_state.confidence >= 0.25:
                        self._last_valid = (est_state.ocr_pos[0], est_state.ocr_pos[1],
                                            self._last_valid[2]
                                            if len(self._last_valid) > 2 else 0)
                        self._overlay_ctl().on_bigmap(self._last_valid,
                                                      est_state.game_scale)
                    self._fallback_failures += 1
                    logger.warning(
                        f"[MapFallback] failed {self._fallback_failures}/{FALLBACK_MAX_FAILURES}"
                    )
                    # 前几次失败只保留当前附加层，继续进行回退匹配，避免在
                    # “本次失败、下一次成功”之间隐藏/重新显示而产生闪烁。
                    # 连续达到最大失败次数后，才清理完整附加层。
                    if self._fallback_failures >= FALLBACK_MAX_FAILURES:
                        self._clear_overlay()
                        self._overlay_ctl().on_idle()
                else:
                    self._last_match_scale = result.map_scale
                    self._fallback_failures = 0
                    if result.game_center:
                        new_x = int(result.game_center[0] / 100)
                        new_y = int(result.game_center[1] / 100)
                        new_z = self._last_valid[2] if len(self._last_valid) > 2 else 0
                        self._last_valid = (new_x, new_y, new_z)
                    game_scale = self._compute_game_scale(result)
                    if self._view_probe is not None:
                        self._view_probe.log_match(
                            map_id=self._locked_map_id, result=result,
                            game_scale=game_scale, crop_size=crop_size,
                            full_map=use_full_map)
                    # 匹配成功即吸附推算状态（残差用于观察推算精度）
                    if est is not None and result.game_center and game_scale:
                        resid = est.on_match(result.game_center, game_scale)
                        if resid is not None and verbose:
                            logger.info(f'[ViewEst] 推算残差 {resid:.1f}px, '
                                        f'{est.summary()}')
                    self._overlay_ctl().on_bigmap(self._last_valid, game_scale)

                if self._fallback_failures >= FALLBACK_MAX_FAILURES:
                    logger.warning("[MapFallback] max failures reached, sleeping 2s then resetting")
                    self._locked_map_id = None
                    self._minimap_match_scale = None
                    self._lock_ocr_pos = None
                    self._second_check_done = False
                    self._fallback_failures = 0
                    self.sleep(2)
            else:
                self._clear_overlay()
                self._overlay_ctl().on_idle()

            interval = self.config.get('Detect interval (ms)', 500)
            self.sleep(interval * 2 / 1000)
            return

        self._clear_overlay()
        self._overlay_ctl().on_idle()
        self._in_team_failures += 1
        if self._in_team_failures >= 5 and self._locked_map_id is not None:
            logger.info(f"[MinimapMatch] in_team failed {self._in_team_failures} times, unlocking map_id={self._locked_map_id}")
            self._locked_map_id = None
            self._minimap_match_scale = None
            self._lock_ocr_pos = None
            self._second_check_done = False
        interval = self.config.get('Detect interval (ms)', 500)
        self.sleep(interval / 1000)

    def _init_overlay(self):
        if self._overlay is not None:
            return
        db_path = os.path.join(MAP_DIR, 'map_items.db')
        self._overlay = MapItemOverlay(db_path)

    def _overlay_ctl(self):
        """Lazily build the three-state OverlayController collaborator."""
        if self._overlay_controller is None:
            self._overlay_controller = OverlayController(self)
        return self._overlay_controller

    def _minimap_scale_per_1000(self):
        """Resolve the minimap scale (pixels per 1000 game units).

        Prefers the live minimap match scale combined with the locked map's
        coords scale; otherwise falls back to the configured/auto default.
        Shared by both the Normal_Mode minimap draw and the Path_Mode minimap
        route draw so the two stay aligned.
        """
        if self._minimap_match_scale is not None and self._locked_map_id is not None:
            self._ensure_coords()
            coords_d = self._coords_dict.get(self._locked_map_id) if self._coords_dict else None
            if coords_d:
                coords_scale = coords_d.get('scale', [1.0, 1.0])[0]
                game_scale = self._minimap_match_scale * coords_scale
                if game_scale > 0:
                    # 问题4：能算出基于匹配的缩放 -> 更新缓存并返回，稳定后续帧。
                    scale = 1000.0 / game_scale
                    self._last_minimap_scale_per_1000 = scale
                    return scale
                return 0
        # 无匹配缩放（会回退默认）但存在缓存值 -> 返回缓存值，避免锁定态瞬断造成的
        # 缩放跳变导致路线/宝箱节点抖动（问题4）。仅在从未获得过匹配缩放时用默认值。
        if self._last_minimap_scale_per_1000 is not None:
            return self._last_minimap_scale_per_1000
        return self._get_default_scale_per_1000()

    def _get_default_scale_per_1000(self):
        if self.config.get('_Auto map scale'):
            scale_factor = 1 - (1.25 - self.screen_width / 1600)
            return scale_factor * 1.205 * 10
        return self.config.get('_Map scale (pixels per 1000 units)')

    _scale_log_counter = 0

    def _draw_overlay(self, player_pos, state_id=None, completed_ids=None,
                      status_lines=None, edge_arrow=None, target_marker=None):
        overlay_view = self.get_overlay_view()
        if overlay_view is None:
            return

        self._init_overlay()

        minimap_box = self.get_box_by_name('box_minimap')
        if minimap_box is None:
            return

        minimap_r = min(minimap_box.width, minimap_box.height) // 2
        scale_per_1000 = self._minimap_scale_per_1000()

        scale = scale_per_1000 / 1000.0
        radius = minimap_r / scale if scale > 0 else self.config.get('_Search radius (world units)')

        self._scale_log_counter += 1
        if self._scale_log_counter <= 3 or self._scale_log_counter % 50 == 0:
            logger.info(
                f"[OverlayScale] map_id={self._locked_map_id} "
                f"scale_per_1000={scale_per_1000:.4f} "
                f"minimap_r={minimap_r} "
                f"search_radius={radius:.0f}"
            )

        type_filter = selected_collection_type_ids(self.config)
        player_x = player_pos[0] * 100
        player_y = player_pos[1] * 100

        draw_items = self._overlay.build_draw_items(
            player_x, player_y, minimap_box, radius, scale_per_1000, type_filter,
            state_id=state_id, completed_ids=completed_ids
        )

        callback = MapItemOverlay.make_paint_callback(
            draw_items, edge_arrow=edge_arrow, target_marker=target_marker,
            status_lines=status_lines
        )
        overlay_view.draw(OVERLAY_DRAW_KEY, callback, duration=OVERLAY_DRAW_DURATION)
        self._overlay_registered = True
        self._minimap_draw_log_counter += 1
        if self._minimap_draw_log_counter <= 3 or self._minimap_draw_log_counter % 50 == 0:
            edge_bearing = edge_arrow[0] if edge_arrow is not None else None
            logger.info(
                f"[MinimapOverlay] map_id={state_id} draw_items={len(draw_items)} "
                f"edge_bearing={edge_bearing} target_marker={target_marker} "
                f"overlay_hwnd={getattr(overlay_view, '_hwnd', None)} "
                f"overlay_visible={getattr(overlay_view, '_visible', None)}"
            )

    def _compute_game_scale(self, result):
        if result.map_scale <= 0 or not result.game_center:
            return None
        map_id = self._locked_map_id
        if map_id is None or map_id not in self._coords_dict:
            return None
        coords_scale = self._coords_dict[map_id].get('scale', [1.0, 1.0])
        game_scale = result.map_scale * coords_scale[0]
        return game_scale

    def _draw_overlay_screen_center(self, player_pos, game_scale=None,
                                    status_lines=None):
        overlay_view = self.get_overlay_view()
        if overlay_view is None:
            return
        self._init_overlay()
        radius = self.config.get('_Search radius (world units)') * BIG_MAP_SEARCH_MULTIPLIER
        if game_scale is not None and 0.01 < game_scale:
            scale = 1.0 / game_scale
        else:
            scale_per_1000 = self._get_default_scale_per_1000()
            scale = scale_per_1000 / 1000.0
        type_filter = selected_collection_type_ids(self.config)
        player_x = player_pos[0] * 100
        player_y = player_pos[1] * 100

        items = self._overlay.query_nearby(player_x, player_y, radius, type_filter,
                                            state_id=self._locked_map_id)

        center_x = self.screen_width // 2
        center_y = self.screen_height // 2
        screen_radius = min(self.screen_width, self.screen_height) // 2

        draw_items = []
        for name, type_id, ix, iy, dist in items:
            sx, sy = self._overlay.project_to_minimap(
                ix, iy, player_x, player_y, scale, center_x, center_y
            )
            dx_center = sx - center_x
            dy_center = sy - center_y
            if math.sqrt(dx_center ** 2 + dy_center ** 2) > screen_radius:
                continue
            pixmap = ITEM_PIXMAPS.get(type_id)
            color = ITEM_COLORS.get(type_id)
            draw_items.append((sx, sy, pixmap, name, color))

        callback = MapItemOverlay.make_paint_callback(
            draw_items, status_lines=status_lines
        )
        overlay_view.draw(OVERLAY_DRAW_KEY, callback, duration=OVERLAY_DRAW_DURATION)
        self._overlay_registered = True

    def _clear_overlay(self):
        if not self._overlay_registered:
            return
        overlay_view = self.get_overlay_view()
        if overlay_view is not None:
            overlay_view.clear_draw(OVERLAY_DRAW_KEY)
        self._overlay_registered = False

    def _force_clear_overlay(self):
        """无条件清一遍附加层：ok 覆盖层 + 可点击的大地图交互窗口。

        与 :meth:`_clear_overlay` 的区别是不看 ``_overlay_registered`` 标记，因此即使
        标记与实际绘制不一致（例如刚画完一帧就被停用）也不会残留。``clear_draw`` 与交互
        窗口的 ``hide_overlay`` 都经排队信号派发到 GUI 线程，可从任意线程调用。
        """
        overlay_view = self.get_overlay_view()
        if overlay_view is not None:
            try:
                overlay_view.clear_draw(OVERLAY_DRAW_KEY)
            except Exception as e:  # pragma: no cover - Qt 运行时相关
                logger.warning(f'[Overlay] force clear draw failed: {e}')
        self._overlay_registered = False
        controller = self._overlay_controller
        if controller is not None:
            try:
                controller.on_idle()
            except Exception as e:  # pragma: no cover - Qt 运行时相关
                logger.warning(f'[Overlay] force hide interaction window failed: {e}')

    def on_destroy(self):
        self._force_clear_overlay()
        self._set_global_overlay_enabled(False)
        if self._view_probe is not None:
            try:
                self._view_probe.stop()
            except Exception as e:  # pragma: no cover - 运行时相关
                logger.warning(f'[ViewProbe] stop failed: {e}')
            self._view_probe = None
        if self._view_est is not None:
            logger.info(f'[ViewEst] 退出，{self._view_est.summary()}')
            self._view_est = None
        if self._mouse_watcher is not None:
            try:
                self._mouse_watcher.stop()
            except Exception as e:  # pragma: no cover - 运行时相关
                logger.warning(f'[MouseWatcher] stop failed: {e}')
            self._mouse_watcher = None
        if self._assets_gui is not None:
            self._assets_gui.close_dialog()
        if self._overlay_controller is not None:
            self._overlay_controller.close()
        if self._overlay is not None:
            self._overlay.close()
