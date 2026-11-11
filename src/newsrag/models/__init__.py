"""Local models on the single GPU, one big model at a time (ticket 006).

- `use(profile)` opens a profile from `models.yaml`: a vLLM server behind an OpenAI-compatible
  `ModelClient` for `vlm`/`llm` profiles, an in-process sentence-transformers `Embedder` for
  `embed` profiles. Re-entrant for the same profile; another profile raises `ModelBusyError`.
- `gpu_lock(holder)` is the exclusive GPU lock for any other in-process GPU user.

Importing this package touches neither the GPU nor the network.
"""

from newsrag.models.client import (
    Embedder,
    ModelBusyError,
    ModelClient,
    loaded_profiles,
    use,
)
from newsrag.models.fit import ModelDoesNotFitError
from newsrag.models.lock import gpu_lock, lock_holders
from newsrag.models.profiles import Profile, get_profile, load_profiles
from newsrag.models.serve import ModelStartError

__all__ = [
    "Embedder",
    "ModelBusyError",
    "ModelClient",
    "ModelDoesNotFitError",
    "ModelStartError",
    "Profile",
    "get_profile",
    "gpu_lock",
    "load_profiles",
    "loaded_profiles",
    "lock_holders",
    "use",
]
