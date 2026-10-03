import json
from pathlib import Path

import requests


RESOURCE_VERSION_URL = 'https://api.kurobbs.com/map/core/config/getMapResource'
CATALOG_URL = 'https://web-static.kurobbs.com/mcmap/catalog/{version}/8/catalog.json'
CATEGORY_FILE = 'map_categories.json'
BUNDLED_CATEGORIES = Path(__file__).resolve().parents[1] / 'data' / CATEGORY_FILE


def validate_categories(document):
    if document['schema_version'] != 1:
        raise ValueError('地图分类文件版本不受支持')
    lookup = {}
    for order, category in enumerate(document['categories']):
        if not category['id'] or not category['name'] or not category['type_ids']:
            raise ValueError('地图分类目录内容不完整')
        for type_order, type_id in enumerate(category['type_ids']):
            if not isinstance(type_id, str) or not type_id:
                raise ValueError('地图分类类型编号无效')
            if type_id in lookup:
                raise ValueError(f'地图类型重复归类：{type_id}')
            lookup[type_id] = (category['id'], category['name'], order, type_order)
    if not lookup:
        raise ValueError('地图分类目录为空')
    return lookup


def load_category_lookup(db_path):
    resource_file = Path(db_path).parent / CATEGORY_FILE
    source = resource_file if resource_file.exists() else BUNDLED_CATEGORIES
    with source.open(encoding='utf-8') as stream:
        return validate_categories(json.load(stream))


def download_collection_categories(destination):
    with requests.Session() as session:
        response = session.post(RESOURCE_VERSION_URL, data={}, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result['code'] != 200:
            raise ValueError(f'获取地图分类版本失败：{result["code"]}')
        version = result['data']
        if not isinstance(version, str) or not version.isalnum():
            raise ValueError('地图分类资源版本无效')
        url = CATALOG_URL.format(version=version)
        response = session.get(url, timeout=30)
        response.raise_for_status()
        catalog = response.json()
    document = {
        'schema_version': 1,
        'resource_version': version,
        'source_url': url,
        'categories': [
            {'id': category['id'], 'name': category['name'],
             'type_ids': [entry['id'] for entry in category['children']]}
            for category in sorted(catalog, key=lambda entry: entry['sort'])
            if category['id'] not in ('1', '2')
        ],
    }
    validate_categories(document)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.json.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temporary.replace(destination)
    return document
