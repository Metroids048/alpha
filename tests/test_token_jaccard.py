import importlib.util
import sys
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "auto_alpha_pipeline_rebuilt_v50.py"
SPEC = importlib.util.spec_from_file_location(
    "auto_alpha_pipeline_rebuilt_v50", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class TokenJaccardTests(unittest.TestCase):
    def test_jaccard_uses_intersection_over_union(self) -> None:
        self.assertAlmostEqual(MODULE._token_jaccard({"a", "b"}, {"b", "c"}), 1 / 3)

    def test_jaccard_returns_zero_for_disjoint_sets(self) -> None:
        self.assertEqual(MODULE._token_jaccard({"a"}, {"b"}), 0.0)


if __name__ == "__main__":
    unittest.main()
