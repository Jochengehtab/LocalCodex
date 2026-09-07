"""Deliberately incomplete public fixture. Only edit the function named by a task."""


def clamp(value, lower, upper):
    """Clamp inclusively; reject inverted bounds with ValueError."""
    raise NotImplementedError


def parse_ids(value):
    """Parse comma-separated nonnegative ints; ignore whitespace, reject empty items."""
    raise NotImplementedError


def ordered_unique(values):
    """Return unique hashable values preserving first appearance; do not mutate input."""
    raise NotImplementedError


def median(values):
    """Median of a nonempty sequence, preserving input; empty raises ValueError."""
    values.sort()
    return values[len(values) // 2]


def percentage(part, total):
    """Return part / total * 100; a zero total raises ValueError."""
    return part / total


def chunks(values, size):
    """Partition into lists of at most size; size must be positive."""
    return [values[index:index + size] for index in range(0, len(values) - size, size)]
