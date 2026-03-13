import mimetypes
import os
from pathlib import Path

from paste.fileapp import FileApp
from webob import exc

from galaxy.exceptions import ItemAccessibilityException, RequestParameterInvalidException
from galaxy.managers.hdas import HDAManager
from galaxy.web.framework.decorators import expose
from galaxy.webapps.base.controller import BaseUIController


class WebatlasZarrController(BaseUIController):
    hda_manager: HDAManager

    def __init__(self, app):
        super().__init__(app)
        self.hda_manager = app[HDAManager]

    def _resolve_hda(self, trans, encoded_dataset_id):
        try:
            decoded_id = trans.security.decode_id(encoded_dataset_id)
            hda = self.hda_manager.get_accessible(
                decoded_id,
                trans.user,
                current_history=trans.history,
            )
        except Exception:
            raise ItemAccessibilityException("Dataset not accessible")
        return hda

    def _safe_join(self, root: str, subpath: str) -> str:
        root_path = Path(root).resolve()
        target_path = (root_path / subpath).resolve()
        if target_path != root_path and root_path not in target_path.parents:
            raise RequestParameterInvalidException("Invalid subpath")
        return str(target_path)

    def _candidate_roots(self, hda):
        candidates = []

        extra_files_path = getattr(hda, "extra_files_path", None)
        if extra_files_path:
            candidates.append(extra_files_path)

        metadata = getattr(hda, "metadata", None)
        store_root = getattr(metadata, "store_root", None) if metadata else None
        if extra_files_path and store_root:
            candidates.append(os.path.join(extra_files_path, store_root))

        hda_file_name = getattr(hda, "file_name", None)
        if hda_file_name:
            candidates.append(hda_file_name)

        dataset = getattr(hda, "dataset", None)
        if dataset is not None:
            dataset_file_name = getattr(dataset, "file_name", None)
            if dataset_file_name:
                candidates.append(dataset_file_name)

            dataset_file_path = getattr(dataset, "file_path", None)
            if dataset_file_path:
                candidates.append(dataset_file_path)

        seen = set()
        ordered = []
        for c in candidates:
            if c and c not in seen:
                ordered.append(c)
                seen.add(c)
        return ordered

    def _resolve_root(self, hda):
        candidates = self._candidate_roots(hda)

        for root in candidates:
            if not os.path.exists(root):
                continue

            if os.path.isdir(root) and os.path.exists(os.path.join(root, ".zgroup")):
                return root

            if os.path.isdir(root):
                try:
                    children = [os.path.join(root, x) for x in os.listdir(root)]
                except Exception:
                    children = []

                for child in children:
                    if os.path.isdir(child) and os.path.exists(os.path.join(child, ".zgroup")):
                        return child

        raise RequestParameterInvalidException("Could not locate Zarr root for dataset")

    def _guess_content_type(self, target: str) -> str:
        if target.endswith((".zgroup", ".zattrs", ".zarray", ".zmetadata")):
            return "application/json"

        content_type, _ = mimetypes.guess_type(target)
        return content_type or "application/octet-stream"

    @expose
    def serve(self, trans, dataset_id, path_info="", **kwd):
        subpath = path_info.lstrip("/")

        hda = self._resolve_hda(trans, dataset_id)
        root = self._resolve_root(hda)

        if not os.path.isdir(root):
            raise RequestParameterInvalidException("Dataset is not a directory-backed Zarr store")

        target = self._safe_join(root, subpath)

        if not os.path.exists(target):
            raise exc.HTTPNotFound("Requested Zarr path does not exist")

        if os.path.isdir(target):
            raise RequestParameterInvalidException("Directory listing is not supported")

        return FileApp(target, content_type=self._guess_content_type(target))