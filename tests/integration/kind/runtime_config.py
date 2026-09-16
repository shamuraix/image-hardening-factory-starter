"""Apply the saved, validated RuntimeClass consistently to harness jobs."""

import re
from pathlib import Path


def render_pipeline(pipeline: str, state: Path) -> str:
    runtime_file = state / "runtime-class"
    if not runtime_file.exists():
        return pipeline
    name = runtime_file.read_text().strip()
    if len(name) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", name):
        raise ValueError("Invalid saved harness RuntimeClass name")
    if pipeline.count("# FACTORY_RUNTIME_CLASS") != 1:
        raise ValueError("Expected one harness RuntimeClass insertion point")
    return pipeline.replace("# FACTORY_RUNTIME_CLASS", f"runtimeClassName: {name}")
