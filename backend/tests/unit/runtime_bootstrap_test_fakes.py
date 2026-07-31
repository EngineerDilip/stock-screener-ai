"""Shared Celery signature fakes for runtime bootstrap tests."""

from __future__ import annotations


class FakeSignature:
    def __init__(self, task: str, *, args=None, kwargs=None):
        self.task = task
        self.args = args or ()
        self.kwargs = kwargs or {}
        self.queue = None

    def set(self, queue=None, **_kwargs):
        self.queue = queue
        return self


class FakeTask:
    def __init__(self, task: str):
        self.task = task

    def si(self, *args, **kwargs):
        return FakeSignature(self.task, args=args, kwargs=kwargs)

    def s(self, *args, **kwargs):
        return FakeSignature(self.task, args=args, kwargs=kwargs)
