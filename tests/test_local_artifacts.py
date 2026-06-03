from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from ap_automation.services.local_artifacts import LocalArtifactStore


class LocalArtifactStoreTests(unittest.TestCase):
    def test_extraction_snapshot_serializes_decimal_property_lookup_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = LocalArtifactStore(root)

            artifact_path = store.write_extraction_snapshot(
                "run-1",
                {
                    "property_lookup": {
                        "returned_payload": [
                            {
                                "property_code": "HW1",
                                "state_score": Decimal("1.0"),
                                "score": Decimal("0.875"),
                            }
                        ]
                    }
                },
            )

            payload = json.loads((root / artifact_path).read_text(encoding="utf-8"))

            returned = payload["property_lookup"]["returned_payload"][0]
            self.assertEqual(returned["state_score"], 1)
            self.assertEqual(returned["score"], 0.875)

