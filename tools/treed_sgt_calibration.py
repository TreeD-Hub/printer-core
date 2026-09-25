"""Изолированный подбор SGT по принципу Prusa SensitivityCalibration.

Контур: чистый алгоритм; не вызывает G-code и не меняет конфиги/драйверы.
probe_pair(sgt) предоставляет пару измеренных точек в ОДНОЙ системе координат.
Скорость, ток и ускорение остаются ответственностью измерительного исполнителя.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Optional, Tuple


# Блок 1: Контракт измерений. Координаты не являются измерением силы контакта.
class ProbeAborted(RuntimeError):
    """Исполнитель потерял достоверность движения; продолжать поиск нельзя."""


class EarlyTrigger(RuntimeError):
    """Первая точка заведомо ранняя: кандидат отклонён без второй пробы."""


@dataclass(frozen=True)
class ProbePair:
    first_mm: float
    second_mm: float


@dataclass(frozen=True)
class CalibrationConfig:
    sgt_min: int
    sgt_max: int
    reference_mm: float
    position_tolerance_mm: float
    home_direction: int
    samples_per_sgt: int = 4
    error_budget_mm: float = 0.6
    score_epsilon_mm: float = 1e-9

    def __post_init__(self) -> None:
        for name in ('sgt_min', 'sgt_max', 'home_direction', 'samples_per_sgt'):
            if type(getattr(self, name)) is not int:
                raise ValueError('%s должен быть целым числом' % name)
        if not -64 <= self.sgt_min <= self.sgt_max <= 63:
            raise ValueError('Нужен явный диапазон -64 <= min <= max <= 63')
        if self.home_direction not in (-1, 1):
            raise ValueError('home_direction должен быть -1 либо 1')
        if not 2 <= self.samples_per_sgt <= 16:
            raise ValueError('samples_per_sgt должен быть в диапазоне 2..16')
        for name in ('reference_mm', 'position_tolerance_mm',
                     'error_budget_mm', 'score_epsilon_mm'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError('%s должен быть конечным числом' % name)
            if not math.isfinite(value):
                raise ValueError('%s должен быть конечным числом' % name)
        if self.position_tolerance_mm <= 0 or self.error_budget_mm <= 0:
            raise ValueError('Допуск позиции и бюджет ошибки должны быть положительными')
        if self.score_epsilon_mm < 0:
            raise ValueError('score_epsilon_mm не может быть отрицательным')


@dataclass(frozen=True)
class CandidateResult:
    sgt: int
    pairs: Tuple[ProbePair, ...]
    status: str
    total_abs_error_mm: Optional[float]

    @property
    def mean_abs_error_mm(self) -> Optional[float]:
        if self.total_abs_error_mm is None or not self.pairs:
            return None
        return self.total_abs_error_mm / len(self.pairs)


@dataclass(frozen=True)
class CalibrationResult:
    status: str
    recommended_sgt: Optional[int]
    best_interval: Optional[Tuple[int, int]]
    candidates: Tuple[CandidateResult, ...]
    reason: str = ''


# Блок 2: Оценка одной настройки. Ранний отсев экономит оставшиеся пробы.
def _measure_candidate(
    config: CalibrationConfig,
    sgt: int,
    probe_pair: Callable[[int], ProbePair],
    cancelled: Callable[[], bool],
) -> CandidateResult:
    pairs = []
    errors = []
    for _ in range(config.samples_per_sgt):
        if cancelled():
            raise ProbeAborted('cancelled')
        try:
            pair = probe_pair(sgt)
        except EarlyTrigger:
            if cancelled():
                raise ProbeAborted('cancelled')
            return CandidateResult(sgt, tuple(pairs), 'early_trigger', None)
        except ProbeAborted:
            raise
        except Exception as exc:
            # Не превращать потерю связи/исключение исполнителя в плохой SGT
            # с последующим продолжением движения. Причина остаётся в result.
            raise ProbeAborted('probe_error: %s: %s' %
                               (type(exc).__name__, exc)) from exc
        if cancelled():
            raise ProbeAborted('cancelled')
        if not isinstance(pair, ProbePair):
            raise ProbeAborted('invalid_measurement_type')
        for value in (pair.first_mm, pair.second_mm):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                raise ProbeAborted('non_finite_measurement')
        pairs.append(pair)
        signed_offsets = [
            (value - config.reference_mm) * config.home_direction
            for value in (pair.first_mm, pair.second_mm)
        ]
        if not all(math.isfinite(value) for value in signed_offsets):
            raise ProbeAborted('measurement_overflow')
        if any(value > config.position_tolerance_mm for value in signed_offsets):
            # После позднего срабатывания могли быть пропущены шаги.
            # Старый удачный кандидат НЕ разрешает завершить эту сессию успехом.
            raise ProbeAborted('late_trigger_or_invalid_reference')
        if any(value < -config.position_tolerance_mm for value in signed_offsets):
            return CandidateResult(sgt, tuple(pairs), 'early_trigger', None)
        error = abs(pair.second_mm - pair.first_mm)
        if not math.isfinite(error):
            raise ProbeAborted('measurement_overflow')
        errors.append(error)
        try:
            total = math.fsum(errors)
        except OverflowError as exc:
            raise ProbeAborted('measurement_overflow') from exc
        if total > config.error_budget_mm:
            return CandidateResult(sgt, tuple(pairs), 'unstable', total)
    return CandidateResult(sgt, tuple(pairs), 'valid', math.fsum(errors))


# Блок 3: Как у Prusa — минимальная средняя ошибка, затем середина лучшего
# непрерывного участка. При равных раздельных участках выбран правый.
def _select(
    candidates: Tuple[CandidateResult, ...], epsilon: float,
) -> Tuple[Optional[int], Optional[Tuple[int, int]]]:
    good = {item.sgt: item for item in candidates if item.status == 'valid'}
    if not good:
        return None, None
    best = min(item.mean_abs_error_mm for item in good.values())
    equal_best = {
        sgt for sgt, item in good.items()
        if abs(item.mean_abs_error_mm - best) <= epsilon
    }
    right = max(equal_best)
    left = right
    while left - 1 in equal_best:
        left -= 1
    # Для чётного числа значений — правая из двух центральных точек.
    return left + (right - left + 1) // 2, (left, right)


def calibrate_sgt(
    config: CalibrationConfig,
    probe_pair: Callable[[int], ProbePair],
    cancelled: Optional[Callable[[], bool]] = None,
) -> CalibrationResult:
    """Автоматический поиск SGT, без ручной классификации проходов.

    reference_mm — неизменная опора этой сессии. probe_pair обязана возвращать
    точки срабатывания ДО переназначения home-координаты; G28-координаты после
    сброса не подходят. Пропуск DIAG, потеря связи, шагов или опоры должны
    немедленно вызывать ProbeAborted. Ток/SGT восстанавливает внешний исполнитель
    в finally; этот модуль не владеет аппаратным состоянием.
    """
    if not callable(probe_pair):
        raise TypeError('probe_pair должен быть вызываемым')
    if cancelled is not None and not callable(cancelled):
        raise TypeError('cancelled должен быть вызываемым')
    is_cancelled = cancelled if cancelled is not None else lambda: False
    candidates = []
    # C++ делит целые с усечением к нулю, в отличие от Python // для отрицательных.
    middle = math.trunc((config.sgt_min + config.sgt_max) / 2)

    def measure(sgt: int) -> CandidateResult:
        result = _measure_candidate(config, sgt, probe_pair, is_cancelled)
        candidates.append(result)
        return result

    def have_good() -> bool:
        return any(item.status == 'valid' for item in candidates)

    try:
        # Середина -> верхняя половина; после найденного рабочего участка
        # первая плохая точка заканчивает исследование этого направления.
        for sgt in range(middle, config.sgt_max + 1):
            item = measure(sgt)
            if item.status != 'valid' and have_good():
                break
        # Если середина плохая, но выше уже есть результат, нижнюю половину
        # Prusa не исследует. Это локальный поиск, не доказательство глобального.
        if candidates[0].status == 'valid' or not have_good():
            for sgt in range(middle - 1, config.sgt_min - 1, -1):
                item = measure(sgt)
                if item.status != 'valid' and have_good():
                    break
        if is_cancelled():
            raise ProbeAborted('cancelled')
    except ProbeAborted as exc:
        return CalibrationResult('aborted', None, None, tuple(candidates), str(exc))

    selected, interval = _select(tuple(candidates), config.score_epsilon_mm)
    if selected is None:
        return CalibrationResult('no_reliable_result', None, None,
                                 tuple(candidates), 'all_tested_candidates_rejected')
    return CalibrationResult('recommended', selected, interval, tuple(candidates))
