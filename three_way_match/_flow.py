"""Internal integer min-cost max-flow.

Successive shortest augmenting paths with Johnson potentials; after each
Dijkstra phase all zero-reduced-cost augmenting paths are pushed before the
next phase. All arithmetic is integer-only and capacities are never
decomposed per unit.
"""

from __future__ import annotations

import heapq

_INF = 1 << 62


class MinCostFlow:
    """Directed graph with integer capacities and non-negative arc costs."""

    def __init__(self, num_nodes: int) -> None:
        # Each edge is stored as [to, residual_capacity, cost, reverse_index].
        self._graph = [[] for _ in range(num_nodes)]

    def add_edge(self, src: int, dst: int, capacity: int, cost: int) -> int:
        """Add an edge; returns its index in ``src``'s adjacency list."""
        graph = self._graph
        graph[src].append([dst, capacity, cost, len(graph[dst])])
        graph[dst].append([src, 0, -cost, len(graph[src]) - 1])
        return len(graph[src]) - 1

    def flow_on(self, src: int, edge_index: int) -> int:
        """Current flow on the edge added at ``src`` with that index."""
        edge = self._graph[src][edge_index]
        return self._graph[edge[0]][edge[3]][1]

    def min_cost_max_flow(self, source: int, sink: int) -> tuple[int, int]:
        """Return (max flow value, minimum total cost over max flows)."""
        graph = self._graph
        num_nodes = len(graph)
        potential = [0] * num_nodes
        total_flow = 0
        while True:
            dist = [_INF] * num_nodes
            dist[source] = 0
            heap = [(0, source)]
            while heap:
                d, node = heapq.heappop(heap)
                if d > dist[node]:
                    continue
                for edge in graph[node]:
                    if edge[1] <= 0:
                        continue
                    nd = d + edge[2] + potential[node] - potential[edge[0]]
                    if nd < dist[edge[0]]:
                        dist[edge[0]] = nd
                        heapq.heappush(heap, (nd, edge[0]))
            if dist[sink] == _INF:
                break
            for node in range(num_nodes):
                if dist[node] < _INF:
                    potential[node] += dist[node]
            # Push every zero-reduced-cost path (each is a shortest path in
            # the current residual graph) before the next Dijkstra phase.
            while True:
                pushed = self._augment_one_zero_cost_path(
                    source, sink, potential)
                if pushed == 0:
                    break
                total_flow += pushed
        total_cost = 0
        for node in range(num_nodes):
            for edge in graph[node]:
                if edge[2] > 0:
                    used = self._graph[edge[0]][edge[3]][1]
                    total_cost += used * edge[2]
        return total_flow, total_cost

    def _augment_one_zero_cost_path(self, source, sink, potential):
        graph = self._graph
        num_nodes = len(graph)
        prev_edge = [(-1, -1)] * num_nodes
        visited = bytearray(num_nodes)
        visited[source] = 1
        stack = [source]
        found = False
        while stack and not found:
            node = stack[-1]
            advanced = False
            for index, edge in enumerate(graph[node]):
                to = edge[0]
                if (edge[1] > 0 and not visited[to]
                        and edge[2] + potential[node] - potential[to] == 0):
                    prev_edge[to] = (node, index)
                    if to == sink:
                        found = True
                        break
                    visited[to] = 1
                    stack.append(to)
                    advanced = True
                    break
            if not advanced and not found:
                stack.pop()
        if not found:
            return 0
        bottleneck = _INF
        node = sink
        while node != source:
            prev_node, index = prev_edge[node]
            if graph[prev_node][index][1] < bottleneck:
                bottleneck = graph[prev_node][index][1]
            node = prev_node
        node = sink
        while node != source:
            prev_node, index = prev_edge[node]
            edge = graph[prev_node][index]
            edge[1] -= bottleneck
            graph[edge[0]][edge[3]][1] += bottleneck
            node = prev_node
        return bottleneck

    def reachable_from(self, source: int) -> list[bool]:
        """Nodes reachable from ``source`` via residual (capacity > 0) edges."""
        graph = self._graph
        visited = [False] * len(graph)
        visited[source] = True
        stack = [source]
        while stack:
            node = stack.pop()
            for edge in graph[node]:
                if edge[1] > 0 and not visited[edge[0]]:
                    visited[edge[0]] = True
                    stack.append(edge[0])
        return visited
