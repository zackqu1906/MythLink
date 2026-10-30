"""Device plugins: no eager imports of optional drivers."""
from importlib import import_module
from ring.product import DRIVERS
from .base import RingDriver


def create_driver(kind, device, name, sample, state, reconnected) -> RingDriver:
    return import_module(DRIVERS[kind][0]).create(device, name, sample, state, reconnected)
