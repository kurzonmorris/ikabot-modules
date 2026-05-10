# Ikabot Plugins

Drop any `.py` file into this folder and it will automatically appear in the
**Plugins** submenu the next time ikabot starts. No manual registration needed.

## Plugin contract

Your file must:

1. Be named after the feature (e.g. `myFeature.py`).
2. Export a function whose name matches the filename exactly:
   ```python
   def myFeature(session, event, stdin_fd, predetermined_input):
       ...
   ```
3. Fire `event.set()` once the plugin is done so the main menu regains control.

## Optional metadata

Define these at module level to control how your plugin appears in the menu:

```python
MENU_LABEL = "My Feature - does X"   # shown in the submenu (default: filename)
MENU_ORDER = 10                        # lower = higher up the list (default: 50)
```

## Security note

Plugins run with full access to the game session. Only install plugins from
sources you trust.
