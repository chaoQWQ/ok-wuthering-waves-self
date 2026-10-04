import math
import uuid
from dataclasses import dataclass

import requests

from src.utils.PathRoute import PathRoute, parse_path_route


API_ROOT = 'https://api.kurobbs.com'


class KuroRoutesClient:
    def __init__(self, state_id=8):
        self.state_id = int(state_id)

    def _post(self, path, data):
        with requests.Session() as session:
            response = session.post(API_ROOT + path, data=data, headers={
                'wiki_type': '10', 'source': 'h5',
                'state_id': str(self.state_id), 'devcode': uuid.uuid4().hex,
            }, timeout=(10, 25))
            response.raise_for_status()
            payload = response.json()
        if payload.get('code') != 200:
            raise ValueError(f"库街区请求失败：{payload.get('msg', payload.get('code'))}")
        return payload['data']

    def search(self, name='', sort_type=2, page=1, all_maps=False):
        return self._post('/ugc/server/path/search', {
            'name': name.strip(), 'sortType': int(sort_type),
            'page': int(page), 'limit': 20, 'stateId': self.state_id,
            'stateIdType': 1, 'needOtherState': int(all_maps),
        })

    def detail(self, route_id):
        data = self._post('/map/core/gamer/path/getContent', {
            'id': str(route_id), 'showType': 3,
        })
        KuroRoute.from_data(data)
        return data


@dataclass(frozen=True)
class KuroRoute:
    route_id: str
    name: str
    author: str
    description: str
    path: PathRoute

    @classmethod
    def from_data(cls, data):
        if not isinstance(data, dict) or not data.get('pathId'):
            raise ValueError('路线缺少编号')
        sections = data['sectionList']
        section_ids = set()
        for section in sections:
            section_id = int(section['sectionId'])
            if section_id in section_ids:
                raise ValueError('路线分段编号重复')
            section_ids.add(section_id)
            for point in section['positionList']:
                if not point.get('positionId'):
                    raise ValueError('路线节点缺少编号')
                if not all(math.isfinite(float(point[key]))
                           for key in ('xposition', 'yposition')):
                    raise ValueError('路线节点坐标无效')
        path = parse_path_route({'data': data})
        return cls(str(data['pathId']), str(data['name']),
                   str(data.get('creatorName') or ''),
                   str(data.get('desc') or ''), path)


class RouteNavigator:
    def __init__(self, route, section_id=0):
        self.route = route
        sections = tuple(section for section in route.path.sections
                         if not section_id or section.section_id == section_id)
        if not sections:
            raise ValueError('所选路线分段不存在')
        self.path = PathRoute(route.path.state_id, sections)
        self.nodes = tuple((section.section_id, index, node)
                           for section in sections
                           for index, node in enumerate(section.nodes))
        self.cursor = 0

    def target(self, completed):
        completed = {str(value) for value in completed}
        while self.cursor < len(self.nodes):
            node = self.nodes[self.cursor][2]
            if node.position_id not in completed:
                return node
            self.cursor += 1
        return None

    def skip(self):
        self.cursor = min(self.cursor + 1, len(self.nodes))

    def remaining(self, completed):
        completed = {str(value) for value in completed}
        return sum(node.position_id not in completed
                   for _, _, node in self.nodes[self.cursor:])
