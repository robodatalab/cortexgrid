# Ray head/worker image — adds our pinned deps to the upstream Ray image.
# KubeRay launches `ray start` itself via the RayCluster CRD, so no custom
# entrypoint — we just inherit the one baked into rayproject/ray.

FROM rayproject/ray:2.9.3

RUN pip install --no-cache-dir \
    mlflow==3.11.1 \
    python-dotenv==1.0.1 \
    psutil==5.9.8
