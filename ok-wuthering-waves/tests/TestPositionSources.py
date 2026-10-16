import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.utils.ChestGuidanceFilter import ChestGuidanceFilter
from src.utils.PositionSources import KuroCaptureReader, PositionSources
from src.utils.KuroDirectClient import parse_kuro_message
from src.utils.positionDetector import PositionDetector


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / 'tests/data/kuro_position'
MESSAGES = [json.loads(line) for line in (CAPTURE / 'kuro-coordinate-messages.jsonl').read_text(encoding='utf-8').splitlines()]
POSITIONS = [message for message in MESSAGES if json.loads(message['text'])[0] == 200]
FIRST_TIME = POSITIONS[0]['timestamp']


def bound_sources(mode='Automatic', now=FIRST_TIME):
    sources = PositionSources()
    sources.configure(mode, str(CAPTURE), '导航测试账号')
    reader = sources.reader.poll(now)
    sources.bind(reader.session_id, now)
    return sources


def test_replay_every_record_and_disconnect():
    reader = KuroCaptureReader(CAPTURE)
    for message in POSITIONS:
        data = json.loads(message['text'])
        reader.poll(message['timestamp'])
        assert reader.latest is not None
        assert (reader.latest.map_id, reader.latest.area_id, reader.latest.x, reader.latest.y) == (
            str(data[1]), data[2], data[3], data[4])
        assert reader.latest.z is None
        assert reader.latest.observed_at is None
    last = MESSAGES[-1]
    reader.poll(last['timestamp'])
    assert reader.code == 2301
    assert reader.latest is None
    reader.poll(last['timestamp'] + 1)
    assert not reader.connected


def test_direct_protocol_parses_every_recorded_message():
    sequence = 0
    for message in MESSAGES:
        sequence += 1
        code, sample = parse_kuro_message(message['text'], 'recorded-session',
                                          sequence, message['timestamp'])
        recorded = json.loads(message['text'])
        assert code == recorded[0]
        if code == 200:
            assert sample is not None
            assert (sample.map_id, sample.area_id, sample.x, sample.y) == (
                str(recorded[1]), recorded[2], recorded[3], recorded[4])
            assert sample.received_at == message['timestamp']
        else:
            assert sample is None


def test_binding_required_account_change_and_repeat_reads():
    sources = PositionSources()
    sources.configure('Automatic', str(CAPTURE), '导航测试账号')
    decision = sources.select(known_maps=['8'], now=FIRST_TIME)
    assert decision.sample is None
    sources.bind(sources.reader.session_id, FIRST_TIME)
    first = sources.select(known_maps=['8'], now=FIRST_TIME).sample
    assert first.overlay_position == (-6910.66, -714.94)
    for index in range(15):
        sample = sources.select(known_maps=['8'], now=FIRST_TIME + index / 10).sample
        assert sample.sequence == first.sequence
        assert sample.received_at == first.received_at
    sources.configure('Automatic', str(CAPTURE), '另外一个账号')
    assert sources.binding is None
    assert sources.select(known_maps=['8'], now=FIRST_TIME + 1.5).sample is None


def test_real_long_message_gap_and_error_expire_navigation():
    # 采用真实消息间隔中的等待时间，接收记录与原始时间戳保持完整。
    gaps = [(a, b) for a, b in zip(POSITIONS, POSITIONS[1:]) if b['timestamp'] - a['timestamp'] > 8]
    assert gaps
    before, after = gaps[0]
    sources = bound_sources(now=before['timestamp'])
    assert sources.select(known_maps=['8'], now=before['timestamp']).sample is not None
    waiting_at = (before['timestamp'] + 8 + after['timestamp']) / 2
    assert sources.select(known_maps=['8'], now=waiting_at).sample is None
    assert '暂停' in sources.decision.status
    end = MESSAGES[-1]['timestamp']
    sources.select(known_maps=['8'], now=end)
    assert sources.decision.sample is None
    assert '2301' in sources.decision.status
    sources.select(known_maps=['8'], now=end + 1)
    assert '断开' in sources.decision.status


