import json
from pathlib import Path

import requests

from src.utils.CollectionCategories import RESOURCE_VERSION_URL


REGION_FILE = 'map_regions.json'
COUNTRY_URL = 'https://web-static.kurobbs.com/mcmap/country/{version}/country.json'
BUNDLED_REGIONS = Path(__file__).resolve().parents[1] / 'data' / REGION_FILE


def validate_regions(document):
    if document['schema_version'] != 1:
        raise ValueError('地图地区目录版本不受支持')
    lookup = {}
    for region in document['regions']:
        country_id = int(region['country_id'])
        if not region['name'] or not region['maps']:
            raise ValueError('地图地区目录内容不完整')
        for entry in region['maps']:
            key = (int(entry['state_id']), country_id)
            if key in lookup or not entry['name']:
                raise ValueError(f'地图地区目录编号重复或名称为空：{key}')
            lookup[key] = (region['name'], entry['name'])
    if not lookup:
        raise ValueError('地图地区目录为空')
    return lookup


def load_region_lookup(db_path):
    resource_file = Path(db_path).parent / REGION_FILE
    source = resource_file if resource_file.exists() else BUNDLED_REGIONS
    with source.open(encoding='utf-8') as stream:
        return validate_regions(json.load(stream))


def download_collection_regions(destination):
    with requests.Session() as session:
        response = session.post(RESOURCE_VERSION_URL, data={}, timeout=30)
        response.raise_for_status()
        result = response.json()
        if result['code'] != 200:
            raise ValueError(f'获取地图地区版本失败：{result["code"]}')
        version = result['data']
        if not isinstance(version, str) or not version.isalnum():
            raise ValueError('地图地区资源版本无效')
        url = COUNTRY_URL.format(version=version)
        response = session.get(url, timeout=30)
        response.raise_for_status()
        countries = response.json()

    regions = []
    for country in sorted(countries, key=lambda entry: entry['order']):
        group_ids = country['mapStateId'].split(',') if country['mapStateId'] else []
        group_names = country['mapStateName'].split(',') if country['mapStateName'] else []
        if len(group_ids) != len(group_names):
            raise ValueError('地图地区分组数量不一致')
        groups = dict(zip(group_ids, group_names))
        states = {}
        for entry in country['countrys']:
            state_id = int(entry['stateId'])
            state = states.setdefault(state_id, {'names': [], 'groups': []})
            state['names'].append(entry['name'])
            group_id = entry['mapState']
            if group_id:
                state['groups'].append(groups[group_id])
        maps = []
        for state_id, state in states.items():
            # 大世界底图共用编号，显示官方分组名称；独立底图显示地图名称。
            use_groups = state_id == 8 or len(state['names']) > 1
            names = state['groups'] if use_groups and state['groups'] else state['names']
            maps.append({'state_id': state_id, 'name': ' / '.join(dict.fromkeys(names))})
        regions.append({'country_id': int(country['countryId']),
                        'name': country['name'], 'maps': maps})
    document = {'schema_version': 1, 'resource_version': version,
                'source_url': url, 'regions': regions}
    validate_regions(document)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.json.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temporary.replace(destination)
    return document
