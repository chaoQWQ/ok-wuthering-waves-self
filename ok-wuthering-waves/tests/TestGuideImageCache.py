import os
import tempfile
import unittest

from src.utils.GuideImageCache import (
    ALLOWED_IMAGE_HOSTS,
    build_kuro_image_url,
    guide_cache_filename,
    is_allowed_image_host,
    validate_image_url,
    GuideImageCache,
)
from src.utils.InteractionOverlayWindow import (
    BubbleSpec,
    InteractionOverlayWindow,
    _normalize_bubble,
)


class TestGuideImageUrlSecurity(unittest.TestCase):
    """测试图片 URL 解析、严格官方域名白名单与注入防护。"""

    def test_allowed_official_hosts(self):
        self.assertTrue(is_allowed_image_host("web-static.kurobbs.com"))
        self.assertTrue(is_allowed_image_host("api.kurobbs.com"))
        self.assertTrue(is_allowed_image_host("static.kurobbs.com"))
        self.assertTrue(is_allowed_image_host("sub.kurobbs.com"))

    def test_untrusted_hosts_rejected(self):
        self.assertFalse(is_allowed_image_host("evil.com"))
        self.assertFalse(is_allowed_image_host("kurobbs.com.evil.com"))
        self.assertFalse(is_allowed_image_host("notkurobbs.com"))
        self.assertFalse(is_allowed_image_host(""))
        self.assertFalse(is_allowed_image_host(None))

    def test_validate_image_url_accepts_valid_kuro_urls(self):
        url = "https://web-static.kurobbs.com/adminConfig/61/props_namephoto/1762501380959.png"
        self.assertEqual(validate_image_url(url), url)

        api_url = "https://api.kurobbs.com/image/detail/123.jpg"
        self.assertEqual(validate_image_url(api_url), api_url)

    def test_validate_image_url_rejects_untrusted_urls(self):
        self.assertIsNone(validate_image_url("https://malicious-site.example/attack.png"))
        self.assertIsNone(validate_image_url("http://192.168.1.1/internal.jpg"))
        self.assertIsNone(validate_image_url("ftp://web-static.kurobbs.com/file.png"))
        self.assertIsNone(validate_image_url("javascript:alert(1)"))
        self.assertIsNone(validate_image_url(""))
        self.assertIsNone(validate_image_url(None))

    def test_build_kuro_image_url_relative_path(self):
        rel = "adminConfig/61/props_namephoto/1762501380959.png"
        expected = "https://web-static.kurobbs.com/adminConfig/61/props_namephoto/1762501380959.png"
        self.assertEqual(build_kuro_image_url(rel), expected)

    def test_build_kuro_image_url_leading_slash(self):
        rel = "/adminConfig/61/props_namephoto/1762501380959.png"
        expected = "https://web-static.kurobbs.com/adminConfig/61/props_namephoto/1762501380959.png"
        self.assertEqual(build_kuro_image_url(rel), expected)

    def test_build_kuro_image_url_absolute_trusted(self):
        absolute = "https://web-static.kurobbs.com/test.webp"
        self.assertEqual(build_kuro_image_url(absolute), absolute)

    def test_build_kuro_image_url_absolute_untrusted_rejected(self):
        absolute = "https://untrusted-host.com/test.png"
        self.assertIsNone(build_kuro_image_url(absolute))


class TestGuideCacheFilenameSecurity(unittest.TestCase):
    """测试缓存文件名基于安全的节点编号和图片 URL 哈希生成。"""

    def test_cache_filename_uses_node_id_and_hash(self):
        url = "https://web-static.kurobbs.com/adminConfig/61/props_namephoto/1762501380959.png"
        fname1 = guide_cache_filename("node_123", url)
        self.assertTrue(fname1.startswith("node_123_"))
        self.assertTrue(fname1.endswith(".png"))

        # 相同输入产生相同文件名
        fname2 = guide_cache_filename("node_123", url)
        self.assertEqual(fname1, fname2)

        # 不同 URL 产生不同文件名
        url_other = "https://web-static.kurobbs.com/adminConfig/61/props_namephoto/9999999999999.png"
        fname3 = guide_cache_filename("node_123", url_other)
        self.assertNotEqual(fname1, fname3)

    def test_cache_filename_sanitizes_path_traversal(self):
        malicious_id = "../../etc/passwd"
        url = "https://web-static.kurobbs.com/test.png"
        fname = guide_cache_filename(malicious_id, url)
        self.assertNotIn("/", fname)
        self.assertNotIn("\\", fname)
        self.assertNotIn("..", fname)

    def test_cache_filename_sanitizes_special_characters(self):
        special_id = "node*?<>:|#1"
        url = "https://web-static.kurobbs.com/sample.jpg"
        fname = guide_cache_filename(special_id, url)
        self.assertNotIn("*", fname)
        self.assertNotIn("?", fname)
        self.assertNotIn("<", fname)
        self.assertNotIn(">", fname)
        self.assertTrue(fname.endswith(".jpg"))