def test_real_teleport_needs_another_message():
    jump_index = next(index for index in range(1, len(POSITIONS))
                      if abs(json.loads(POSITIONS[index]['text'])[3] -
                             json.loads(POSITIONS[index - 1]['text'])[3]) > 50000)
    before, jump, next_message = POSITIONS[jump_index - 1:jump_index + 2]
    sources = bound_sources(now=before['timestamp'])
    sources.select(known_maps=['8'], now=before['timestamp'])
    assert sources.select(known_maps=['8'], now=jump['timestamp']).sample is None
    assert sources.select(known_maps=['8'], now=jump['timestamp'] + .2).sample is None
    decision = sources.select(known_maps=['8'], now=next_message['timestamp'])
    assert decision.sample is not None
    assert decision.reset_guidance
    assert decision.sample.area_id == json.loads(next_message['text'])[2]


def test_unknown_map_and_unbound_source_cannot_supply_position():
    sources = bound_sources()
    assert sources.select(known_maps=[], now=FIRST_TIME).sample is None
    sources.unbind()
    assert sources.select(known_maps=['8'], now=FIRST_TIME).sample is None


def test_capture_directory_change_clears_binding(tmp_path):
    sources = bound_sources()
    for name in ('kuro-coordinate-messages.jsonl', 'kuro-capture-events.jsonl'):
        shutil.copyfile(CAPTURE / name, tmp_path / name)
    sources.configure('Automatic', str(tmp_path), sources.account)
    assert sources.binding is None
    assert sources.select(known_maps=['8'], now=FIRST_TIME).sample is None


def test_rotated_real_capture_invalidates_session(tmp_path):
    names = ('kuro-coordinate-messages.jsonl', 'kuro-capture-events.jsonl')
    for name in names:
        shutil.copyfile(CAPTURE / name, tmp_path / name)
    sources = PositionSources()
    sources.configure('Automatic', str(tmp_path), '导航测试账号')
    sources.reader.poll(FIRST_TIME)
    sources.bind(sources.reader.session_id, FIRST_TIME)
    assert sources.select(known_maps=['8'], now=FIRST_TIME).sample is not None
    session_id = sources.reader.session_id
    for name in names:
        (tmp_path / name).rename(tmp_path / (name + '.previous'))
        shutil.copyfile(CAPTURE / name, tmp_path / name)
    assert sources.select(known_maps=['8'], now=FIRST_TIME).sample is None
    assert sources.reader.session_id != session_id


def test_actual_ocr_recovers_and_conflicting_capture_is_held():
    frame = cv2.imdecode(np.fromfile(ROOT / 'tests/images/mini_map.png', dtype=np.uint8), cv2.IMREAD_COLOR)
    text = PositionDetector().detect_position(frame)
    position = tuple(float(value) for value in text.split(','))
    assert position == (-1175, 2126, 7)
    sources = bound_sources()
    assert sources.select(known_maps=['8'], now=FIRST_TIME).sample.source == 'kuro'
    # 两份真实记录属于不同位置，验证冲突处理，不将其作为同步精度测试。
    assert sources.select(position, '8', ['8'], FIRST_TIME + .1).sample is None
    assert sources.select(position, '8', ['8'], FIRST_TIME + .2).sample is None
    result = sources.select(position, '8', ['8'], FIRST_TIME + .3)
    assert result.sample.source == 'ocr'
    assert result.sample.overlay_position == position
    assert '不一致' in result.status
    assert sources.select(None, '8', ['8'], FIRST_TIME + .4).sample is None


def test_received_guidance_uses_messages_without_repeated_sampling():
    reader = KuroCaptureReader(CAPTURE)
    start = POSITIONS[0]
    changed = next(message for message in POSITIONS if json.loads(message['text'])[3:5] !=
                   json.loads(start['text'])[3:5])
    first = reader.poll(start['timestamp']).latest
    second = reader.poll(changed['timestamp']).latest
    target = (second.x + 10000, second.y + 10000)
    guidance = ChestGuidanceFilter()
    guidance.update_received(first, 'target', *target, now=0)
    guidance.update_received(second, 'target', *target, now=1)
    result = guidance.update_received(second, 'target', *target, now=1.21)
    assert (result.player_x, result.player_y) == (second.x, second.y)
    assert not guidance._history
    assert guidance.update_received(second, 'target', *target, now=2) == result


def test_old_session_cannot_be_bound_after_real_disconnect():
    sources = bound_sources()
    session_id = sources.reader.session_id
    with pytest.raises(ValueError):
        sources.bind(session_id, MESSAGES[-1]['timestamp'] + 1)
