"""Dependencies shared across endpoints.

Declared as ``Annotated`` aliases so endpoint signatures stay readable and a
dependency can be swapped in tests via ``app.dependency_overrides``.
"""

from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]
