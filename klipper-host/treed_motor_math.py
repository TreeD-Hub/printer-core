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
    if frequency and frequency * 4. >= rate:
        raise MeasurementError('sensor_bandwidth')
    if frequency and (times[-1] - times[0]) * frequency < 12.:
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


def baseline_summary(rows, harmonic):
    """Повторяемость исходной таблицы для двух направлений одного режима."""
    directions = {}
    for row in rows:
        entry = row['harmonics']['H%d' % harmonic]
        directions.setdefault(row['direction'], []).append((entry, row))
    if set(directions) != {'positive', 'negative'}:
        return None
    summary = {}
    for direction, group in directions.items():
        if len(group) < 3 or any(entry['quality'] != 'valid'
                                 for entry, _ in group):
            return None
        amplitudes = [entry['amplitude_mm_s2'] for entry, _ in group]
        mean = statistics.fmean(amplitudes)
        stddev = statistics.stdev(amplitudes)
        floor = statistics.fmean(entry['noise_floor_mm_s2']
                                 for entry, _ in group)
        if stddev > max(.15 * mean, 3. * floor):
            return None
        summary[direction] = {
            'mean_mm_s2': mean, 'stddev_mm_s2': stddev,
            'cv': stddev / mean if mean else 0.,
            'noise_floor_mm_s2': floor,
            'retry_count': sum(row.get('sample_gap_retries', 0)
                               for _, row in group)}
    return summary


def compare_verification(baseline, corrected):
    """Парные проходы: разброс базы, отдельные регрессии и 95% граница выигрыша."""
    if len(baseline) != len(corrected) or not baseline:
        return {'accepted': False, 'reason': 'missing_verification_rows'}
    critical = {('isolated_diagonal', max(
        (row['speed_mm_s'] for row in baseline
         if row['trajectory'] == 'isolated_diagonal'), default=0.)): 'H2',
                ('joint_x', min(
                    (row['speed_mm_s'] for row in baseline
                     if row['trajectory'] == 'joint_x'), default=0.)): 'H4'}
    groups = {}
    for base, candidate in zip(baseline, corrected):
        if any(base[k] != candidate[k] for k in
               ('motor', 'direction', 'speed_mm_s', 'trajectory')):
            return {'accepted': False, 'reason': 'unmatched_passes'}
        condition = {key: base[key] for key in
                     ('motor', 'direction', 'speed_mm_s', 'trajectory')}
        if not any(base['harmonics'][harmonic]['quality'] == 'valid'
                   for harmonic in ('H2', 'H4')):
            return {'accepted': False,
                    'reason': 'unmeasurable_verification_condition',
                    'condition': condition}
        for harmonic in ('H2', 'H4'):
            first, second = (base['harmonics'][harmonic],
                             candidate['harmonics'][harmonic])
            if first['quality'] != 'valid':
                if critical.get((base['trajectory'], base['speed_mm_s'])) == harmonic:
                    return {'accepted': False,
                            'reason': 'unmeasurable_verification_condition',
                            'condition': dict(condition, harmonic=harmonic)}
                continue
            if second['quality'] != 'valid':
                return {'accepted': False, 'reason': 'candidate_signal_invalid',
                        'condition': dict(condition, harmonic=harmonic)}
            start, end = (first['amplitude_mm_s2'],
                          second['amplitude_mm_s2'])
            floor = max(first['noise_floor_mm_s2'],
                        second['noise_floor_mm_s2'])
            if end - start > max(3. * floor, .15 * start):
                return {'accepted': False,
                        'reason': 'individual_condition_regressed',
                        'condition': dict(condition, harmonic=harmonic),
                        'baseline_mm_s2': start, 'candidate_mm_s2': end}
            key = (base['motor'], base['direction'], base['speed_mm_s'],
                   base['trajectory'], harmonic)
            groups.setdefault(key, []).append((start, end, floor,
                                                base.get('sample_gap_retries', 0)))
    if not groups or any(len(rows) < 3 for rows in groups.values()):
        return {'accepted': False, 'reason': 'insufficient_verified_signal'}
    summaries = []
    motor_groups = {'stepper_x': [], 'stepper_y': []}
    for key, rows in groups.items():
        starts = [row[0] for row in rows]
        differences = [row[1] - row[0] for row in rows]
        base_mean = statistics.fmean(starts)
        base_std = statistics.stdev(starts)
        floor = statistics.fmean(row[2] for row in rows)
        if base_std > max(.15 * base_mean, 3. * floor):
            return {'accepted': False, 'reason': 'unstable_baseline',
                    'condition': key}
        diff_mean = statistics.fmean(differences)
        # Трёх повторов достаточно только для консервативной оценки (t_0.975,2).
        bound = 4.303 * statistics.stdev(differences) / math.sqrt(len(starts))
        if diff_mean - bound > max(.05 * base_mean, 3. * floor):
            return {'accepted': False, 'reason': 'condition_regressed',
                    'condition': key}
        motor_groups[key[0]].append(rows)
        summaries.append({'motor': key[0], 'direction': key[1],
                          'speed_mm_s': key[2], 'trajectory': key[3],
                          'harmonic': key[4], 'baseline_mean_mm_s2': base_mean,
                          'baseline_stddev_mm_s2': base_std,
                          'baseline_cv': base_std / base_mean if base_mean else 0.,
                          'noise_floor_mm_s2': floor,
                          'retry_count': sum(row[3] for row in rows),
                          'candidate_mean_mm_s2': base_mean + diff_mean,
                          'paired_difference_ci95_half_width_mm_s2': bound})
    if any(sum(len(group) for group in groups) < 8
           for groups in motor_groups.values()):
        return {'accepted': False, 'reason': 'insufficient_verified_signal'}
    improved = []
    for motor, groups in motor_groups.items():
        if len({len(group) for group in groups}) != 1:
            return {'accepted': False, 'reason': 'missing_verification_rows'}
        starts = [sum(group[i][0] for group in groups)
                  for i in range(len(groups[0]))]
        differences = [sum(group[i][1] - group[i][0] for group in groups)
                       for i in range(len(groups[0]))]
        baseline_mean = statistics.fmean(starts)
        floor = math.sqrt(sum(statistics.fmean(row[2] for row in group) ** 2
                              for group in groups))
        change = statistics.fmean(differences)
        bound = 4.303 * statistics.stdev(differences) / math.sqrt(len(starts))
        if change - bound > max(.05 * baseline_mean, 3. * floor):
            return {'accepted': False, 'reason': 'motor_total_regressed'}
        if -change - bound > max(.10 * baseline_mean, 3. * floor):
            improved.append(motor)
    if not improved:
        return {'accepted': False, 'reason': 'improvement_below_error_margin'}
    return {'accepted': True, 'reason': '',
            'conditions': summaries,
            'improved_motors': improved}


