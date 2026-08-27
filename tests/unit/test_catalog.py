import tempfile
import unittest
from pathlib import Path

from factory.catalog import CatalogError, descendants, load_catalog, load_image

ROOT = Path(__file__).resolve().parents[2]


class CatalogTests(unittest.TestCase):
    def test_catalog_loads_all_images(self) -> None:
        images = load_catalog(ROOT / "catalog/images")
        self.assertEqual(
            set(images),
            {"ubi9-minimal", "ubi10-minimal", "bitbucket-lts", "confluence-lts", "jira-lts"},
        )

    def test_ubi9_selects_all_atlassian_descendants_but_not_ubi10(self) -> None:
        images = load_catalog(ROOT / "catalog/images")
        self.assertEqual(
            descendants(images, {"ubi9-minimal"}),
            {"ubi9-minimal", "bitbucket-lts", "confluence-lts", "jira-lts"},
        )

    def test_revisions_are_full_commit_ids(self) -> None:
        images = load_catalog(ROOT / "catalog/images")
        self.assertTrue(
            all(len(image.data["source"]["revision"]) == 40 for image in images.values())
        )

    def test_publication_repositories_are_deployment_variables(self) -> None:
        images = load_catalog(ROOT / "catalog/images")
        for image in images.values():
            publication = image.data["publication"]
            self.assertRegex(
                publication["quarantineRepository"],
                r"^\$\{FACTORY_[A-Z0-9_]+_REPOSITORY\}$",
            )
            self.assertRegex(
                publication["releaseRepository"],
                r"^\$\{FACTORY_[A-Z0-9_]+_REPOSITORY\}$",
            )

    def test_invalid_catalog_fails_schema_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "invalid.yaml"
            catalog.write_text("apiVersion: wrong\nkind: HardenedImage\n", encoding="utf-8")
            with self.assertRaises(CatalogError):
                load_image(catalog)
