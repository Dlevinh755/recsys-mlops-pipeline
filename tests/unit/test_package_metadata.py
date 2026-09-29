import re
from pathlib import Path
import tomllib
import unittest

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class PackageMetadataTest(unittest.TestCase):
    def test_internal_package_name_and_version_are_well_formed(self) -> None:
        """`libs/CHANGELOG.md` is the source of truth for *which* version is
        current (it grows every phase that touches `libs/`) — this test only
        pins the package name and checks the version is a real semver, so it
        does not need editing every time libs/pyproject.toml bumps."""
        pyproject_path = Path(__file__).parents[2] / "libs" / "pyproject.toml"
        with pyproject_path.open("rb") as file:
            project = tomllib.load(file)["project"]

        self.assertEqual(project["name"], "reco-mlops-libs")
        self.assertRegex(project["version"], _SEMVER)


if __name__ == "__main__":
    unittest.main()
