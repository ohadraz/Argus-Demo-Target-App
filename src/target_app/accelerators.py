"""The GPUs the shop's nodes carry, in the words a cluster advertises them in.

NVIDIA's GPU Feature Discovery labels every node with the product of the GPU it
carries, under `nvidia.com/gpu.product`, spelled as the driver reports it with
spaces turned to dashes. A scheduler reads those labels to place a pod, an Argo
CD that allow-lists the label reports it on the host a pod runs on, and a
`nodeSelector` on the label is how a deployment is held to one kind of card.

The shop's fleet was bought as Volta. One Ampere node joined it later, for the
reason one does - a larger model somewhere else needed it - and nothing about the
shop's deployment says its replicas may not land there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

# The node label GPU Feature Discovery publishes the product under.
GPU_PRODUCT_LABEL: Final = "nvidia.com/gpu.product"

# The cards the shop's replicas run on, by the product name the label carries.
V100: Final = "Tesla-V100-SXM2-16GB"
A100: Final = "NVIDIA-A100-SXM4-40GB"

# What the nodes carrying each are called. One prefix per kind of card, and an
# index per node, which is how a node pool names what it provisions.
V100_NODE_PREFIX: Final = "gpu-v100-"
A100_NODE_PREFIX: Final = "gpu-a100-"

# Where the pricing service runs. It serves no model, so its node carries no GPU
# and no product label - which is a node, not a missing reading.
PRICING_NODE: Final = "general-0"


@dataclass(frozen=True)
class ReplicaPlacement:
    """Where one of the shop's replicas is running, and since when.

    `index` is which replica, counting from nothing; what the platform calls its
    pod is the platform's business. `started_at` is when the pod came up, which
    is the moment it was last placed.
    """

    index: int
    node: str
    accelerator: str
    started_at: datetime


def the_node_carrying(accelerator: str, index: int) -> str:
    """The name of the `index`th node carrying this kind of card."""
    prefix = A100_NODE_PREFIX if accelerator == A100 else V100_NODE_PREFIX

    return f"{prefix}{index}"
