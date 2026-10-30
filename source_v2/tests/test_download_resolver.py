from __future__ import annotations

import unittest

from smart_launcher.domain.downloads import DownloadSource
from smart_launcher.downloads.resolver import DownloadResolutionError, ModelLinkResolver


class DownloadResolverTests(unittest.TestCase):
    def test_civitai_red_model_version_uses_official_api_and_bearer_header(self) -> None:
        calls = []

        def getter(url, headers):
            calls.append((url, headers))
            return {
                "id": 77,
                "name": "v1",
                "model": {"name": "Example"},
                "downloadUrl": "https://civitai.com/api/download/models/77",
                "files": [
                    {
                        "id": 9,
                        "name": "example.safetensors",
                        "sizeKB": 1024,
                        "primary": True,
                    }
                ],
            }

        candidates = ModelLinkResolver(getter).resolve(
            "https://civitai.red/models/12/example?modelVersionId=77",
            civitai_token="secret-token",
        )

        self.assertEqual(calls[0][0], "https://civitai.com/api/v1/model-versions/77")
        self.assertEqual(calls[0][1]["Authorization"], "Bearer secret-token")
        self.assertNotIn("secret-token", calls[0][0])
        self.assertEqual(candidates[0].source, DownloadSource.CIVITAI)
        self.assertEqual(candidates[0].name, "example.safetensors")
        self.assertEqual(candidates[0].size_bytes, 1024 * 1024)

    def test_civitai_model_page_flattens_files_from_all_versions(self) -> None:
        def getter(_url, _headers):
            return {
                "name": "Model",
                "modelVersions": [
                    {
                        "id": 1,
                        "name": "one",
                        "files": [
                            {
                                "id": 10,
                                "name": "one.safetensors",
                                "downloadUrl": "https://example.invalid/one",
                            }
                        ],
                    },
                    {
                        "id": 2,
                        "name": "two",
                        "files": [
                            {
                                "id": 20,
                                "name": "two.safetensors",
                                "downloadUrl": "https://example.invalid/two",
                            }
                        ],
                    },
                ],
            }

        candidates = ModelLinkResolver(getter).resolve("civitai.com/models/42/model")

        self.assertEqual([item.name for item in candidates], ["one.safetensors", "two.safetensors"])
        self.assertEqual([item.version_name for item in candidates], ["one", "two"])

    def test_hugging_face_blob_link_becomes_resolve_download(self) -> None:
        candidate = ModelLinkResolver().resolve(
            "https://huggingface.co/org/repo/blob/main/models/a model.safetensors"
        )[0]

        self.assertEqual(candidate.source, DownloadSource.HUGGING_FACE)
        self.assertEqual(candidate.name, "a model.safetensors")
        self.assertEqual(
            candidate.url,
            "https://huggingface.co/org/repo/resolve/main/models/a%20model.safetensors",
        )

    def test_hugging_face_repository_page_is_rejected(self) -> None:
        with self.assertRaises(DownloadResolutionError):
            ModelLinkResolver().resolve("https://huggingface.co/org/repo")

    def test_direct_filename_is_sanitized(self) -> None:
        candidate = ModelLinkResolver().resolve("https://example.com/models/file.safetensors")[0]
        self.assertEqual(candidate.source, DownloadSource.DIRECT)
        self.assertEqual(candidate.name, "file.safetensors")


if __name__ == "__main__":
    unittest.main()
