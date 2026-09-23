"""Ограниченная таблица токов TMC5160 для штатного STEP/DIR."""
# Copyright (C) 2026 TreeD contributors
# SPDX-License-Identifier: GPL-3.0-only
import math


REGISTER_NAMES = tuple('MSLUT%d' % i for i in range(8)) + (
    'MSLUTSEL', 'MSLUTSTART')
PHASES = (0., math.pi / 2., math.pi, 3. * math.pi / 2.)
# Штатная таблица TMC5160 из закреплённого Klipper и datasheet, раздел 18.
DEFAULT_TABLE = dict(zip(REGISTER_NAMES, (
    0xAAAAB554, 0x4A9554AA, 0x24492929, 0x10104222,
    0xFBFFFFFF, 0xB5BB777D, 0x49295556, 0x00404222,
    0xFFFF8056, 0x00F70000)))


def make_table(a2=0., p2=0., a4=0., p4=0.):
    """Вернуть регистры MSLUT; амплитуды заданы в единицах таблицы токов."""
    values = (a2, p2, a4, p4)
    if not all(math.isfinite(v) for v in values):
        raise ValueError('non_finite_wave_coefficient')
    if max(abs(a2), abs(a4)) > 6. or abs(a2) + abs(a4) > 8.:
        raise ValueError('wave_amplitude_limit')
    points = []
    for index in range(257):
        angle = index * math.pi / 512.
        envelope = math.sin(2. * angle) ** 2
        correction = envelope * (a2 * math.sin(2. * angle + p2)
                                 + a4 * math.sin(4. * angle + p4))
        points.append(round(247. * math.sin(angle) + correction))
    if points[0] != 0 or points[-1] != 247 or min(points) < 0 or max(points) > 247:
        raise ValueError('wave_boundary_limit')
    steps = [right - left for left, right in zip(points, points[1:])]
    if any(step not in (0, 1, 2) for step in steps):
        raise ValueError('wave_slope_limit')
    segments = []
    for index, step in enumerate(steps):
        required = 2 if step == 2 else 1 if step == 0 else None
        if not segments:
            first = next((2 if v == 2 else 1 for v in steps if v != 1), 1)
            segments.append((0, first))
        elif required is not None and required != segments[-1][1]:
            segments.append((index, required))
    if len(segments) > 4:
        raise ValueError('wave_not_encodable')
    widths = [width for _, width in segments]
    borders = [start for start, _ in segments[1:]]
    while len(widths) < 4:
        widths.append(widths[-1])
        borders.append(255)
    if any(not 0 < border <= 255 for border in borders):
        raise ValueError('wave_segment_bounds')
    bits = []
    segment = 0
    for index, step in enumerate(steps):
        if segment < len(segments) - 1 and index == segments[segment + 1][0]:
            segment += 1
        bits.append(step - (segments[segment][1] - 1))
    registers = {}
    for word in range(8):
        registers['MSLUT%d' % word] = sum(
            bit << offset for offset, bit in enumerate(bits[word * 32:(word + 1) * 32]))
    registers['MSLUTSEL'] = sum(border << (8 * (i + 1))
                                 for i, border in enumerate(borders)) | sum(
                                     width << (2 * i)
                                     for i, width in enumerate(widths))
    registers['MSLUTSTART'] = 247 << 16
    return registers


def _decode_points(registers):
    if set(registers) != set(REGISTER_NAMES):
        raise ValueError('wave_register_set')
    if any(not isinstance(v, int) or v < 0 or v > 0xffffffff
           for v in registers.values()):
        raise ValueError('wave_register_value')
    selector = registers['MSLUTSEL']
    borders = (0, (selector >> 8) & 255, (selector >> 16) & 255,
               (selector >> 24) & 255, 256)
    widths = tuple((selector >> (2 * i)) & 3 for i in range(4))
    if not (0 < borders[1] <= borders[2] <= borders[3] < 256):
        raise ValueError('wave_segment_bounds')
    start = registers['MSLUTSTART']
    if start & 0xff != 0 or (start >> 16) & 0xff != 247:
        raise ValueError('wave_start_values')
    points = [0]
    for index in range(256):
        segment = max(i for i in range(4) if borders[i] <= index)
        bit = (registers['MSLUT%d' % (index // 32)] >> (index % 32)) & 1
        points.append(points[-1] + widths[segment] - 1 + bit)
    return points


def decode_table(registers):
    """Декодирование для проверки сохранённого профиля до записи в TMC."""
    points = _decode_points(registers)
    # У штатной таблицы сумма последнего дифференциального бита даёт 248;
    # на позиции 256 драйвер берёт START_SIN90=247, а не эту сумму.
    stock = registers == DEFAULT_TABLE
    if (points[-1] != (248 if stock else 247) or min(points) < 0 or
            max(points) > (248 if stock else 247)):
        raise ValueError('wave_shape_limit')
    return points


def transition_scores(before, after):
    """Максимальное изменение тока на остановленной фазе при записи MSLUT."""
    current = dict(before)
    snapshots = [_decode_points(current)]
    for register in REGISTER_NAMES:
        current[register] = after[register]
        snapshots.append(_decode_points(current))
    baseline = snapshots[0]
    return tuple(max(max(abs(points[q] - baseline[q]),
                         abs(points[256 - q] - baseline[256 - q]))
                     for points in snapshots)
                 for q in range(256))
