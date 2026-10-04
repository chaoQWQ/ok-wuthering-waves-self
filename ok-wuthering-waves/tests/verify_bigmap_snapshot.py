import argparse
import json
from pathlib import Path

import cv2

from src.match_engine import SiftGzEngine
from src.match_engine.common import CoordsRef
from src.match_engine.params import ParamSet, params_to_engine_kwargs
from src.utils.BigMapViewFilter import BigMapViewFilter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('image')
    parser.add_argument('--map-id', type=int, default=8)
    args = parser.parse_args()
    assets = Path('assets/stitched')
    settings = json.loads((assets / 'setting.json').read_text(encoding='utf-8'))
    kwargs = params_to_engine_kwargs(ParamSet.from_name(settings['siftgz']['default']))
    kwargs.pop('ratio', None)
    kwargs.pop('max_dist', None)
    engine = SiftGzEngine(args.map_id, str(assets / f'{args.map_id}.png'), str(assets), **kwargs)
    coords = json.loads((assets / 'map_coords.json').read_text(encoding='utf-8'))[str(args.map_id)]
    engine.coords = CoordsRef(offset=tuple(coords['offset']), scale=tuple(coords['scale']),
                             min_xy=tuple(coords['min']), max_xy=tuple(coords['max']))
    frame = cv2.imread(args.image)
    if frame is None:
        raise ValueError('无法读取游戏截图')
    height, width = frame.shape[:2]
    cropped = frame[height // 2 - 200:height // 2 + 200, width // 2 - 200:width // 2 + 200]
    state = BigMapViewFilter()
    region = None
    states = []
    for _ in range(15):
        result = engine.match_array(cropped, region=region, crop_size=0)
        if not result.success or result.confidence < 0.7 or result.match_count < 10:
            raise ValueError('游戏截图匹配没有达到确认条件')
        game_scale = result.map_scale * coords['scale'][0]
        center, stable_scale = state.update(result.game_center, game_scale, args.map_id, (width, height))
        states.append((center, stable_scale))
        region = (int(result.center[0] - 500), int(result.center[1] - 500), 1000, 1000)
    changes = sum(left != right for left, right in zip(states, states[1:]))
    if changes:
        raise AssertionError(f'静止截图的稳定输出变化了 {changes} 次')
    print(f'真实游戏截图匹配 {len(states)} 次，稳定输出变化 {changes} 次')


if __name__ == '__main__':
    main()
