"""
Calculates alloy compositions.
Handles component percentage ranges, absolute limits, priorities.
"""
def recipe_ranges(components):
    ranges = {}
    for name, data in components.items():
        minimum = float(data["min"]) / 100
        maximum = float(data["max"]) / 100
        if minimum < 0 or maximum < 0 or minimum > maximum:
            raise ValueError(f"Invalid percentage limits for {name}")
        ranges[name] = (minimum, maximum)

    if sum(item[0] for item in ranges.values()) > 1:
        raise ValueError("Recipe impossible: sum of minimum percentages exceeds 100%")
    if sum(item[1] for item in ranges.values()) < 1:
        raise ValueError("Recipe impossible: sum of maximum percentages is below 100%")
    return ranges

def get_components(alloys, alloy_name):
    if alloy_name not in alloys:
        raise ValueError(f"Alloy not found: {alloy_name}")
    return alloys[alloy_name]["components"]


def _effective_limits(data, absolute_limits, amount, name):
    minimum = float(data["min"]) / 100
    maximum = float(data["max"]) / 100
    limits = absolute_limits.get(name, {})
    if not isinstance(limits, dict):
        raise ValueError(f"Absolute limits for {name} must be a dictionary")

    absolute_min = float(limits.get("min", 0)) / amount
    absolute_max = float(limits.get("max", amount)) / amount
    if absolute_min < 0 or absolute_max < 0:
        raise ValueError(f"Absolute limits for {name} cannot be negative")
    if absolute_min > absolute_max:
        raise ValueError(f"Absolute minimum for {name} exceeds its maximum")

    minimum = max(minimum, absolute_min)
    maximum = min(maximum, absolute_max)
    if minimum > maximum:
        raise ValueError(f"Absolute limits for {name} contradict percentage limits")
    return minimum, maximum


def _normalize(values):
    active = set(values)
    while active:
        difference = 1 - sum(item["value"] for item in values.values())
        if abs(difference) < 1e-12:
            return

        active_total = sum(values[name]["value"] for name in active)
        if active_total <= 0:
            raise RuntimeError("Failed to normalize the composition.")

        factor = (active_total + difference) / active_total
        fixed = False
        for name in tuple(active):
            item = values[name]
            candidate = item["value"] * factor
            if candidate < item["min"]:
                item["value"] = item["min"]
                active.remove(name)
                fixed = True
            elif candidate > item["max"]:
                item["value"] = item["max"]
                active.remove(name)
                fixed = True
            else:
                item["value"] = candidate
        if not fixed:
            return

    raise RuntimeError("Failed to find a valid composition.")


def calculate_alloy(alloys, alloy_name, amount, priorities, absolute_limits=None):
    """Choose component amounts while respecting recipe and absolute limits."""
    if amount <= 0:
        raise ValueError("Amount must be greater than 0")

    components = get_components(alloys, alloy_name)
    absolute_limits = absolute_limits or {}
    unknown = set(absolute_limits) - set(components)
    if unknown:
        raise ValueError(
            "Absolute limits specified for unknown components: "
            + ", ".join(sorted(unknown))
        )

    values = {}
    for name, data in components.items():
        minimum, maximum = _effective_limits(
            data, absolute_limits, amount, name
        )
        priority = priorities.get(name, 0.0)
        if not -1 <= priority <= 1:
            raise ValueError(f"{name}'s priority must be between -1.0 and 1.0")
        values[name] = {
            "min": minimum,
            "max": maximum,
            "value": minimum + (maximum - minimum) * (priority + 1) / 2,
        }

    minimum_total = sum(item["min"] for item in values.values())
    maximum_total = sum(item["max"] for item in values.values())
    if minimum_total > 1:
        raise ValueError(f"Recipe impossible: sum of mins {minimum_total * 100:.2f}%")
    if maximum_total < 1:
        raise ValueError(f"Recipe impossible: sum of maxes {maximum_total * 100:.2f}%")

    _normalize(values)
    total = sum(item["value"] for item in values.values())
    if abs(total - 1) > 1e-9:
        raise RuntimeError(f"Failed to normalize the composition: sum = {total * 100:.10f}%")

    contributions = {
        name: round(amount * item["value"], 0)
        for name, item in values.items()
    }
    percentages = [round(item["value"] * 100, 0) for item in values.values()]
    return contributions, percentages
