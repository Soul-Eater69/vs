from __future__ import annotations


def is_gateway_timeout_error(exc: Exception) -> bool:
    stack = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)

        if getattr(current, "status_code", None) == 504:
            return True

        response = getattr(current, "response", None)
        if response is not None and getattr(response, "status_code", None) == 504:
            return True

        msg = str(current).lower()
        if "gateway timeout" in msg or ("504" in msg and "timeout" in msg):
            return True

        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, Exception):
            stack.append(cause)
        if isinstance(context, Exception):
            stack.append(context)
    return False
