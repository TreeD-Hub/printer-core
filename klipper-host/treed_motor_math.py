# Геометрия проходов и синхронный анализ вибрации двигателей TreeD.
# Copyright (C) 2026 TreeD contributors
# SPDX-License-Identifier: GPL-3.0-only
import math
import statistics


class MeasurementError(ValueError):
    pass


def motor_component(motor, dx, dy, direction_inverted=False):
    """Командное перемещение названного двигателя CoreXY, не оси X/Y."""
    if motor not in ('stepper_x', 'stepper_y'):
        raise ValueError('motor must be stepper_x or stepper_y')
    distance = dx + dy if motor == 'stepper_x' else dx - dy
    return -distance if direction_inverted else distance


def diagonal(motor, center, half_span, sign):
    if motor not in ('stepper_x', 'stepper_y') or sign not in (-1, 1):
        raise ValueError('invalid motor or direction')
    x, y, z = center
    slope = 1 if motor == 'stepper_x' else -1
    return ((x - sign * half_span, y - sign * slope * half_span, z),
            (x + sign * half_span, y + sign * slope * half_span, z))


def cruise_window(length, speed, accel, min_seconds, settle_seconds=0.30):
    if min(length, speed, accel, min_seconds) <= 0:
        raise ValueError('positive length, speed, accel and window required')
    ramp = speed / accel
    cruise = (length - speed * speed / accel) / speed - 2 * settle_seconds
    if cruise < min_seconds:
        raise MeasurementError('insufficient_cruise_distance')
    return ramp + settle_seconds, length / speed - settle_seconds


def electrical_frequency(motor_speed, rotation_distance, full_steps, gearing=1.):
    if min(abs(motor_speed), rotation_distance, full_steps, gearing) <= 0:
        raise ValueError('invalid motor geometry')
    return abs(motor_speed) * gearing * full_steps / (4. * rotation_distance)


def path_motor_speed(motor, speed, start, end):
    if motor == 'stepper_z':
        return speed
    distance = math.dist(start, end)
    if distance <= 0:
        raise ValueError('zero_length_path')
    return speed * abs(motor_component(motor, end[0] - start[0],
                                       end[1] - start[1])) / distance


def _quality(samples, start, end, frequency):
    window = [s for s in samples if start <= s[0] <= end]
    if len(window) < 40:
        raise MeasurementError('too_few_samples')
    times = [s[0] for s in window]
    deltas = [b - a for a, b in zip(times, times[1:])]
    if min(deltas) <= 0:
        raise MeasurementError('non_monotonic_sample_time')
    period = statistics.median(deltas)
    rate = 1. / period
    missing = sum(max(0, round(delta / period) - 1)
                  for delta in deltas if delta > period * 1.5)
    largest_gap = max(deltas)
    loss_fraction = missing / (len(window) + missing)
    if largest_gap > period * 3.5 or loss_fraction > .01:
        raise MeasurementError(
            'dropped_samples: estimated_missing=%d samples=%d max_gap_ms=%.3f'
            % (missing, len(window), largest_gap * 1000.))
    if frequency * 4. >= rate:
        raise MeasurementError('sensor_bandwidth')
    if (times[-1] - times[0]) * frequency < 12.:
        raise MeasurementError('too_few_periods')
    # ADXL345 full-resolution +/-16g: отсечь отсчёты у предела датчика.
    if any(abs(v) >= 150000. for s in window for v in s[1:4]):
        raise MeasurementError('sensor_saturation')
    return window, rate, missing, largest_gap


def _lockin(samples, frequency, harmonic=1):
    means = [statistics.fmean(s[i] for s in samples) for i in (1, 2, 3)]
    t0 = samples[0][0]
    result = []
    for i, mean in enumerate(means, 1):
        real = imaginary = 0.
        for sample in samples:
            phase = (harmonic * sample[4] if len(sample) > 4 else
                     2. * math.pi * frequency * (sample[0] - t0))
            real += (sample[i] - mean) * math.cos(phase)
            imaginary += (sample[i] - mean) * math.sin(phase)
        result.append((2. * real / len(samples), 2. * imaginary / len(samples)))
    return result


def _magnitude(components):
    return math.sqrt(sum(a * a + b * b for a, b in components))


