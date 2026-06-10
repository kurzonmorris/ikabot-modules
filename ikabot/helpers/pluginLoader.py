"""
Plugin auto-loader.

Scans the plugins/ directory (sibling of the ikabot package root) for *.py
files and returns a list of plugin descriptors ready for use by the menu.
"""

import importlib.util
import logging
import os
import sys
from typing import NamedTuple

logger = logging.getLogger(__name__)

_DEFAULT_ORDER = 50
_PLUGINS_DIR_NAME = "plugins"


class PluginDescriptor(NamedTuple):
    name: str           # module name / function name
    label: str          # shown in the submenu
    order: int          # sort key within the submenu
    entrypoint: object  # callable: fn(session, event, stdin_fd, predetermined_input)


def _find_plugins_dir() -> str | None:
    """Locate the plugins/ directory relative to the installed package."""
    # Walk up from this file: helpers/ -> ikabot/ -> project root -> plugins/
    helpers_dir = os.path.dirname(os.path.abspath(__file__))
    ikabot_dir = os.path.dirname(helpers_dir)
    project_root = os.path.dirname(ikabot_dir)
    candidate = os.path.join(project_root, _PLUGINS_DIR_NAME)
    if os.path.isdir(candidate):
        return candidate
    return None


def discover_plugins() -> list[PluginDescriptor]:
    """Return a sorted list of valid plugins found in the plugins/ directory.

    Invalid or broken plugins are skipped with a logged warning so the rest of
    the menu continues to work.
    """
    plugins_dir = _find_plugins_dir()
    if plugins_dir is None:
        return []

    descriptors: list[PluginDescriptor] = []

    for filename in sorted(os.listdir(plugins_dir)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        module_name = filename[:-3]
        filepath = os.path.join(plugins_dir, filename)

        try:
            spec = importlib.util.spec_from_file_location(module_name, filepath)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot create module spec for {filepath}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.warning("Plugin %s failed to load: %s", filename, exc)
            continue

        entrypoint = getattr(module, module_name, None)
        if not callable(entrypoint):
            logger.warning(
                "Plugin %s has no callable '%s'; skipping.", filename, module_name
            )
            continue

        label = getattr(module, "MENU_LABEL", module_name)
        order = getattr(module, "MENU_ORDER", _DEFAULT_ORDER)

        descriptors.append(PluginDescriptor(
            name=module_name,
            label=str(label),
            order=int(order),
            entrypoint=entrypoint,
        ))

    return sorted(descriptors, key=lambda d: (d.order, d.label))
