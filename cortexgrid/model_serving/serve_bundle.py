from __future__ import annotations

import hashlib
import inspect
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from cortexgrid._bundle import bundle, digest, stage, worker_provides
from cortexgrid.s3_util import upload


log = logging.getLogger(__name__)


@dataclass
class BundleMetadata:
    """What `bundle_class` produces and `deploy_model` needs to PUT the app."""

    bundle_url: str
    class_import_path: str
    # pinned third-party requirements the replica pip-installs (the bundle's
    # distributions the Ray image does not already provide)
    pip_requirements: list[str] = field(default_factory=list)
    # ServeBundle.fingerprint of the uploaded bundle; empty for models saved
    # before bundles were fingerprinted
    fingerprint: str = ""


@dataclass
class ServeBundle:
    """A serve-app's bundle, resolved locally but not uploaded yet: what
    `build_bundle` finds and `upload_bundle` ships."""

    files: set[Path]
    class_import_path: str
    pip_requirements: list[str]
    # Hash of everything the replica runs: the staged files, the class it
    # imports, and the requirements it installs. Equal fingerprints mean the
    # same code, so an uploaded bundle can be reused.
    fingerprint: str


def build_bundle(cls: type) -> ServeBundle:
    """Resolve the serve-app class's code (and the serve entrypoint) into a
    bundle, without uploading it.

    Raises ValueError for a class wrapped by `ray.serve.ingress`: that wrapper
    is a subclass Ray defines in its own module, and on older Ray (e.g. 2.9) it
    reports that module as its own, so the class's source and import path would
    resolve to Ray instead of the serve-app. `cortexgrid.serve.ingress` leaves
    the class unwrapped."""
    if any(klass.__module__.startswith("ray.serve") for klass in cls.__mro__):
        raise ValueError(
            f"{cls.__name__} is wrapped by ray.serve.ingress; decorate it with "
            "cortexgrid.serve.ingress instead (from cortexgrid import serve)"
        )
    entry_file = Path(inspect.getfile(cls)).resolve()
    serve_entry = Path(__file__).parent.with_name("_serve_entry.py")
    desc = bundle(entry_file).merge(bundle(serve_entry))
    class_import_path = f"{cls.__module__}:{cls.__name__}"
    pip_requirements = desc.pip_requirements(worker_provides())
    fingerprint = hashlib.sha256(
        json.dumps(
            [digest(desc.local_files), class_import_path, pip_requirements]
        ).encode()
    ).hexdigest()
    return ServeBundle(
        files=desc.local_files,
        class_import_path=class_import_path,
        pip_requirements=pip_requirements,
        fingerprint=fingerprint,
    )


def bundle_class(
    cls: type, family: str, suffix: str, run_name: str
) -> BundleMetadata:
    """Bundle the serve-app class's code (and the serve entrypoint), zip it, and
    upload to MinIO: `build_bundle` followed by `upload_bundle`.

    Returns the metadata `deploy_model` needs later; callers (typically
    `save_model`) persist it on the registry entry so the deploy step can run
    without holding the class object."""
    return upload_bundle(build_bundle(cls), family, suffix, run_name)


def upload_bundle(
    serve_bundle: ServeBundle, family: str, suffix: str, run_name: str
) -> BundleMetadata:
    """Zip a built bundle and upload it under its fingerprint.

    The fingerprint is part of the URL because Ray keeps a remote working_dir
    it has downloaded and reuses it for the same URL: new code at an old URL
    would never reach a replica."""
    with tempfile.TemporaryDirectory() as tmp:
        code_root = Path(tmp) / "code"
        stage(serve_bundle.files, code_root)
        log.info(
            "Serve bundle for %s/%s/%s: %d files, pip: %s",
            family,
            suffix,
            run_name,
            len(serve_bundle.files),
            serve_bundle.pip_requirements,
        )
        # Ray unpacks a remote (s3://) working_dir zip by stripping its
        # top-level directory when there is exactly one, so a bundle of a single
        # package (model_gateway/) would lose that directory and its import
        # path. Zipping under code/ gives Ray that one directory to strip.
        # make_archive returns the path it wrote. Rebuilding it with
        # Path.with_suffix would cut dotted names ("Qwen2.5-0.5B" -> "Qwen2.zip").
        archive = shutil.make_archive(
            str(Path(tmp) / f"{family}__{suffix}"),
            "zip",
            root_dir=tmp,
            base_dir=code_root.name,
        )
        bundle_url = upload(
            archive,
            dest_path=(
                f"serve-bundles/{run_name}/{family}__{suffix}/"
                f"{serve_bundle.fingerprint}.zip"
            ),
        )
    return BundleMetadata(
        bundle_url=bundle_url,
        class_import_path=serve_bundle.class_import_path,
        pip_requirements=serve_bundle.pip_requirements,
        fingerprint=serve_bundle.fingerprint,
    )
