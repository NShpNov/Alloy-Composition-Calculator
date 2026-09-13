"""
Optimizes alloy composition using mixed-integer linear programming (MILP).
Handles component ratios, batch size constraints, optional batch splitting,
and absolute limits to maximize or meet a target alloy amount.
"""
import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from logic.composition import calculate_alloy, get_components, recipe_ranges


def calculate_max_composition_amount(
    alloys,
    alloy_name,
    absolute_limits,
    batch_limits,
    minimum_amount=None,
    return_usage=False,
    priorities=None,
):
    """Optimize an alloy made from batches."""
    components = get_components(alloys, alloy_name)
    ranges = recipe_ranges(components)
    names = list(components)
    model = _build_composition_model(
        names, batch_limits, absolute_limits or {}, minimum_amount
    )
    model.add_recipe_constraints(ranges, absolute_limits or {}, minimum_amount)

    primary_objective = model.total_row if minimum_amount is not None else -model.total_row
    result = _solve_model(
        model,
        primary_objective,
        "Recipe cannot be produced with the selected batches.",
    )
    priorities = _validate_priorities(names, priorities)
    if priorities is not None:
        total = model.total_row @ result.x
        target_contributions, _ = calculate_alloy(
            alloys,
            alloy_name,
            total,
            priorities,
            absolute_limits,
        )
        model = _build_composition_model(
            names, batch_limits, absolute_limits or {}, minimum_amount
        )
        deviation_variables = model.add_priority_deviations(target_contributions)
        balance_variables = model.add_balance_deviations(target_contributions)
        model.add_recipe_constraints(ranges, absolute_limits or {}, minimum_amount)
        model.constraints.append(LinearConstraint(model.total_row, total, total))
        preference_objective = np.zeros(len(model.variables))
        preference_objective[deviation_variables] = 1
        preference_objective[balance_variables] = total + 1
        result = _solve_model(
            model,
            preference_objective,
            "Recipe cannot be optimized for the selected priorities.",
        )

    contributions = {
        name: round(max(0, float(row @ result.x)), 0)
        for name, row in zip(names, model.component_rows)
    }
    amount = round(sum(contributions.values()), 0)
    if not return_usage:
        return amount, contributions
    return amount, contributions, model.usage(result.x)


def _build_composition_model(names, batch_limits, absolute_limits, minimum_amount):
    model = _CompositionModel(names)
    for name in names:
        model.add_component(name, batch_limits.get(name, {}), absolute_limits, minimum_amount)
    return model


def _solve_model(model, objective, error_prefix):
    result = milp(
        c=objective,
        integrality=np.array(model.integrality),
        bounds=Bounds(np.zeros(len(model.upper_bounds)), np.array(model.upper_bounds)),
        constraints=model.constraints,
        options={"time_limit": 10},
    )
    if not result.success:
        raise ValueError(f"{error_prefix}")
    return result


def _validate_priorities(names, priorities):
    if priorities is None:
        return None
    unknown = set(priorities) - set(names)
    if unknown:
        raise ValueError("Priorities specified for unknown components: " + ", ".join(sorted(unknown)))
    validated = {}
    for name in names:
        priority = priorities.get(name, 0.0)
        if not isinstance(priority, (int, float)) or not -1 <= priority <= 1:
            raise ValueError(f"{name}'s priority must be between -1.0 and 1.0")
        validated[name] = float(priority)
    return validated


