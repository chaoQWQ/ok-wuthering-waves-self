import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CollectionType:
    type_id: str
    name: str
    count: int


@dataclass(frozen=True)
class CollectionMap:
    state_id: int
    country_id: int
    state_name: str
    country_name: str
    types: tuple

    @property
    def key(self):
        return f'{self.state_id}:{self.country_id}'


def load_collection_catalog(db_path, state_id=None, country_id=None):
    path = Path(db_path).resolve(strict=True)
    conditions = ["l.type_id IS NOT NULL", "l.type_id != ''"]
    params = []
    for column, value in (('state_id', state_id), ('country_id', country_id)):
        if value is not None:
            conditions.append(f'l.{column} = ?')
            params.append(int(value))
    # 资源数据库只读；连接在读取结束时关闭，允许随后更新资源包。
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
        rows = conn.execute(
            'SELECT l.type_id, i.name, COUNT(*) FROM location l '
            'JOIN item i ON i.id = l.item_id '
            'WHERE ' + ' AND '.join(conditions) +
            ' GROUP BY l.type_id, i.name ORDER BY i.name, l.type_id', params,
        ).fetchall()
    names = {}
    counts = {}
    for type_id, name, count in rows:
        if not name or not name.strip():
            raise ValueError(f'地图资源类型缺少名称：{type_id}')
        names.setdefault(type_id, []).append(name)
        counts[type_id] = counts.get(type_id, 0) + count
    return tuple(
        CollectionType(type_id, ' / '.join(dict.fromkeys(labels)), counts[type_id])
        for type_id, labels in names.items()
    )


def load_collection_maps(db_path):
    path = Path(db_path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
        rows = conn.execute(
            'SELECT DISTINCT s.id, c.id, s.name, c.name FROM location l '
            'JOIN state s ON s.id = l.state_id '
            'JOIN country c ON c.id = l.country_id '
            'ORDER BY c.id, s.id'
        ).fetchall()
    return tuple(
        CollectionMap(state_id, country_id, state_name, country_name,
                      load_collection_catalog(path, state_id, country_id))
        for state_id, country_id, state_name, country_name in rows
    )


def collection_filter_sql(default_types, map_type_filters, state_id=None):
    scopes = []
    branches = []
    branch_params = []
    scope_params = []
    for key, types in sorted((map_type_filters or {}).items()):
        map_id, country_id = (int(value) for value in key.split(':'))
        if state_id is not None and map_id != int(state_id):
            continue
        scope = '(l.state_id = ? AND l.country_id IS ?)'
        scopes.append(scope)
        scope_params.extend((map_id, country_id))
        if types:
            placeholders = ','.join('?' for _ in types)
            branches.append(f'({scope} AND l.type_id IN ({placeholders}))')
            branch_params.extend((map_id, country_id, *types))
    default_clause = '1'
    if default_types:
        default_clause = 'l.type_id IN (' + ','.join('?' for _ in default_types) + ')'
    if scopes:
        default_clause = '(NOT (' + ' OR '.join(scopes) + ') AND ' + default_clause + ')'
    branches.append(default_clause)
    return '(' + ' OR '.join(branches) + ')', [
        *branch_params, *scope_params, *(default_types or ()),
    ]


def encode_collection_types(type_ids):
    return ['type:' + type_id for type_id in dict.fromkeys(type_ids)]
