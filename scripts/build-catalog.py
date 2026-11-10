import unittest
import json
import sys
from pathlib import Path

root = Path(r"C:\Users\jouzu\Documents\Development\Research and Development\payment-gateway-platform")
sys.path.insert(0, str(root))
test_dir = root / "tests" / "e2e"

loader = unittest.TestLoader()
suite = loader.discover(str(test_dir), pattern="test_*.py", top_level_dir=str(root))

catalog = {
    "total_tests": 0,
    "tiers": {"tier1": 0, "tier2": 0, "tier3": 0, "tier4": 0},
    "features": {},
    "tests": []
}

def extract(s):
    for item in s:
        if isinstance(item, unittest.TestSuite):
            extract(item)
        else:
            catalog["total_tests"] += 1
            test_id = item.id()
            name = item._testMethodName
            doc = item._testMethodDoc or ""

            if "tier1" in test_id:
                tier = 1
                catalog["tiers"]["tier1"] += 1
            elif "tier2" in test_id:
                tier = 2
                catalog["tiers"]["tier2"] += 1
            elif "tier3" in test_id:
                tier = 3
                catalog["tiers"]["tier3"] += 1
            elif "tier4" in test_id:
                tier = 4
                catalog["tiers"]["tier4"] += 1
            else:
                tier = 0

            feature = None
            for f_idx in range(1, 26):
                f_key = f"f{f_idx:02d}"
                if f_key in name.lower():
                    feature = f"F{f_idx:02d}"
                    catalog["features"][feature] = catalog["features"].get(feature, 0) + 1
                    break

            catalog["tests"].append({
                "id": test_id,
                "name": name,
                "tier": tier,
                "feature": feature,
                "description": doc.strip().split("\n")[0] if doc else name
            })

extract(suite)

with open(test_dir / "test_catalog.json", "w", encoding="utf-8") as f:
    json.dump(catalog, f, indent=2)

print(f"Catalog written with {catalog['total_tests']} tests: {catalog['tiers']}")
