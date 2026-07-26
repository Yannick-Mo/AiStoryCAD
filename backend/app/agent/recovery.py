from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recovery action enum
# ---------------------------------------------------------------------------

class RecoveryAction(str, Enum):
    """Recovery strategies, tried in order of escalating cost."""
    RETRY = "retry"                             # Exponential backoff retry
    RETRY_WITH_ERROR_CONTEXT = "retry_with_error_context"  # Give LLM the error to self-correct
    RETRY_WITH_COMPRESSED_CONTEXT = "retry_with_compressed_context"  # Compress history
    RETRY_ESCALATED_TOKENS = "retry_escalated_tokens"   # Bump max_tokens
    SWITCH_MODEL = "switch_model"               # Try fallback model
    GIVE_UP = "give_up"                         # No recovery possible


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RecoveryDecision:
    action: RecoveryAction
    delay_seconds: float = 0.0
    message: str = ""
    context: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Error classifier
# ---------------------------------------------------------------------------

class ErrorClassifier:
    """Classifies errors to determine the appropriate recovery strategy.

    Layers (in escalating order):
      1. Transient/server error  → exponential backoff retry
      2. Parameter / tool error   → LLM self-correction (inject error context)
      3. Context overflow         → compress conversation history
      4. Persistent model errors  → switch to fallback model
      5. Unknown / exhausted      → give up
    """

    # HTTP-level transient errors the LLM client already handles; agent-level
    # recovery kicks in for errors that survive LLMClient retries or are
    # semantic (tool param errors, context length, etc.).
    TRANSIENT_PATTERNS: list[str] = [
        "overloaded", "rate limit", "capacity", "service unavailable",
        "internal error", "server error", "timed out", "timeout",
        "connection", "network",
    ]

    PARAM_ERROR_PATTERNS: list[str] = [
        "Missing required param", "Unknown param",
        "Invalid parameter", "parameter", "argument",
        "参数验证失败",
    ]

    CONTEXT_OVERFLOW_PATTERNS: list[str] = [
        "context length", "too long", "token limit", "maximum context",
        "reduce the length", "too many tokens",
    ]

    @classmethod
    def classify(
        cls,
        error: str,
        attempt: int,
        max_retries: int = 3,
        recovery_history: list[str] | None = None,
    ) -> RecoveryDecision:
        """Classify an error string and return a recovery decision.

        Args:
            error: The error message string.
            attempt: Current recovery attempt number (0-indexed).
            max_retries: Maximum retries per layer.
            recovery_history: Actions already tried (to avoid repeats).
        """
        history = recovery_history or []
        error_lower = error.lower()

        # ── Layer 1: Transient / server error → backoff ──
        if any(kw in error_lower for kw in cls.TRANSIENT_PATTERNS):
            if attempt < max_retries:
                delay = min(2 ** attempt, 30)  # cap at 30s
                return RecoveryDecision(
                    action=RecoveryAction.RETRY,
                    delay_seconds=delay,
                    message=f"Transient error, retrying in {delay}s (attempt {attempt + 1}/{max_retries})",
                    context={"retry_attempt": attempt + 1, "layer": "transient"},
                )
            # Exhausted transient retries → escalate to model switch
            if RecoveryAction.SWITCH_MODEL not in history:
                return RecoveryDecision(
                    action=RecoveryAction.SWITCH_MODEL,
                    message="Transient errors exhausted retries, switching model",
                    context={"layer": "model_fallback", "reason": "transient_exhausted"},
                )
            return RecoveryDecision(
                action=RecoveryAction.GIVE_UP,
                message=f"All recovery attempts exhausted on transient error: {error[:200]}",
            )

        # ── Layer 2: Parameter / tool error → LLM self-correction ──
        if any(kw in error_lower for kw in cls.PARAM_ERROR_PATTERNS):
            if RecoveryAction.RETRY_WITH_ERROR_CONTEXT not in history and attempt < 2:
                return RecoveryDecision(
                    action=RecoveryAction.RETRY_WITH_ERROR_CONTEXT,
                    message=f"Parameter error, letting LLM self-correct (attempt {attempt + 1})",
                    context={
                        "original_error": error,
                        "retry_attempt": attempt + 1,
                        "layer": "self_correction",
                    },
                )
            # Self-correction failed → give up on this step
            return RecoveryDecision(
                action=RecoveryAction.GIVE_UP,
                message=f"Parameter error persists after correction: {error[:200]}",
                context={"layer": "self_correction_exhausted"},
            )

        # ── Layer 3: Context overflow → compress history ──
        if any(kw in error_lower for kw in cls.CONTEXT_OVERFLOW_PATTERNS):
            if RecoveryAction.RETRY_WITH_COMPRESSED_CONTEXT not in history:
                return RecoveryDecision(
                    action=RecoveryAction.RETRY_WITH_COMPRESSED_CONTEXT,
                    message="Context overflow, compressing history before retry",
                    context={"layer": "context_compression"},
                )
            # Compression didn't help enough → escalate tokens or model switch
            if RecoveryAction.RETRY_ESCALATED_TOKENS not in history:
                return RecoveryDecision(
                    action=RecoveryAction.RETRY_ESCALATED_TOKENS,
                    message="Context still overflowing after compression, escalating max_tokens",
                    context={"layer": "token_escalation"},
                )
            return RecoveryDecision(
                action=RecoveryAction.GIVE_UP,
                message="Context overflow persists after compression and token escalation",
            )

        # ── Layer 4: Unknown errors → backoff, then give up ──
        if attempt < max_retries:
            delay = min(2 ** attempt, 30)
            return RecoveryDecision(
                action=RecoveryAction.RETRY,
                delay_seconds=delay,
                message=f"Unknown error, retrying in {delay}s (attempt {attempt + 1}/{max_retries})",
                context={"retry_attempt": attempt + 1, "layer": "unknown"},
            )

        return RecoveryDecision(
            action=RecoveryAction.GIVE_UP,
            message=f"All recovery attempts exhausted: {error[:200]}",
        )


