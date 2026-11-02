"""providers/__init__.py — Provider management + multi-key rotation.

§25 Phase 1 ③. Each provider has a base_url and one or more aliased keys.
Keys carry a user-assigned status (high/medium/low). Universe rotates within a
provider before falling back to a different provider. The active key is
chosen by status and recent real error rate — there is no internal
budget or rate-limit-headroom tracker; spend/rate caps are the provider's
own job, set at the API-key level in the provider's dashboard.

Every provider is a real LLM endpoint (cloud or local). Nothing answers
until a key is added via Settings → Model provider or /provider add; a
missing provider surfaces as an explicit no_provider error.
"""

from .manager import ProviderManager, get_provider_manager
from .client import LLMClient, LLMResponse, LLMError

__all__ = [
    "ProviderManager",
    "get_provider_manager",
    "LLMClient",
    "LLMResponse",
    "LLMError",
]
