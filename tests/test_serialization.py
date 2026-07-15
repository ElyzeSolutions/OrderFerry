from __future__ import annotations

import json
import math
import unittest
from collections import namedtuple

import numpy as np

from orderferry.serialization import to_jsonable


class SerializationTests(unittest.TestCase):
    def test_namedtuple_and_nested_values_are_json_safe(self):
        Tick = namedtuple("Tick", "symbol bid")

        result = to_jsonable({"tick": Tick("XAUUSD", 3350.25), "raw": b"ok"})

        self.assertEqual(
            result,
            {"tick": {"symbol": "XAUUSD", "bid": 3350.25}, "raw": "ok"},
        )
        json.dumps(result, allow_nan=False)

    def test_non_finite_numbers_become_json_null(self):
        result = to_jsonable([math.nan, math.inf, -math.inf, np.float64("nan")])

        self.assertEqual(result, [None, None, None, None])
        self.assertEqual(json.dumps(result, allow_nan=False), "[null, null, null, null]")

    def test_structured_mt5_array_becomes_records(self):
        rates = np.array(
            [(1_700_000_000, 1.1, 1.2)],
            dtype=[("time", "i8"), ("open", "f8"), ("close", "f8")],
        )

        self.assertEqual(
            to_jsonable(rates),
            [{"time": 1_700_000_000, "open": 1.1, "close": 1.2}],
        )


if __name__ == "__main__":
    unittest.main()