# Блок 1: одинаковая сегментированная окружность для парных проходов.
def circle_points(center, radius, clockwise, segments=128):
    if radius <= 0 or segments < 32 or segments % 16:
        raise ValueError('invalid_circle_geometry')
    x, y, z = center
    direction = -1 if clockwise else 1
    points = [(x + radius, y, z)]
    for index in range(1, segments):
        angle = direction * 2. * math.pi * index / segments
        points.append((x + radius * math.cos(angle),
                       y + radius * math.sin(angle), z))
    points.append(points[0])
    return points


def circle_sector(x, y, center):
    return min(15, int((math.atan2(y - center[1], x - center[0]) %
                        (2. * math.pi)) * 8. / math.pi))


def circle_motor_velocities(speed, angle, clockwise):
    sign = -1 if clockwise else 1
    vx = -sign * speed * math.sin(angle)
    vy = sign * speed * math.cos(angle)
    return {'stepper_x': vx + vy, 'stepper_y': vx - vy}


# Блок 2: оценка вибрации по фазе командных шагов при переменной скорости.
def circle_metrics(samples, max_frequencies, sector=False):
    if sector:
        window = samples
        deltas = [b[0] - a[0] for a, b in zip(window, window[1:])]
        period = statistics.median(deltas)
        rate, missing = 1. / period, 0
        adjacent = [(a, b) for a, b in zip(window, window[1:])
                    if b[0] - a[0] <= 1.5 * period]
        gap = max(b[0] - a[0] for a, b in adjacent)
    else:
        window, rate, missing, gap = _quality(
            samples, samples[0][0], samples[-1][0], 0.)
        adjacent = list(zip(window, window[1:]))
    means = [statistics.fmean(row[i] for row in window) for i in (1, 2, 3)]
    powers = [sum((row[i] - means[i - 1]) ** 2 for i in (1, 2, 3))
              for row in window]
    rms = math.sqrt(statistics.fmean(powers))
    distance = sum(math.hypot(right[6] - left[6], right[7] - left[7])
                   for left, right in adjacent)
    duration = sum(right[0] - left[0] for left, right in adjacent)
    harmonics = {}
    for motor, phase_index in (('stepper_x', 4), ('stepper_y', 5)):
        phases = [row[phase_index] for row in window]
        cycles = (max(phases) - min(phases)) / (2. * math.pi)
        for harmonic in (2, 4):
            name = '%s_H%d' % (motor, harmonic)
            if max_frequencies[motor] * harmonic * 4. >= rate or cycles * harmonic < 12.:
                harmonics[name] = {'quality': 'unmeasurable',
                                   'reason': ('sensor_bandwidth' if
                                              max_frequencies[motor] * harmonic * 4. >= rate
                                              else 'too_few_phase_cycles')}
                continue
            phased = [row[:4] + (row[phase_index],) for row in window]
            harmonics[name] = {
                'quality': 'valid',
                'amplitude_mm_s2': _magnitude(_lockin(phased, 0., harmonic)),
                'command_phase_cycles': cycles}
    return {'samples': len(window), 'sample_rate_hz': rate,
            'estimated_missing_samples': missing,
            'largest_sample_gap_ms': gap * 1000.,
            'duration_s': duration, 'actual_speed_mm_s': distance / duration,
            'rms_accel_mm_s2': rms,
            'peak_accel_mm_s2': math.sqrt(max(powers)),
            'vibration_energy_mm2_s4': statistics.fmean(powers),
            'harmonics': harmonics}


