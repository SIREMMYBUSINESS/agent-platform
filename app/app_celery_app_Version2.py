"""Backward-compatible import path for the configured Celery app."""

from app.queue.celery import celery_app

__all__ = ["celery_app"]
