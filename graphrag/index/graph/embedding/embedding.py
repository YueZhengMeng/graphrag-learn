# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""Utilities to generate graph embeddings."""

from dataclasses import dataclass

import graspologic as gc
import networkx as nx
import numpy as np


@dataclass
class NodeEmbeddings:
    """Node embeddings class definition."""

    nodes: list[str]
    embeddings: np.ndarray


def embed_nod2vec(
    graph: nx.Graph | nx.DiGraph,
    dimensions: int = 1536,
    num_walks: int = 10,
    walk_length: int = 40,
    window_size: int = 2,
    iterations: int = 3,
    random_seed: int = 86,
) -> NodeEmbeddings:
    """Generate node embeddings using Node2Vec."""
    # generate embedding
    # 核心逻辑
    # 调库执行node2vec算法，对输入的图进行embedding
    lcc_tensors = gc.embed.node2vec_embed(  # type: ignore
        graph=graph,
        dimensions=dimensions,
        window_size=window_size,
        iterations=iterations,
        num_walks=num_walks,
        walk_length=walk_length,
        random_seed=random_seed,
    )
    # lcc_tensors[0]是embedding向量组成的ndarray，lcc_tensors[1]是node_id组成的list
    return NodeEmbeddings(embeddings=lcc_tensors[0], nodes=lcc_tensors[1])
