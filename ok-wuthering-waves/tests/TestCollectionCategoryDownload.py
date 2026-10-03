import json
import tempfile
import unittest
from pathlib import Path

from src.utils.CollectionCategories import (
    CATEGORY_FILE, download_collection_categories, load_category_lookup, validate_categories,
)


class TestCollectionCategoryDownload(unittest.TestCase):
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
