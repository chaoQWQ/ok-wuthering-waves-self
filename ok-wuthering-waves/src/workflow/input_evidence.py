import json


def describe_input_events(events):
    state_events = []
    trajectories = []
    previous = None
    group = None
    for event in events:
        if event['kind'] != 'input':
            state_events.append(event)
            continue
        sample = json.loads(event['payload'])
        state = (tuple(sample['keys']), tuple(sample['buttons']),
                 sample.get('cursor_visible'))
        if state != previous or sample.get('baseline') or sample.get('key_event'):
            payload = {key: value for key, value in sample.items() if key != 'mouse'}
            state_events.append({**event, 'payload': json.dumps(payload, ensure_ascii=False)})
        previous = state
        mouse = sample.get('mouse')
        if mouse is None:
            group = None
            continue
        group_state = (*state, mouse['mode'])
        if group is None or group_state != group['state'] or event['timestamp'] - group['started'] >= .25:
            group = {'state': group_state, 'started': event['timestamp'], 'ended': event['timestamp'],
                     'first_event': event['id'], 'last_event': event['id'], 'samples': 0,
                     'keys': sample['keys'], 'buttons': sample['buttons'],
                     'mode': mouse['mode'], 'cursor_visible': sample.get('cursor_visible'),
                     'cursor_start': sample.get('cursor'), 'cursor_end': sample.get('cursor'),
                     'relative_total': [0, 0], 'wheel': 0, 'horizontal_wheel': 0}
            trajectories.append(group)
        group['ended'] = event['timestamp']
        group['last_event'] = event['id']
        group['samples'] += 1
        group['cursor_end'] = sample.get('cursor')
        if mouse['mode'] == 'relative':
            group['relative_total'][0] += mouse['x']
            group['relative_total'][1] += mouse['y']
        group['wheel'] += mouse['wheel']
        group['horizontal_wheel'] += mouse['horizontal_wheel']
    return state_events, [{key: value for key, value in item.items() if key != 'state'} for item in trajectories]
