"""A sibling module the entry function needs but never imports directly at the
call site -- it is reached only through the package __init__/core import chain."""


def weight_count() -> int:
    return 7
