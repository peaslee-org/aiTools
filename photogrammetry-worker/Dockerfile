# Stage 1: compile OpenMVS (CUDA) on the same Ubuntu/CUDA line as the COLMAP image.
FROM nvidia/cuda:12.9.1-devel-ubuntu24.04 AS build
ARG OPENMVS_TAG=v2.4.0
RUN apt-get update && apt-get install -y --no-install-recommends \
      git cmake build-essential pkg-config ca-certificates curl xz-utils libeigen3-dev libnanoflann-dev libboost-iostreams-dev \
      libboost-program-options-dev libboost-system-dev libboost-serialization-dev libopencv-dev \
      libglu1-mesa-dev libglew-dev libpng-dev libjpeg-dev libtiff-dev libgmp-dev libmpfr-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
# CGAL 6.0.1, header-only, from the release tarball (libs + CMake config; ~5 MB, installs in seconds).
# v2.4.0 includes CGAL 6 names (AABB_traits_3.h, AABB_triangle_primitive_3.h); noble's libcgal-dev is
# 5.6 and must not be installed here or find_package could pick it up. Matches upstream's own build.
ARG CGAL_VERSION=6.0.1
RUN curl -fsSL "https://github.com/CGAL/cgal/releases/download/v${CGAL_VERSION}/CGAL-${CGAL_VERSION}-library.tar.xz" | tar -xJ \
 && cmake -S "CGAL-${CGAL_VERSION}" -B cgal-build -DCMAKE_BUILD_TYPE=Release \
 && cmake --install cgal-build \
 && rm -rf "CGAL-${CGAL_VERSION}" cgal-build
RUN git clone --depth 1 https://github.com/cdcseacave/VCG.git vcglib \
 && git clone --depth 1 --branch ${OPENMVS_TAG} https://github.com/cdcseacave/openMVS.git openMVS
# v2.4.0 writes .jxl via cv::IMWRITE_JPEGXL_QUALITY unguarded (OpenCV >= 4.11); noble ships 4.6.0 in
# both stages, and upstream's own ubuntu:24.04 Dockerfile has the same break. The patch turns JXL
# support off (libs/IO/CMakeLists.txt) and guards the one write-path branch (libs/Common/Types.inl).
# The worker never reads or writes .jxl. Regenerate the patch if OPENMVS_TAG changes.
# The seam-leveling patch is upstream commit eeedab7 ("texture: fix corner case in global seam
# leveling logic", 2026-02-27, first commit after v2.4.0 to touch this code): v2.4.0's sampler
# refactor made TImage::sample pass the float interpolation type as cv::Mat::at's pixel type, so
# seam leveling read 8-bit source images as raw floats and wrote ~0 into every face (black faces,
# stray saturated texels). Verified on the sample scan 2026-08-31; drop the patch when OPENMVS_TAG
# moves past a release containing eeedab7.
COPY photogrammetry-worker/openmvs-v2.4.0-noble.patch photogrammetry-worker/openmvs-v2.4.0-seam-leveling.patch /src/
RUN git -C openMVS apply --verbose /src/openmvs-v2.4.0-noble.patch \
 && git -C openMVS apply --verbose --whitespace=nowarn /src/openmvs-v2.4.0-seam-leveling.patch
# USE_PYTHON=OFF: v2.4.0 defaults the Python bindings ON, which makes FIND_PACKAGE(Python3 ... REQUIRED)
# fail on a builder without python3-dev (first CI build, 2026-08-27). The worker calls the CLI tools
# only. VIEWER/TESTS off: no GUI in a worker, and both shorten the ~30 min build.
# INSTALL_PREFIX at configure time: OpenMVS bakes absolute install dirs (<prefix>/bin/OpenMVS,
# <prefix>/lib/OpenMVS) when it configures, so `cmake --install --prefix` is ignored (attempt 6 put
# everything under /usr/local). Libraries are static; only bin/OpenMVS/ is needed downstream.
RUN cmake -S openMVS -B openMVS/build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/openmvs \
      -DVCG_ROOT=/src/vcglib -DOpenMVS_USE_CUDA=ON \
      -DOpenMVS_USE_PYTHON=OFF -DOpenMVS_BUILD_VIEWER=OFF -DOpenMVS_ENABLE_TESTS=OFF \
 && cmake --build openMVS/build -j"$(nproc)" \
 && cmake --install openMVS/build \
 && ls /opt/openmvs/bin/OpenMVS/DensifyPointCloud /opt/openmvs/bin/OpenMVS/TextureMesh \
 && cp /usr/local/cuda/lib64/stubs/libcuda.so /opt/openmvs/libcuda-stub.so
