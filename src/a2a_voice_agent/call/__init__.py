"""The conversation layer: takes a Brief, holds one call, returns an Outcome."""

from a2a_voice_agent.call.runtime import EscalationHandler, run_call

__all__ = ["EscalationHandler", "run_call"]
