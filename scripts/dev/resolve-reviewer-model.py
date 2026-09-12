"""Resolve the model a review child actually ran as, from the parent side."""

import json
import pathlib
import sys

# Resolve the model the child ACTUALLY ran as. The requested slug is a routing
# hint: the router can apply a per-turn fallback, so only the child's own
# session record is evidence. Reading it here, in the parent, is what lets the
# child stay a plain reviewer with no self-identification tooling.
#
# `openclaw gateway call tools.invoke` cannot be used for this: it ignores the
# `args` object and resolves sessionKey to "main", which is a CLI limitation
# rather than a tool one. `openclaw sessions --json` carries the same values and
# agrees with session_status exactly.
sessions_path, child_key = sys.argv[1:3]
try:
    sessions = json.loads(pathlib.Path(sessions_path).read_text(encoding="utf-8"))["sessions"]
except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
    sys.exit("attribution: session list unreadable")

match = next((s for s in sessions if s.get("key") == child_key), None)
if match is None:
    sys.exit("attribution: child session not found")

provider = match.get("modelProvider")
model = match.get("model")
if not isinstance(provider, str) or not isinstance(model, str) or not provider or not model:
    sys.exit("attribution: child session reports no resolved model")

# Emit only the slug. Session records carry token counts, context sizes and
# other operational fields that have no business in a public PR comment.
print(f"{provider}/{model}")
