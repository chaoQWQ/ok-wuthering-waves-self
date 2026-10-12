import re

from PIL import Image


class BaselinePositionDetector:
    CROP_BOX_1080P = (0, 1050, 240, 1080)

    def preprocess(self, cropped):
        return cropped.resize((cropped.width * 2, cropped.height * 2), Image.LANCZOS)

    @staticmethod
    def try_parse_3nums(text):
        if not text:
            return None
        parts = text.split(",")
        if len(parts) == 3:
            for part in parts:
                if not part.lstrip("-").isdigit():
                    return None
            return text
        return None

    @staticmethod
    def _text_process(raw_text):
        text = ''.join(c if c.isalnum() or c == '-' else ',' for c in raw_text)
        if text.count(',') >= 2:
            text = ''.join(c for c in text if c in set('0123456789,-'))
        else:
            text = ''.join(c if c.isdigit() or c == '-' else ',' for c in text)
        return text.strip(',')

    @staticmethod
    def _rule1_strip_space_20(text):
        index = text.rfind(' ')
        if index != -1 and re.match(r'^20', text[index + 1:]):
            text = text[:index]
        return text

    @staticmethod
    def _rule2_strip_202_or_20(text):
        parts = [part for part in text.split(',') if part]
        if not parts:
            return text
        last = parts[-1]
        if len(''.join(c for c in last if c.isdigit())) <= 4:
            return text
        index = last.rfind('202')
        if index != -1:
            parts[-1] = last[:index]
            return ','.join(parts)
        index = last.rfind('20')
        if index != -1:
            parts[-1] = last[:index]
            return ','.join(parts)
        return text

    @staticmethod
    def _rule3_strip_before_first_minus(text):
        first_comma = text.find(',')
        segment, rest = (text, '') if first_comma == -1 else (text[:first_comma], text[first_comma:])
        minus_index = segment.find('-')
        if minus_index > 0:
            segment = segment[minus_index:]
        return segment + rest

    @staticmethod
    def _enumerate_3nums(result):
        parts = [part for part in result.split(',') if part]
        if len(parts) == 3:
            candidate = ','.join(parts)
            if BaselinePositionDetector.try_parse_3nums(candidate):
                return candidate
        groups = re.findall(r'-?\d+', result)
        if len(groups) >= 3:
            for i in range(len(groups) - 2):
                for j in range(i + 1, len(groups) - 1):
                    for k in range(j + 1, len(groups)):
                        candidate = f'{groups[i]},{groups[j]},{groups[k]}'
                        if BaselinePositionDetector.try_parse_3nums(candidate):
                            return candidate
            candidate = ','.join(groups[:3])
            if BaselinePositionDetector.try_parse_3nums(candidate):
                return candidate
        return ''

    def postprocess(self, texts):
        if not texts:
            return ''
        joined = self._rule1_strip_space_20(''.join(texts))
        joined = self._rule2_strip_202_or_20(joined)
        joined = self._rule3_strip_before_first_minus(joined)
        text = self._text_process(joined)
        return self._enumerate_3nums(text) if text else ''

    def _calc_crop_box(self, frame_h, frame_w):
        scale = frame_h / 1080
        return tuple(int(value * scale) for value in self.CROP_BOX_1080P)
