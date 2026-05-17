"""Adapter dramatiq — broker Redis + setup do worker."""

from app.adapters.queue.dramatiq_setup import broker, get_broker

__all__ = ["broker", "get_broker"]
