"""Ограниченная таблица токов TMC5160 для штатного STEP/DIR."""
# Copyright (C) 2026 TreeD contributors
# SPDX-License-Identifier: GPL-3.0-only
import math


REGISTER_NAMES = tuple('MSLUT%d' % i for i in range(8)) + (
    'MSLUTSEL', 'MSLUTSTART')
PHASES = (0., math.pi / 2., math.pi, 3. * math.pi / 2.)
BACKEND_CAPABILITIES = {
    'backend': 'tmc5160_mslut_v2',
    'phase_harmonics': (4,),
    'measurable_harmonics': (2, 4),
    'direction_specific': False,
}
# Штатная таблица TMC5160 из закреплённого Klipper и datasheet, раздел 18.
DEFAULT_TABLE = dict(zip(REGISTER_NAMES, (
    0xAAAAB554, 0x4A9554AA, 0x24492929, 0x10104222,
    0xFBFFFFFF, 0xB5BB777D, 0x49295556, 0x00404222,
    0xFFFF8056, 0x00F70000)))


def make_table(a2=0., p2=0., a4=0., p4=0.):
    """Прежняя модель формы тока; амплитуды в единицах таблицы, не в радианах."""
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
    return _encode_points(points)


def _encode_points(points):
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


def _phase_delta(angle, coefficients):
    return (coefficients['s4'] * math.sin(4. * angle) +
            coefficients['c4'] * math.cos(4. * angle))


def _current(points, index):
    index %= 1024
    if index <= 256:
        return 247 if index == 256 else points[index]
    if index <= 512:
        return points[512 - index]
    if index <= 768:
        return -(247 if index == 768 else points[index - 512])
    return -points[1024 - index]


def phase_table(coefficients, base_table):
    """Спроецировать фазовую модель на MSLUT и отвергнуть её большой остаток."""
    if not isinstance(coefficients, dict):
        raise ValueError('phase_coefficients_invalid')
    if coefficients.get('s2') or coefficients.get('c2'):
        raise ValueError('phase_harmonic_unsupported')
    if set(coefficients) != {'s4', 'c4'} or not all(
            isinstance(v, (int, float)) and math.isfinite(v)
            for v in coefficients.values()):
        raise ValueError('phase_coefficients_invalid')
    if sum(abs(v) for v in coefficients.values()) > .06:
        raise ValueError('phase_magnitude_limit')
    decode_table(base_table)
    if not any(coefficients.values()):
        return dict(base_table), {'rms_error_lsb': 0., 'rms_signal_lsb': 0.}
    points = [round(247. * math.sin(
        index * math.pi / 512. +
        _phase_delta(index * math.pi / 512., coefficients)))
        for index in range(257)]
    if points[0] != 0 or points[-1] != 247 or min(points) < 0 or max(points) > 247:
        raise ValueError('phase_boundary_limit')
    table = _encode_points(points)
    actual = decode_table(table)
    error_sq = signal_sq = 0.
    for index in range(1024):
        angle = index * math.pi / 512.
        delta = _phase_delta(angle, coefficients)
        ideal_a = 247. * math.sin(angle + delta)
        ideal_b = 247. * math.cos(angle + delta)
        error_sq += (_current(actual, index) - ideal_a) ** 2
        error_sq += (_current(actual, index + 256) - ideal_b) ** 2
        signal_sq += (ideal_a - 247. * math.sin(angle)) ** 2
        signal_sq += (ideal_b - 247. * math.cos(angle)) ** 2
    rms_error = math.sqrt(error_sq / 2048.)
    rms_signal = math.sqrt(signal_sq / 2048.)
    if rms_signal < 1. or rms_error > .35 * rms_signal:
        raise ValueError('phase_not_representable_by_mslut')
    return table, {'rms_error_lsb': rms_error,
                   'rms_signal_lsb': rms_signal}


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