def circle_analysis(samples, center, radius, max_frequencies, clockwise):
    if not samples:
        raise MeasurementError('too_few_samples')
    # Один контроль качества на полный непрерывный захват; сектора его наследуют.
    _quality(samples, samples[0][0], samples[-1][0], 0.)
    sectors = [[] for _ in range(16)]
    for sample in samples:
        if abs(math.hypot(sample[6] - center[0], sample[7] - center[1]) - radius) > 2.:
            raise MeasurementError('circle_tracking_out_of_bounds')
        sectors[circle_sector(sample[6], sample[7], center)].append(sample)
    total = circle_metrics(samples, max_frequencies)
    results = []
    for sector in sectors:
        if len(sector) < 40:
            raise MeasurementError('too_few_sector_samples')
        local = {}
        for motor in max_frequencies:
            ratio = max(abs(circle_motor_velocities(1., math.atan2(
                row[7] - center[1], row[6] - center[0]), clockwise)[motor])
                for row in sector) / math.sqrt(2.)
            local[motor] = max_frequencies[motor] * ratio
        results.append(circle_metrics(sector, local, sector=True))
    # Усреднение комплексной амплитуды по полному кругу скрывало бы
    # противоположные направления вращения; агрегируем мощность секторов.
    for name in total['harmonics']:
        measurable = [row['harmonics'][name]['amplitude_mm_s2']
                      for row in results
                      if row['harmonics'][name]['quality'] == 'valid']
        if measurable:
            total['harmonics'][name] = {
                'quality': 'valid', 'aggregation': 'sector_rms',
                'amplitude_mm_s2': math.sqrt(statistics.fmean(
                    value * value for value in measurable)),
                'measurable_sectors': len(measurable)}
    return {'total': total, 'sectors': results}


# Блок 3: парная разность с консервативной границей повторяемости.
def circle_compare(pairs):
    if not pairs:
        raise ValueError('missing_circle_pairs')
    def values(row):
        result = {key: row[key] for key in (
            'rms_accel_mm_s2', 'peak_accel_mm_s2',
            'vibration_energy_mm2_s4')}
        result.update({name: entry['amplitude_mm_s2']
                       for name, entry in row['harmonics'].items()
                       if entry['quality'] == 'valid'})
        return result

    output = {}
    for key in set.intersection(*(set(values(off)) & set(values(on))
                                  for off, on in pairs)):
        before = [values(off)[key] for off, _ in pairs]
        after = [values(on)[key] for _, on in pairs]
        deltas = [b - a for a, b in zip(before, after)]
        mean = statistics.fmean(deltas)
        base = statistics.fmean(before)
        bound = (4.303 * statistics.stdev(deltas) / math.sqrt(len(deltas))
                 if len(deltas) >= 3 else None)
        repeatability = (max(statistics.stdev(before), statistics.stdev(after))
                         if len(deltas) >= 2 else None)
        output[key] = {
            'off_mean': base, 'on_mean': statistics.fmean(after),
            'absolute_delta': mean,
            'relative_delta_percent': 100. * mean / base if base else None,
            'paired_ci95_half_width': bound,
            'repeatability_stddev': repeatability,
            'verdict': ('no_statistically_meaningful_change' if bound is None or
                        abs(mean) <= max(bound, repeatability) else
                        'vibration_reduced' if mean < 0 else 'vibration_increased')}
    return output