# gltfpack (from meshoptimizer, pinned): packs the published GLB — quantized attributes
# (KHR_mesh_quantization) + meshopt-compressed geometry/indices (EXT_meshopt_compression, -cc);
# ~4× on the sample GLB, decoded by model-viewer's meshopt decoder. ~0.5 MB binary, builds in
# seconds; no basis/KTX2 — textures pass through as the JPEGs the exporter already wrote.
ARG MESHOPT_TAG=v1.2
RUN git clone --depth 1 --branch ${MESHOPT_TAG} https://github.com/zeux/meshoptimizer.git \
 && cmake -S meshoptimizer -B meshoptimizer/build -DCMAKE_BUILD_TYPE=Release -DMESHOPT_BUILD_GLTFPACK=ON \
 && cmake --build meshoptimizer/build -j"$(nproc)" --target gltfpack \
 && cp meshoptimizer/build/gltfpack /opt/gltfpack
# u2netp (U²-Net small, Apache 2.0, 4.5 MB): salient-object masks for the remove_background option,
# run on CPU by pipeline/masks.py over the undistorted images. Validated on the turntable rig
# 2026-09-04 (docs/superpowers/specs/2026-09-04-photogrammetry-background-removal-design.md).
ARG U2NETP_SHA256=309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8
RUN mkdir -p /opt/models \
 && curl -fsSL -o /opt/models/u2netp.onnx https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx \
 && echo "${U2NETP_SHA256}  /opt/models/u2netp.onnx" | sha256sum -c -

# Stage 2: runtime = the official COLMAP image (Ubuntu 24.04, CUDA 12.9.1 runtime) + OpenMVS + Python.
FROM colmap/colmap:20260729.7651
RUN grep -q 'VERSION_ID="24.04"' /etc/os-release   # the build stage above must match this base
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.12 python3.12-venv libpython3.12 \
      libopencv-core406t64 libopencv-imgproc406t64 libopencv-imgcodecs406t64 libopencv-calib3d406t64 \
      libboost-iostreams1.83.0 libboost-program-options1.83.0 libboost-system1.83.0 libboost-serialization1.83.0 \
      libgmp10 libmpfr6 libgomp1 libglew2.2 libglu1-mesa libpng16-16t64 libjpeg8 libtiff6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/openmvs/bin/OpenMVS/ /usr/local/bin/
COPY --from=build /opt/gltfpack /usr/local/bin/gltfpack
COPY --from=build /opt/models/u2netp.onnx /opt/models/u2netp.onnx
# libcuda.so.1 is the NVIDIA *driver* library: the container runtime injects it on a GPU host, and no
# image carries it at build time. OpenMVS links it directly (CUDA::cuda_driver), so its binaries cannot
# even load without it (attempt 7). The link-time stub from the devel image is kept OFF the loader path;
# the build-time check below and CPU-only smokes opt in with LD_LIBRARY_PATH=/opt/cuda-stubs (the
# pipeline passes --cuda-device -2 when COLMAP_USE_GPU=0, so the stub is loaded but never called).
# On the GPU host the real driver is on the default path and the stub is never used.
COPY --from=build /opt/openmvs/libcuda-stub.so /opt/cuda-stubs/libcuda.so.1
# OpenMVS apps exit 1 after printing --help (no input file => Initialize() returns false), so the
# check is: every shared library resolves (ldd), and the binary runs to completion (exit 0 or 1 —
# not 127 loader failure, not a crash) without a loader error in its output.
RUN ldconfig && colmap -h >/dev/null && cd /tmp \
 && for b in InterfaceCOLMAP DensifyPointCloud ReconstructMesh RefineMesh TextureMesh gltfpack; do \
      if ldd /usr/local/bin/$b | grep 'not found' | grep -v 'libcuda.so.1'; then echo "$b: unresolved libraries"; exit 1; fi; \
      LD_LIBRARY_PATH=/opt/cuda-stubs $b --help >/tmp/help.txt 2>&1; rc=$?; \
      if [ $rc -ne 0 ] && [ $rc -ne 1 ] || grep -q 'error while loading' /tmp/help.txt; then cat /tmp/help.txt; echo "$b: rc=$rc"; exit 1; fi; \
      echo "$b: ok (rc=$rc, $(wc -l </tmp/help.txt) lines of usage)"; \
    done && rm -f /tmp/help.txt /tmp/*.log

WORKDIR /app
RUN python3.12 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
COPY gpu-worker/ /app/gpu-worker/
# constraints.txt pins every package to the acceptance-tested image; see the header there.
COPY photogrammetry-worker/constraints.txt /app/constraints.txt
RUN pip install --no-cache-dir -c /app/constraints.txt /app/gpu-worker boto3 pydantic-settings sqlalchemy psycopg2-binary "trimesh>=4.4" pillow numpy onnxruntime opencv-python-headless
COPY photogrammetry-worker/ /app/
CMD ["python", "main.py"]
