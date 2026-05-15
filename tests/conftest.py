"""Shared test setup for ADLC agent tests.

Agent modules instantiate ``LLMClient`` at import time, which constructs a
real ``google.genai.Client(vertexai=True)`` and therefore tries to resolve
GCP Application Default Credentials. That is undesirable in unit tests —
we don't want a CI environment to need a service-account key just to run
mock-based tests.

This conftest patches the ``genai.Client`` constructor with a no-op stub
before any agent module is imported. Tests that need to drive the LLM
swap ``agent._llm_client.call`` with an ``AsyncMock`` themselves.

The fixture also sets ``ENV=dev`` and minimal Vertex env vars so any
code path that reads them sees plausible values.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")
# Do NOT set ENV=dev here. The agent's _resolve_git_inputs() looks for
# dev fixture overrides when ENV=dev, which would bypass the test's
# patched _git_reader and read from the real fixture files instead.

# Patch the Vertex SDK Client constructor before any test imports an agent
# module. This runs at conftest import time, which pytest loads before
# collecting tests under this directory.
from google import genai as _genai  # noqa: E402

_genai.Client = MagicMock(return_value=MagicMock())