def measure_harmonics(samples, idle_samples, start, end, base_frequency,
                      harmonics=(2, 4)):
    results = {}
    for harmonic in harmonics:
        frequency = base_frequency * harmonic
        try:
            window, rate, missing, largest_gap = _quality(
                samples, start, end, frequency)
        except MeasurementError as exc:
            if str(exc) not in ('sensor_bandwidth', 'too_few_periods'):
                raise
            results['H%d' % harmonic] = {
                'quality': 'unmeasurable', 'reason': str(exc),
                'frequency_hz': frequency}
            continue
        if any(len(sample) < 5 for sample in window):
            raise MeasurementError('command_phase_missing')
        idle_start = idle_samples[0][0] if idle_samples else 0.
        idle_end = idle_samples[-1][0] if idle_samples else 0.
        idle, _, _, _ = _quality(idle_samples, idle_start, idle_end, frequency)
        components = _lockin(window, frequency, harmonic)
        amplitude = _magnitude(components)
        floor = _magnitude(_lockin(idle, frequency))
        signal_ok = amplitude > max(3. * floor, 1.)
        results['H%d' % harmonic] = {
            'quality': 'valid' if signal_ok else 'insufficient_signal',
            'frequency_hz': frequency, 'amplitude_mm_s2': amplitude,
            'noise_floor_mm_s2': floor, 'sample_rate_hz': rate,
            'estimated_missing_samples': missing,
            'largest_sample_gap_ms': largest_gap * 1000.,
            'samples': len(window), 'periods': (window[-1][0] - window[0][0]) * frequency,
            'command_phase_components_mm_s2': components,
        }
    return results


def compare_verification(baseline, corrected):
    """Сравнить парные повторные проходы, не скрывая отдельное ухудшение."""
    if (len(baseline) != len(corrected) or not baseline or
            len(baseline) % 2):
        return {'accepted': False, 'reason': 'missing_verification_rows'}
    per_repeat = len(baseline) // 2
    motors = ('stepper_x', 'stepper_y')
    totals = {motor: [[0., 0.], [0., 0.], 0, 0.] for motor in motors}
    for index, (base, candidate) in enumerate(zip(baseline, corrected)):
        if any(base[k] != candidate[k] for k in
               ('motor', 'direction', 'speed_mm_s', 'trajectory')):
            return {'accepted': False, 'reason': 'unmatched_passes'}
        if not any(base['harmonics'][harmonic]['quality'] == 'valid'
                   for harmonic in ('H2', 'H4')):
            return {'accepted': False,
                    'reason': 'unmeasurable_verification_condition',
                    'condition': {key: base[key] for key in
                                  ('motor', 'direction', 'speed_mm_s',
                                   'trajectory')}}
        motor = base['motor']
        for harmonic in ('H2', 'H4'):
            first, second = (base['harmonics'][harmonic],
                             candidate['harmonics'][harmonic])
            if first['quality'] != 'valid':
                continue
            if second['quality'] != 'valid':
                return {'accepted': False, 'reason': 'candidate_signal_invalid'}
            start, end = (first['amplitude_mm_s2'],
                          second['amplitude_mm_s2'])
            floor = max(first['noise_floor_mm_s2'],
                        second['noise_floor_mm_s2'])
            if end - start > max(3. * floor, .15 * start):
                return {'accepted': False,
                        'reason': 'individual_condition_regressed'}
            repeat = index // per_repeat
            totals[motor][0][repeat] += start
            totals[motor][1][repeat] += end
            totals[motor][2] += 1
            totals[motor][3] += floor * floor
    if any(t[2] < 8 for t in totals.values()):
        return {'accepted': False, 'reason': 'insufficient_verified_signal'}
    improved = []
    for motor, (base, candidate, count, floor_sq) in totals.items():
        base_sum, candidate_sum = sum(base), sum(candidate)
        error = max(2. * (abs(base[0] - base[1]) +
                          abs(candidate[0] - candidate[1])),
                    3. * math.sqrt(floor_sq))
        if candidate_sum - base_sum > max(.05 * base_sum, error):
            return {'accepted': False, 'reason': 'motor_total_regressed'}
        if (all(a > b for a, b in zip(base, candidate)) and
                base_sum - candidate_sum > max(.10 * base_sum, error)):
            improved.append(motor)
    if not improved:
        return {'accepted': False, 'reason': 'improvement_below_error_margin'}
    return {'accepted': True, 'reason': '',
            'motor_amplitude_sums': totals,
            'improved_motors': improved}