# ---------------------------------------------------------------------------
# Recovery executor
# ---------------------------------------------------------------------------

class RecoveryExecutor:
    """Executes recovery actions by transforming agent state before retry.

    Does NOT call the LLM itself — it only prepares the state for the next
    execution attempt.  The actual retry call is handled by the normal
    execute_tool graph node.
    """

    def __init__(
        self,
        fallback_models: list[str] | None = None,
        recovery_state: dict | None = None,
    ):
        self._fallback_models = fallback_models or []
        recovery_state = recovery_state or {}
        self._current_model_index = recovery_state.get("model_index", 0)

    async def apply(
        self,
        decision: RecoveryDecision,
        state: dict,
    ) -> dict:
        """Apply a recovery transformation to *state* and return the modified dict.

        The caller should merge the returned dict into the agent state so the
        next execution attempt uses the transformed context.
        """
        updates: dict = {}
        recovery_state = dict(state.get("recovery_state", {}))
        history: list[str] = list(recovery_state.get("recovery_history", []))
        history.append(decision.action.value)
        recovery_state["recovery_history"] = history
        recovery_state["last_action"] = decision.action.value
        recovery_state["last_message"] = decision.message

        if decision.action == RecoveryAction.RETRY:
            # Simple backoff — sleep is handled by the caller (loop.py)
            pass

        elif decision.action == RecoveryAction.RETRY_WITH_ERROR_CONTEXT:
            # Inject the error so the LLM can see what went wrong
            errors: list[str] = list(state.get("errors", []))
            original = decision.context.get("original_error", "")
            errors.append(
                f"[SELF-CORRECTION] Previous attempt failed: {original}"
            )
            updates["errors"] = errors
            updates["retry_context"] = {
                "failed": True,
                "error": original,
                "correction_attempt": decision.context.get("retry_attempt", 1),
            }
            recovery_state["self_correction_applied"] = True

        elif decision.action == RecoveryAction.RETRY_WITH_COMPRESSED_CONTEXT:
            from app.agent.context_compressor import compress_history  # noqa: F811
            compressed = compress_history(
                state.get("messages", [])
            )
            updates["messages"] = compressed
            recovery_state["context_compressed"] = True

        elif decision.action == RecoveryAction.RETRY_ESCALATED_TOKENS:
            recovery_state["escalated_tokens"] = True

        elif decision.action == RecoveryAction.SWITCH_MODEL:
            if self._current_model_index < len(self._fallback_models):
                new_model = self._fallback_models[self._current_model_index]
                self._current_model_index += 1
                recovery_state["switched_model"] = new_model
                recovery_state["model_index"] = self._current_model_index
                updates["_model_override"] = new_model
            else:
                recovery_state["models_exhausted"] = True
                updates["errors"] = list(state.get("errors", [])) + [
                    "All fallback models exhausted"
                ]

        elif decision.action == RecoveryAction.GIVE_UP:
            recovery_state["gave_up"] = True
            recovery_state["gave_up_reason"] = decision.message

        updates["recovery_state"] = recovery_state
        return updates

    # ------------------------------------------------------------------
    # Deprecated: _compress_messages has been removed.
    # Use ``context_compressor.compress_history()`` instead.
    # ------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fallback model helpers
# ---------------------------------------------------------------------------

def get_fallback_models() -> list[str]:
    """Return the ordered fallback model chain from settings.

    The primary model is excluded from the fallback list so we don't
    retry the same model that just failed.
    """
    from app.config import settings

    primary = settings.llm_model
    fallback_str = getattr(settings, "llm_fallback_models", "")
    if not fallback_str:
        return []

    fallbacks = [m.strip() for m in fallback_str.split(",") if m.strip()]
    return [m for m in fallbacks if m != primary]



