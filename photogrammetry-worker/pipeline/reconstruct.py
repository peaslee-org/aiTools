"""Reconstruction stages: one method per tool group; the handler decides refine and decimation."""
from pathlib import Path

from pipeline import colmap, openmvs
from pipeline.colmap import SparseModel
from pipeline.masks import MaskReport, load_model, make_masks


class Reconstruction:
    def __init__(self, runner, work: Path, use_gpu: bool, mask_model_path: Path | None = None):
        self._r = runner
        self._work = work
        self._gpu = use_gpu
        self._mask_model_path = mask_model_path
        self._mask_session = None
        self.mask_report: MaskReport | None = None

    def sfm(self, images: Path) -> SparseModel:
        return colmap.sparse_reconstruct(self._r, self._work, images, self._gpu)

    def dense(self, images: Path, model: SparseModel, masks: bool = False) -> Path:
        """undistort → (masks over the undistorted images) → InterfaceCOLMAP → DensifyPointCloud.
        Masks are made from the undistorted images so they line up with what OpenMVS reads."""
        dense = colmap.undistort(self._r, self._work, images, model)
        mask_path = None
        if masks:
            mask_path = dense / "masks"
            self.mask_report = make_masks(self._session(), dense / "images", mask_path, self._work / "overlays")
        scene = openmvs.interface(self._r, dense)
        openmvs.densify(self._r, dense, scene, self._gpu, mask_path=mask_path)
        return dense

    def _session(self):
        if self._mask_session is None:
            if self._mask_model_path is None:
                raise RuntimeError("background removal requested but MASK_MODEL_PATH is not set")
            self._mask_session = load_model(self._mask_model_path)
        return self._mask_session

    def reconstruct_mesh(self, dense: Path) -> tuple[Path, int]:
        return openmvs.reconstruct_mesh(self._r, dense, dense / "scene_dense.mvs", self._gpu)

    def refine_mesh(self, dense: Path, mesh_ply: Path) -> tuple[Path, int]:
        return openmvs.refine_mesh(self._r, dense, dense / "scene_dense.mvs", mesh_ply, self._gpu)

    def texture(self, dense: Path, mesh_ply: Path, decimate: float | None = None) -> Path:
        return openmvs.texture_mesh(self._r, dense, dense / "scene_dense.mvs", mesh_ply, self._gpu, decimate=decimate)