class _CompositionModel:
    def __init__(self, names):
        self.names = names
        self.variables = []
        self.component_entries = {name: [] for name in names}
        self.usage_entries = {name: [] for name in names}
        self.constraints = []
        self.upper_bounds = []
        self.integrality = []
        self.component_rows = []
        self.total_row = None

    def add_variable(self, name, coefficient, upper, usage_label=None):
        index = len(self.variables)
        self.variables.append(name)
        self.component_entries[name].append((index, coefficient))
        self.upper_bounds.append(float(upper))
        self.integrality.append(1)
        if usage_label:
            self.usage_entries[name].append((usage_label, index))
        return index

    def add_auxiliary_variable(self, upper):
        index = len(self.variables)
        self.variables.append(None)
        self.upper_bounds.append(float(upper))
        self.integrality.append(0)
        return index

    def add_priority_deviations(self, target_contributions):
        deviation_variables = []
        for name, target in target_contributions.items():
            deviation = self.add_auxiliary_variable(target)
            deviation_variables.append(deviation)
            component_entries = self.component_entries[name]
            self.constraints.extend([
                (component_entries + [(deviation, -1)], -np.inf, target),
                ([(index, -coefficient) for index, coefficient in component_entries]
                 + [(deviation, -1)], -np.inf, -target),
            ])
        return deviation_variables

    def add_balance_deviations(self, target_contributions):
        balance_variables = []
        names = list(target_contributions)
        for first_index, first_name in enumerate(names):
            for second_name in names[first_index + 1:]:
                if target_contributions[first_name] != target_contributions[second_name]:
                    continue
                balance = self.add_auxiliary_variable(sum(target_contributions.values()))
                balance_variables.append(balance)
                first_entries = self.component_entries[first_name]
                second_entries = self.component_entries[second_name]
                difference = first_entries + [
                    (index, -coefficient) for index, coefficient in second_entries
                ]
                self.constraints.extend([
                    (difference + [(balance, -1)], -np.inf, 0),
                    ([(index, -coefficient) for index, coefficient in difference]
                     + [(balance, -1)], -np.inf, 0),
                ])
        return balance_variables

    def add_component(self, name, raw_limits, absolute_limits, minimum_amount):
        limits = raw_limits if isinstance(raw_limits, dict) else {}
        count_121 = limits.get("121", 0)
        count_144 = limits.get("144", 0)
        if count_144 is None:
            count_144 = _unlimited_144_count(absolute_limits, minimum_amount)
        _validate_batch_counts(name, count_121, count_144)

        if limits.get("split", False):
            self._add_split_batches(name, count_121, count_144)
        else:
            self.add_variable(name, 121, count_121, "121")
            self.add_variable(name, 144, count_144, "144")

    def _add_split_batches(self, name, count_121, count_144):
        whole_121 = self.add_variable(name, 121, count_121, "121 whole")
        whole_144 = self.add_variable(name, 144, count_144, "144 whole")
        split_121_13 = self.add_variable(name, 0, count_121)
        split_121_20 = self.add_variable(name, 0, count_121)
        split_144_16 = self.add_variable(name, 0, count_144)
        split_144_36 = self.add_variable(name, 0, count_144)
        pieces = [
            (13, count_121 * 9, split_121_13, 9),
            (20, count_121 * 4, split_121_20, 4),
            (16, count_144 * 9, split_144_16, 9),
            (36, count_144 * 4, split_144_36, 4),
        ]
        for size, limit, source, pieces_per_batch in pieces:
            piece = self.add_variable(name, size, limit, f"Piece {size:g}")
            self.constraints.append(([(piece, 1), (source, -pieces_per_batch)], -np.inf, 0))
        self.constraints.extend([
            ([(split_121_13, 1), (split_121_20, 1), (whole_121, 1)], 0, count_121),
            ([(split_144_16, 1), (split_144_36, 1), (whole_144, 1)], 0, count_144),
        ])

    def add_recipe_constraints(self, ranges, absolute_limits, minimum_amount):
        self._finish_rows()
        for index, name in enumerate(self.names):
            minimum, maximum = ranges[name]
            self.constraints.extend([
                (minimum * self.total_row - self.component_rows[index], -np.inf, 0),
                (self.component_rows[index] - maximum * self.total_row, -np.inf, 0),
            ])
            limits = absolute_limits.get(name, {})
            if not isinstance(limits, dict):
                raise ValueError(f"Absolute limits for {name} must be a dictionary")
            if "min" in limits:
                self.constraints.append((self.component_rows[index], limits["min"], np.inf))
            if "max" in limits:
                self.constraints.append((self.component_rows[index], -np.inf, limits["max"]))
        if minimum_amount is not None:
            if not isinstance(minimum_amount, (int, float)) or minimum_amount <= 0:
                raise ValueError("Target amount must be greater than 0")
            self.constraints.append((self.total_row, minimum_amount, np.inf))
        variable_count = len(self.variables)
        self.constraints = [
            _linear_constraint(*item, variable_count)
            for item in self.constraints
        ]

    def _finish_rows(self):
        size = len(self.variables)
        self.component_rows = []
        for name in self.names:
            row = np.zeros(size)
            for index, coefficient in self.component_entries[name]:
                row[index] = coefficient
            self.component_rows.append(row)
        self.total_row = sum(self.component_rows, np.zeros(size))

    def usage(self, solution):
        result = {name: {} for name in self.names}
        for name in self.names:
            for label, index in self.usage_entries[name]:
                count = int(round(solution[index]))
                if count:
                    result[name][label] = count
        return result


def _linear_constraint(entries, low, high, variable_count):
    row = (
        entries
        if isinstance(entries, np.ndarray)
        else _entries_to_row(entries, variable_count)
    )
    return LinearConstraint(row, low, high)


def _entries_to_row(entries, variable_count):
    row = np.zeros(variable_count)
    for index, coefficient in entries:
        row[index] = coefficient
    return row


def _validate_batch_counts(name, count_121, count_144):
    if any(
        not isinstance(value, (int, float)) or value < 0 or value != int(value)
        for value in (count_121, count_144)
    ):
        raise ValueError(f"Batch counts for {name} must be non-negative integers")


def _unlimited_144_count(absolute_limits, minimum_amount):
    finite_max = [
        value.get("max")
        for value in absolute_limits.values()
        if isinstance(value, dict) and value.get("max") is not None
    ]
    if minimum_amount is None and not finite_max:
        raise ValueError("Maximum amount is unbounded: enter a target or absolute maximum")
    if minimum_amount is not None:
        return math.ceil(minimum_amount / 144) + 10
    return math.ceil(sum(finite_max) / 144) + 1
