from __future__ import annotations

import json

from resource_engine_devtools.documentation.model import DocumentationModel


def render_json(model: DocumentationModel) -> str:
    return json.dumps(model.to_dict(), indent=2, ensure_ascii=False, default=str)
