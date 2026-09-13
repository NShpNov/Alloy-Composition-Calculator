"""
I don't want to go into all the mathematical magic happening under the hood.
In short, the packing problem is solved as a mixed-integer linear program (MILP) .
"""

import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


CRUCIBLE_CAPACITY = 4608




def expand_batch_usage(usage):
    """Convert display labels returned by optimizers into batch sizes."""
    inventory = {}
    for component, entries in usage.items():
        inventory[component] = []
        for label, raw_count in entries.items():
            count = int(raw_count)
            if label == "Independent":
                continue
            if label.startswith("Batch "):
                _add_batches(inventory[component], label[6:], count)
            elif label in {"121", "144", "121 whole", "144 whole"}:
                _add_batches(inventory[component], label.split()[0], count)
            elif label.startswith("Piece "):
                _add_batches(inventory[component], label[6:], count)
            elif "->" in label:
                size, pieces = label.split("->", 1)[1].strip().split("x")
                _add_batches(inventory[component], size, count * int(pieces))
    return inventory


def _add_batches(target, size, count):
    target.extend([float(size.strip())] * count)


def pack_batches_into_crucibles(batch_inventory, percentages, capacity=CRUCIBLE_CAPACITY):
    """Find the fewest capacity-limited loads preserving recipe ranges."""
    if capacity <= 0:
        raise ValueError("Crucible capacity must be greater than 0")

    batch_groups = _group_batches(batch_inventory)
    if not batch_groups:
        return []
    if any(size > capacity for size, _, _ in batch_groups):
        raise ValueError("A metal batch exceeds crucible capacity")

    total_amount = sum(size * count for size, _, count in batch_groups)
    minimum_loads = math.ceil(total_amount / capacity)
    for load_count in range(minimum_loads, len(batch_groups) + 1):
        model = _build_packing_model(
            batch_groups, percentages, capacity, load_count, total_amount
        )
        used_offset = model.pop("used_offset")
        result = milp(**model)
        if result.success:
            return _decode_loads(result.x, batch_groups, load_count, used_offset)

    raise ValueError("Cannot form crucible loads that satisfy alloy percentages")


def _group_batches(batch_inventory):
    groups = {}
    for component, sizes in batch_inventory.items():
        for size in sizes:
            key = (float(size), component)
            groups[key] = groups.get(key, 0) + 1
    return [(size, component, count) for (size, component), count in groups.items()]


def _build_packing_model(
    batch_groups, percentages, capacity, load_count, total_amount
):
    group_count = len(batch_groups)
    used_offset = group_count * load_count
    variable_count = used_offset + load_count
    objective = np.zeros(variable_count)
    objective[used_offset:] = 1
    constraints = []
    component_names = list(percentages)
    smallest_batch = min(size for size, _, _ in batch_groups)

    for group_index, (_, _, count) in enumerate(batch_groups):
        row = np.zeros(variable_count)
        start = group_index * load_count
        row[start:start + load_count] = 1
        constraints.append(LinearConstraint(row, count, count))

    for load_index in range(load_count):
        total_row = np.zeros(variable_count)
        component_rows = {
            name: np.zeros(variable_count) for name in component_names
        }
        for group_index, (size, component, _) in enumerate(batch_groups):
            variable = group_index * load_count + load_index
            total_row[variable] = size
            component_rows[component][variable] = size

        used = used_offset + load_index
        capacity_row = total_row.copy()
        capacity_row[used] = -capacity
        constraints.append(LinearConstraint(capacity_row, -np.inf, 0))
        minimum_row = total_row.copy()
        minimum_row[used] = -smallest_batch
        constraints.append(LinearConstraint(minimum_row, 0, np.inf))

        for name, (minimum, maximum) in percentages.items():
            component_row = component_rows[name]
            lower = minimum * total_row - component_row
            upper = component_row - maximum * total_row
            lower[used] = total_amount
            upper[used] = total_amount
            constraints.extend([
                LinearConstraint(lower, -np.inf, total_amount),
                LinearConstraint(upper, -np.inf, total_amount),
            ])

    return {
        "c": objective,
        "integrality": np.ones(variable_count),
        "bounds": Bounds(
            np.zeros(variable_count),
            np.array([
                count
                for _, _, count in batch_groups
                for _ in range(load_count)
            ] + [1] * load_count),
        ),
        "constraints": constraints,
        "options": {"time_limit": 10},
        "used_offset": used_offset,
    }


def _decode_loads(solution, batch_groups, load_count, used_offset):
    loads = []
    for load_index in range(load_count):
        if solution[used_offset + load_index] < 0.5:
            continue
        metals = {}
        total = 0
        for group_index, (size, component, _) in enumerate(batch_groups):
            variable = group_index * load_count + load_index
            count = int(round(solution[variable]))
            if count:
                by_size = metals.setdefault(component, {})
                by_size[size] = by_size.get(size, 0) + count
                total += size * count
        loads.append({"total": total, "metals": metals})
    return loads
