"""
In-process event bus used to fan out real-time updates to SSE clients.

The FastAPI layer subscribes a per-client queue here; services publish
events (alert created, case created, transaction created, stats
updated...) and every SSE subscriber receives the JSON payload.
"""

import json
import queue
import threading

_listeners = []
_listeners_lock = threading.Lock()


def subscribe(listener_queue):
    """Register a queue.Queue that will receive published events."""
    with _listeners_lock:
        _listeners.append(listener_queue)
    return listener_queue


def unsubscribe(listener_queue):
    with _listeners_lock:
        try:
            _listeners.remove(listener_queue)
        except ValueError:
            pass


def publish(event_type, data=None):
    """Publish an event to every currently subscribed SSE client."""
    payload = json.dumps({"event": event_type, "data": data or {}})
    with _listeners_lock:
        listeners = list(_listeners)
    for listener in listeners:
        try:
            listener.put_nowait(payload)
        except (queue.Full, ValueError):
            pass
        except Exception:
            pass