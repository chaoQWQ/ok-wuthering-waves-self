import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CollectionType:
    type_id: str
    name: str
    count: int


def load_collection_catalog(db_path):
    path = Path(db_path).resolve(strict=True)
    # 资源数据库只读；连接在读取结束时关闭，允许随后更新资源包。
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
        rows = conn.execute(
            'SELECT l.type_id, i.name, COUNT(*) FROM location l '
            'JOIN item i ON i.id = l.item_id '
            'WHERE l.type_id IS NOT NULL AND l.type_id != \'\' '
            'GROUP BY l.type_id, i.name ORDER BY i.name, l.type_id'
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


def encode_collection_types(type_ids):
    return ['type:' + type_id for type_id in dict.fromkeys(type_ids)]
