from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from smart_launcher.domain.downloads import DownloadCandidate, DownloadSource


class DownloadResolutionError(RuntimeError):
    pass


JsonGetter = Callable[[str, dict[str, str]], object]


class ModelLinkResolver:
    CIVITAI_HOSTS = frozenset(("civitai.com", "www.civitai.com", "civitai.red", "www.civitai.red"))
    HUGGING_FACE_HOSTS = frozenset(("huggingface.co", "www.huggingface.co"))

    def __init__(self, json_getter: JsonGetter | None = None) -> None:
        self._json_getter = json_getter or self._get_json

    def resolve(
        self,
        value: str,
        *,
        civitai_token: str | None = None,
        huggingface_token: str | None = None,
    ) -> tuple[DownloadCandidate, ...]:
        url = self._normalized_url(value)
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").casefold()
        if host in self.CIVITAI_HOSTS:
            return self._resolve_civitai(parsed, civitai_token)
        if host in self.HUGGING_FACE_HOSTS:
            return (self._resolve_hugging_face(parsed),)
        return (self._resolve_direct(parsed),)

    def _resolve_civitai(
        self, parsed: urllib.parse.ParseResult, token: str | None
    ) -> tuple[DownloadCandidate, ...]:
        query = urllib.parse.parse_qs(parsed.query)
        version_id = self._first_integer(query.get("modelVersionId", ()))
        path_version = re.search(r"/api/(?:v1/)?(?:download/models|model-versions)/(\d+)", parsed.path)
        if version_id is None and path_version:
            version_id = int(path_version.group(1))
        headers = self._auth_headers(token)
        if version_id is not None:
            payload = self._json_getter(
                f"https://civitai.com/api/v1/model-versions/{version_id}", headers
            )
            return self._civitai_version_candidates(payload)

        match = re.search(r"/models/(\d+)", parsed.path)
        if match is None:
            raise DownloadResolutionError("Ссылка Civitai не содержит ID модели или версии.")
        payload = self._json_getter(
            f"https://civitai.com/api/v1/models/{int(match.group(1))}", headers
        )
        if not isinstance(payload, dict):
            raise DownloadResolutionError("Civitai вернул неожиданный ответ модели.")
        versions = payload.get("modelVersions")
        if not isinstance(versions, list):
            raise DownloadResolutionError("У модели Civitai нет доступных версий.")
        candidates: list[DownloadCandidate] = []
        for version in versions:
            candidates.extend(self._civitai_version_candidates(version, payload.get("name")))
        if not candidates:
            raise DownloadResolutionError("У модели Civitai нет доступных файлов.")
        return tuple(candidates)

    def _civitai_version_candidates(
        self, payload: object, fallback_model_name: object = None
    ) -> tuple[DownloadCandidate, ...]:
        if not isinstance(payload, dict):
            raise DownloadResolutionError("Civitai вернул неожиданный ответ версии.")
        version_id = self._integer(payload.get("id"))
        model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
        model_name = str(model.get("name") or fallback_model_name or "Civitai model")
        version_name = str(payload.get("name") or f"Version {version_id or ''}").strip()
        version_url = payload.get("downloadUrl")
        files = payload.get("files") if isinstance(payload.get("files"), list) else []
        candidates: list[DownloadCandidate] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            primary = bool(item.get("primary"))
            download_url = item.get("downloadUrl") or (version_url if primary else None)
            name = item.get("name")
            if not isinstance(download_url, str) or not isinstance(name, str):
                continue
            size_kb = item.get("sizeKB", item.get("sizeKb"))
            try:
                size_bytes = int(float(size_kb) * 1024) if size_kb is not None else None
            except (TypeError, ValueError):
                size_bytes = None
            candidates.append(
                DownloadCandidate(
                    DownloadSource.CIVITAI,
                    self._safe_name(name),
                    download_url,
                    size_bytes,
                    model_name,
                    version_name,
                    self._integer(item.get("id")),
                )
            )
        if not candidates and isinstance(version_url, str) and version_id is not None:
            candidates.append(
                DownloadCandidate(
                    DownloadSource.CIVITAI,
                    f"civitai-{version_id}.safetensors",
                    version_url,
                    model_name=model_name,
                    version_name=version_name,
                )
            )
        if not candidates:
            raise DownloadResolutionError("В выбранной версии Civitai нет доступных файлов.")
        return tuple(candidates)

    def _resolve_hugging_face(self, parsed: urllib.parse.ParseResult) -> DownloadCandidate:
        parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) < 5 or parts[2] not in ("blob", "resolve"):
            raise DownloadResolutionError(
                "Нужна ссылка Hugging Face на конкретный файл, а не на страницу репозитория."
            )
        owner, repository, _mode, revision, *file_parts = parts
        if not file_parts:
            raise DownloadResolutionError("Ссылка Hugging Face не содержит имя файла.")
        encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in file_parts)
        url = f"https://huggingface.co/{owner}/{repository}/resolve/{urllib.parse.quote(revision, safe='')}/{encoded_path}"
        return DownloadCandidate(
            DownloadSource.HUGGING_FACE,
            self._safe_name(file_parts[-1]),
            url,
            model_name=f"{owner}/{repository}",
            version_name=revision,
        )

    def _resolve_direct(self, parsed: urllib.parse.ParseResult) -> DownloadCandidate:
        name = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
        if not name:
            raise DownloadResolutionError("Прямая ссылка не содержит имя файла.")
        return DownloadCandidate(
            DownloadSource.DIRECT,
            self._safe_name(name),
            urllib.parse.urlunparse(parsed),
        )

    @staticmethod
    def _normalized_url(value: str) -> str:
        url = value.strip()
        if not url:
            raise DownloadResolutionError("Введите ссылку на модель.")
        if "://" not in url:
            url = "https://" + url
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise DownloadResolutionError("Поддерживаются только корректные HTTP/HTTPS-ссылки.")
        return url

    @staticmethod
    def _auth_headers(token: str | None) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "SmartLauncherComfyUI2/2.0"}
        if token and token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
        return headers

    @staticmethod
    def _safe_name(value: str) -> str:
        name = value.replace("\\", "_").replace("/", "_").strip(" .")
        if not name or name in (".", ".."):
            raise DownloadResolutionError("Сервер вернул недопустимое имя файла.")
        return name

    @staticmethod
    def _first_integer(values) -> int | None:
        for value in values:
            parsed = ModelLinkResolver._integer(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _integer(value: object) -> int | None:
        try:
            parsed = int(str(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _get_json(url: str, headers: dict[str, str]) -> object:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise DownloadResolutionError(
                    "Civitai отклонил запрос. Проверьте API-токен и доступ к модели."
                ) from exc
            raise DownloadResolutionError(f"Сервер вернул HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise DownloadResolutionError(f"Не удалось получить сведения о модели: {exc}") from exc