class TestGuideImageCacheStorage(unittest.TestCase):
    """测试 GuideImageCache 本地缓存与状态管理。"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cache = GuideImageCache(cache_dir=self.temp_dir)

    def tearDown(self):
        self.cache.stop()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initial_status_is_none(self):
        url = "https://web-static.kurobbs.com/sample.png"
        self.assertEqual(self.cache.get_status(url), "none")

    def test_untrusted_url_request_fails_fast(self):
        results = []
        self.cache.request_image("bad_node", "https://untrusted.com/test.png",
                                 on_ready=lambda pm, status: results.append((pm, status)))
        self.assertEqual(results, [(None, "failed")])

    def test_cached_file_loaded_from_disk(self):
        # 创建伪造的合法缓存图片文件在本地磁盘中
        from PySide6.QtGui import QImage, QColor
        url = "https://web-static.kurobbs.com/test_img.png"
        fname = guide_cache_filename("local_node", url)
        cache_path = os.path.join(self.temp_dir, fname)

        img = QImage(64, 64, QImage.Format_RGB32)
        img.fill(QColor(255, 100, 50))
        img.save(cache_path, "PNG")

        # 从磁盘同步获取或通过 request_image 获取
        pm = self.cache.get_pixmap("local_node", url)
        self.assertIsNotNone(pm)
        self.assertFalse(pm.isNull())
        self.assertEqual(pm.width(), 64)
        self.assertEqual(pm.height(), 64)


class TestQtBubbleSpecAndPainting(unittest.TestCase):
    """测试真实 Qt 详情气泡的尺寸计算、规范归一化与 QPainter 渲染。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_normalize_bubble_legacy_three_tuple(self):
        spec = _normalize_bubble((100, 200, "测试描述"))
        self.assertEqual(spec.x, 100)
        self.assertEqual(spec.y, 200)
        self.assertEqual(spec.text, "测试描述")
        self.assertEqual(spec.title, "")
        self.assertIsNone(spec.pixmap)
        self.assertEqual(spec.image_status, "")

    def test_normalize_bubble_extended_tuple(self):
        from PySide6.QtGui import QPixmap
        pm = QPixmap(30, 30)
        spec = _normalize_bubble((50, 60, "说明文字", "点位标题", pm, "ready"))
        self.assertEqual(spec.x, 50)
        self.assertEqual(spec.y, 60)
        self.assertEqual(spec.text, "说明文字")
        self.assertEqual(spec.title, "点位标题")
        self.assertEqual(spec.pixmap, pm)
        self.assertEqual(spec.image_status, "ready")

    def test_bubble_box_rect_expands_for_title_and_image(self):
        from PySide6.QtGui import QPixmap
        window = InteractionOverlayWindow(0, 0, 1000, 800)
        try:
            # 只有文本的气泡
            spec_text_only = BubbleSpec(x=100, y=100, text="短文本")
            window._bubble = spec_text_only
            rect1 = window._bubble_box_rect()
            self.assertIsNotNone(rect1)

            # 增加标题和图片的气泡
            pm = QPixmap(200, 150)
            spec_full = BubbleSpec(x=100, y=100, text="长文本多行说明\n第二行攻略内容",
                                   title="中曲台地收集宝箱", pixmap=pm, image_status="ready")
            window._bubble = spec_full
            rect2 = window._bubble_box_rect()
            self.assertIsNotNone(rect2)

            # 带有图片和标题的气泡高度和宽度都应显著大于纯短文本气泡
            self.assertGreater(rect2.height(), rect1.height())
            self.assertGreater(rect2.width(), rect1.width())
        finally:
            window.close()

    def test_paint_bubble_real_qpainter(self):
        from PySide6.QtGui import QImage, QPainter, QPixmap, QColor
        window = InteractionOverlayWindow(0, 0, 600, 400)
        try:
            pm = QPixmap(100, 80)
            pm.fill(QColor(100, 200, 50))
            spec = BubbleSpec(x=50, y=50, text="详细攻略说明：击败敌人后开启宝箱",
                              title="宝箱1号", pixmap=pm, image_status="ready")
            window._bubble = spec

            canvas_img = QImage(600, 400, QImage.Format_ARGB32)
            canvas_img.fill(0)
            painter = QPainter(canvas_img)
            try:
                window._paint_bubble(painter)
            finally:
                painter.end()

            # 验证气泡区域有实际绘制的像素（非全透明）
            rect = window._bubble_box_rect()
            center_x = rect.x() + rect.width() // 2
            center_y = rect.y() + rect.height() // 2
            self.assertNotEqual(canvas_img.pixel(center_x, center_y), 0)
        finally:
            window.close()

    def test_bubble_status_notices_rendered(self):
        from PySide6.QtGui import QImage, QPainter
        window = InteractionOverlayWindow(0, 0, 500, 300)
        try:
            for status in ("loading", "failed", "no_image"):
                spec = BubbleSpec(x=20, y=20, text="资源描述说明",
                                  title="测试点位", pixmap=None, image_status=status)
                window._bubble = spec
                rect = window._bubble_box_rect()
                self.assertIsNotNone(rect)

                img = QImage(500, 300, QImage.Format_ARGB32)
                img.fill(0)
                p = QPainter(img)
                try:
                    window._paint_bubble(p)
                finally:
                    p.end()
                self.assertNotEqual(img.pixel(rect.x() + 10, rect.y() + 10), 0)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
