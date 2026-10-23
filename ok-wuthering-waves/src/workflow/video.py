from fractions import Fraction
from pathlib import Path

import av
import numpy as np


class SegmentedVideoWriter:
    def __init__(self, directory, segment_seconds=60.0, bit_rate=4_000_000):
        self.directory = Path(directory)
        self.segment_seconds = segment_seconds
        self.bit_rate = bit_rate
        self.container = None
        self.stream = None
        self.started = None
        self.last_timestamp = None
        self.size = None
        self.segment = 0
        self.frames_written = 0

    def write(self, frame, timestamp):
        if self.last_timestamp is not None and timestamp <= self.last_timestamp:
            raise ValueError('视频帧时间必须递增')
        self.last_timestamp = timestamp
        height, width = frame.shape[:2]
        size = (width - width % 2, height - height % 2)
        if self.container is None or timestamp - self.started >= self.segment_seconds or size != self.size:
            self.close()
            self.size = size
            self.started = timestamp
            path = self.directory / f'video_{self.segment:05d}.mkv'
            self.segment += 1
            self.container = av.open(str(path), mode='w')
            self.stream = self.container.add_stream('libx264', rate=20)
            self.stream.width, self.stream.height = size
            self.stream.pix_fmt = 'yuv420p'
            self.stream.bit_rate = self.bit_rate
            self.stream.time_base = Fraction(1, 1_000_000)
            self.stream.codec_context.time_base = Fraction(1, 1_000_000)
            self.stream.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
            self.container.metadata['capture_start'] = repr(timestamp)
        picture = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame[:size[1], :size[0], :3]), format='bgr24')
        picture.pts = round((timestamp - self.started) * 1_000_000)
        picture.time_base = Fraction(1, 1_000_000)
        for packet in self.stream.encode(picture):
            self.container.mux(packet)
        self.frames_written += 1

    def close(self):
        if self.container is not None:
            try:
                for packet in self.stream.encode():
                    self.container.mux(packet)
            finally:
                self.container.close()
                self.container = None


def read_video(path):
    with av.open(str(path)) as container:
        metadata = {key.casefold(): value for key, value in container.metadata.items()}
        started = float(metadata['capture_start'])
        for frame in container.decode(video=0):
            yield started + float(frame.pts * frame.time_base), frame.to_ndarray(format='bgr24')
