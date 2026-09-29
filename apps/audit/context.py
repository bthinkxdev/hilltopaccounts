import uuid
from contextvars import ContextVar
from dataclasses import dataclass

@dataclass(frozen=True)
class RequestContext:
    ip_address: str | None
    user_agent: str
    request_id: str
    actor: object | None = None
_current_request_context: ContextVar[RequestContext | None] = ContextVar('_current_request_context', default=None)

def set_context(request) -> None:
    ip_address = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR')
    actor = getattr(request, 'user', None)
    if actor is not None and (not actor.is_authenticated):
        actor = None
    _current_request_context.set(RequestContext(ip_address=ip_address or None, user_agent=request.META.get('HTTP_USER_AGENT', '')[:512], request_id=str(uuid.uuid4()), actor=actor))

def clear_context() -> None:
    _current_request_context.set(None)

def get_context() -> RequestContext:
    return _current_request_context.get() or RequestContext(ip_address=None, user_agent='', request_id='')
