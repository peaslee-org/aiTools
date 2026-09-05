"""Reconstruction is the stage table's view of colmap+openmvs: one method per tool group."""
from pipeline.reconstruct import Reconstruction

SAVED = "Mesh 'x.ply' saved: 3 vertices, 1 faces (0ms)\n"


class Runner:
    def __init__(self): self.cmds = []
    def run(self, cmd, cwd, tool=None):
        self.cmds.append(cmd); return SAVED


def test_mesh_stages_are_separate_calls(tmp_path):
    r = Runner()
    recon = Reconstruction(r, tmp_path, use_gpu=False)
    dense = tmp_path / "dense"
    ply, faces = recon.reconstruct_mesh(dense)
    assert ply == dense / "scene_dense_mesh.ply" and faces == 1
    refined, faces2 = recon.refine_mesh(dense, ply)
    assert refined == dense / "scene_dense_mesh_refine.ply" and faces2 == 1
    obj = recon.texture(dense, refined, decimate=0.5)
    assert obj == dense / "scene_textured.obj"
    assert [c[0] for c in r.cmds] == ["ReconstructMesh", "RefineMesh", "TextureMesh"]
    assert "--decimate" in r.cmds[2] and not hasattr(recon, "mesh")


# ── dense stage with object masks ─────────────────────────────────────────────
import pytest

import pipeline.reconstruct as recon_mod
from pipeline.colmap import SparseModel
from pipeline.masks import MaskReport


def _stub_masks(monkeypatch, calls):
    def fake_make_masks(session, images_dir, masks_dir, overlays_dir, predict_fn=None):
        calls.append((session, images_dir, masks_dir, overlays_dir))
        masks_dir.mkdir(parents=True, exist_ok=True)
        return MaskReport(masked=3, unmasked=["0002.jpg"])
    monkeypatch.setattr(recon_mod, "make_masks", fake_make_masks)
    monkeypatch.setattr(recon_mod, "load_model", lambda path: f"session:{path}")


def test_dense_without_masks_runs_undistort_interface_densify(tmp_path, monkeypatch):
    calls = []; _stub_masks(monkeypatch, calls)
    r = Runner()
    recon = Reconstruction(r, tmp_path, use_gpu=False, mask_model_path=tmp_path / "m.onnx")
    dense = recon.dense(tmp_path / "images", SparseModel(tmp_path / "sparse" / "0", 5))
    assert dense == tmp_path / "dense"
    assert [c[0] for c in r.cmds] == ["colmap", "InterfaceCOLMAP", "DensifyPointCloud"]
    assert calls == [] and recon.mask_report is None
    assert "--mask-path" not in r.cmds[2]


def test_dense_with_masks_masks_the_undistorted_images_before_densify(tmp_path, monkeypatch):
    calls = []; _stub_masks(monkeypatch, calls)
    r = Runner()
    recon = Reconstruction(r, tmp_path, use_gpu=False, mask_model_path=tmp_path / "m.onnx")
    dense = recon.dense(tmp_path / "images", SparseModel(tmp_path / "sparse" / "0", 5), masks=True)
    assert calls == [(f"session:{tmp_path / 'm.onnx'}", dense / "images", dense / "masks", tmp_path / "overlays")]
    cmd = r.cmds[2]
    assert cmd[0] == "DensifyPointCloud" and cmd[cmd.index("--mask-path") + 1] == str(dense / "masks")
    assert recon.mask_report == MaskReport(masked=3, unmasked=["0002.jpg"])


def test_model_is_loaded_once_per_reconstruction(tmp_path, monkeypatch):
    calls = []; _stub_masks(monkeypatch, calls)
    loads = []
    monkeypatch.setattr(recon_mod, "load_model", lambda path: loads.append(path) or "s")
    recon = Reconstruction(Runner(), tmp_path, use_gpu=False, mask_model_path=tmp_path / "m.onnx")
    model = SparseModel(tmp_path / "sparse" / "0", 5)
    recon.dense(tmp_path / "images", model, masks=True)
    recon.dense(tmp_path / "images", model, masks=True)
    assert loads == [tmp_path / "m.onnx"]


def test_masks_without_a_model_path_is_an_error(tmp_path, monkeypatch):
    calls = []; _stub_masks(monkeypatch, calls)
    recon = Reconstruction(Runner(), tmp_path, use_gpu=False)
    with pytest.raises(RuntimeError, match="MASK_MODEL_PATH"):
        recon.dense(tmp_path / "images", SparseModel(tmp_path / "sparse" / "0", 5), masks=True)
