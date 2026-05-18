"""DE-07 Technology Selection Agent package.

Note: do NOT import ``.agent`` here. The agent module instantiates an
``LLMClient`` at module-load time which requires Vertex/GCP credentials.
Auto-importing it from the package __init__ broke replay tooling, tests,
and any other tool that just needs the parser. Matches DE-03/DE-04
convention — agents are bootstrapped explicitly via
``core.agent_registry.bootstrap()`` at server startup.
"""
