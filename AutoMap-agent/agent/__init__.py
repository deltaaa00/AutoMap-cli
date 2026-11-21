"""Autonomous YouTube Shorts agent package.

The web entry point is the adjacent ``web.py``; the worker entry point is
``python -m agent.runner``. AgentDB is their shared persistence boundary.
Video generation, media storage, optimization, and external API clients live
in separate modules so web requests can queue work without running the worker.
Importing this package alone does not start polling or make network requests."""
