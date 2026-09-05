"""OpenMVS: dense → mesh → (refine) → texture. Every step runs inside the COLMAP dense workspace."""
from pathlib import Path
import re
from pipeline.runner import StageError

_SAVED = re.compile(r"Mesh '[^']*' saved: (\d+) vertices, (\d+) faces")


def mesh_faces(output: str, tool: str = "ReconstructMesh") -> int:
    """Face count from the last `Mesh '…' saved: V vertices, F faces` line OpenMVS prints."""
    matches = _SAVED.findall(output)
    if not matches:
        raise StageError(tool, "could not read face count")
    return int(matches[-1][1])


def interface(runner, dense: Path) -> Path:
    out = dense / "scene.mvs"
    runner.run(["InterfaceCOLMAP", "-i", str(dense), "-o", str(out), "-w", str(dense)], cwd=dense, tool="InterfaceCOLMAP")
    return out


def _cuda_device(use_gpu: bool) -> list[str]:
    """OpenMVS: -1 = best GPU, -2 = CPU. Passed to all four tools: three default to -1 and
    initialise CUDA at startup (a crash without a usable driver), RefineMesh defaults to -2."""
    return ["--cuda-device", "-1" if use_gpu else "-2"]


def densify(runner, dense: Path, scene: Path, use_gpu: bool = True, mask_path: Path | None = None) -> Path:
    out = dense / "scene_dense.mvs"
    cmd = ["DensifyPointCloud", str(scene), "-w", str(dense), "-o", str(out), "--resolution-level", "2"]
    if mask_path is not None:
        # <image stem>.mask.png under mask_path; pixels equal to the label are skipped by the
        # depth-map estimator (masks are nearest-resized to the depth-map size). 0 = background.
        cmd += ["--mask-path", str(mask_path), "--ignore-mask-label", "0"]
    runner.run([*cmd, *_cuda_device(use_gpu)], cwd=dense, tool="DensifyPointCloud")
    return out


def reconstruct_mesh(runner, dense: Path, scene_dense: Path, use_gpu: bool = True) -> tuple[Path, int]:
    out = dense / "scene_dense_mesh.mvs"
    output = runner.run(["ReconstructMesh", str(scene_dense), "-w", str(dense), "-o", str(out), *_cuda_device(use_gpu)],
                        cwd=dense, tool="ReconstructMesh")
    return dense / "scene_dense_mesh.ply", mesh_faces(output, "ReconstructMesh")


def refine_mesh(runner, dense: Path, scene_dense: Path, mesh_ply: Path, use_gpu: bool = True) -> tuple[Path, int]:
    out = dense / "scene_dense_mesh_refine.mvs"
    output = runner.run(["RefineMesh", str(scene_dense), "-m", str(mesh_ply), "-w", str(dense), "-o", str(out),
                         *_cuda_device(use_gpu)], cwd=dense, tool="RefineMesh")
    return dense / "scene_dense_mesh_refine.ply", mesh_faces(output, "RefineMesh")


def texture_mesh(runner, dense: Path, scene_dense: Path, mesh_ply: Path, use_gpu: bool = True,
                 decimate: float | None = None) -> Path:
    out = dense / "scene_textured.mvs"
    # Both seam-leveling passes are ON. Stock v2.4.0 blackened every leveled face — its sampler
    # refactor passed the float interpolation type as cv::Mat::at's pixel type, so leveling read
    # 8-bit source images as raw floats — and the image cherry-picks upstream's fix
    # (openmvs-v2.4.0-seam-leveling.patch; root cause + measurements in docs/TODO.md history,
    # 2026-08-31). The flags are explicit so an upstream default change can't flip them silently.
    cmd = ["TextureMesh", str(scene_dense), "-m", str(mesh_ply), "-w", str(dense), "-o", str(out),
           "--export-type", "obj", "--global-seam-leveling", "1", "--local-seam-leveling", "1"]
    if decimate is not None:
        decimate = max(decimate, 0.001)   # OpenMVS rejects/ignores a ratio that rounds to 0.000
        cmd += ["--decimate", f"{decimate:.3f}"]   # OpenMVS decimates the input surface before texturing
    runner.run([*cmd, *_cuda_device(use_gpu)], cwd=dense, tool="TextureMesh")
    return dense / "scene_textured.obj"
