import json
import tempfile
import unittest
from pathlib import Path

from src.utils.CollectionCategories import (
    CATEGORY_FILE, download_collection_categories, load_category_lookup, validate_categories,
)
from src.utils.CollectionRegions import (
    REGION_FILE, download_collection_regions, load_region_lookup, validate_regions,
)


class TestCollectionCategoryDownload(unittest.TestCase):
    def test_live_official_region_download_and_local_read(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / REGION_FILE
            document = download_collection_regions(target)
            with target.open(encoding='utf-8') as stream:
                self.assertEqual(json.load(stream), document)
            lookup = load_region_lookup(Path(directory) / 'map_items.db')
            self.assertEqual(lookup, validate_regions(document))
            self.assertEqual(lookup[(903, 3)], ('黎那汐塔', '阿维纽林'))
            self.assertNotIn((903, 1), lookup)
            self.assertEqual(lookup[(8, 1)], ('瑝珑', '今州 / 梦州'))
            self.assertEqual(lookup[(906, 4)], ('罗伊冰原', '拉海洛'))
            self.assertFalse(target.with_suffix('.json.tmp').exists())

    def test_live_official_catalog_download_and_local_read(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / CATEGORY_FILE
            document = download_collection_categories(target)
            with target.open(encoding='utf-8') as stream:
                self.assertEqual(json.load(stream), document)
            lookup = load_category_lookup(Path(directory) / 'map_items.db')
            self.assertEqual(lookup, validate_categories(document))
            self.assertEqual(lookup['qzx_01'][1], '收集物')
            self.assertEqual(lookup['CS_01'][1], '探索')
            self.assertEqual(lookup['42601630'][1], '采集物')
            self.assertEqual(lookup['qzk'][1], '强敌')
            self.assertEqual(lookup['IconMap_WYQ'][1], '挑战')
            self.assertEqual(lookup['3731001'][1], 'BOSS')
            self.assertFalse(target.with_suffix('.json.tmp').exists())


if __name__ == '__main__':
    unittest.main()
