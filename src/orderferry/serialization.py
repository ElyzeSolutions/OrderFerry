"""Conversion of MetaTrader and NumPy values into JSON-safe Python values."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def to_jsonable(value: Any) -> Any:
    """Recursively convert MetaTrader and NumPy values to JSON-safe values."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (int, str, bool)):
        return value

    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    if isinstance(value, np.void):
        names = value.dtype.names
        if names:
            return {name: to_jsonable(value[name]) for name in names}
        try:
            return {
                "time": int(value[0]),
                "open": float(value[1]),
                "high": float(value[2]),
                "low": float(value[3]),
                "close": float(value[4]),
                "tick_volume": int(value[5]),
                "spread": int(value[6]),
                "real_volume": int(value[7]),
            }
        except (IndexError, TypeError, ValueError):
            return str(value)

    as_dict = getattr(value, "_asdict", None)
    if callable(as_dict):
        return {key: to_jsonable(item) for key, item in as_dict().items()}

    if isinstance(value, np.ndarray) and value.dtype.names:
        return [to_jsonable(row) for row in value]

    if isinstance(value, np.ndarray):
        return to_jsonable(value.tolist())

    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]

    return str(value)
