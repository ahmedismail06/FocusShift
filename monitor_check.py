from screeninfo import get_monitors as _get_monitors


def get_monitors() -> list:
    return list(_get_monitors())


def is_vertical_layout(monitors: list | None = None) -> bool:
    """True when monitors are stacked vertically (y spread exceeds x spread)."""
    mons = monitors if monitors is not None else get_monitors()
    if len(mons) < 2:
        return False
    xs = [m.x for m in mons]
    ys = [m.y for m in mons]
    return (max(ys) - min(ys)) > (max(xs) - min(xs))


def get_monitors_sorted(monitors: list | None = None) -> list:
    mons = monitors if monitors is not None else get_monitors()
    if is_vertical_layout(mons):
        # NSScreen y-up: higher y = physically higher. Sort descending so idx 0 = top monitor.
        return sorted(mons, key=lambda m: m.y, reverse=True)
    return sorted(mons, key=lambda m: m.x)


def is_multi_monitor() -> bool:
    return len(get_monitors()) >= 2


def get_monitor_count() -> int:
    return len(get_monitors())
