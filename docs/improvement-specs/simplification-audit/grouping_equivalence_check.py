"""Independent check of the grouping-prepass deletion proposed in the audit.

This models the control flow inspected in grouping.py at ec7e82754db0.
It does NOT import Code Tribunal, execute its tests, or test same_issue itself.
Nodes are already in stable source-finding-ID order; an edge means same_issue.
Bucket labels stand for (category, anchor_path_key).
"""
from itertools import combinations, product
import json
from pathlib import Path
import random


def split(nodes, adjacency):
    groups = []
    for node in sorted(nodes):
        for group in groups:
            if all(adjacency[node][other] for other in group):
                group.append(node)
                break
        else:
            groups.append([node])
    return groups


def old_grouping(adjacency, labels):
    n = len(labels)
    parent = list(range(n))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for left in range(n):
        for right in range(left + 1, n):
            if adjacency[left][right]:
                a, b = find(left), find(right)
                if a != b:
                    parent[b] = a
    components = {}
    for node in range(n):
        components.setdefault(find(node), []).append(node)
    groups = []
    for component in components.values():
        buckets = {}
        for node in component:
            buckets.setdefault(labels[node], []).append(node)
        for bucket in sorted(buckets.values(), key=lambda b: b[0]):
            groups.extend(split(bucket, adjacency))
    return sorted(groups, key=lambda g: g[0])


def new_grouping(adjacency, labels):
    buckets = {}
    for node, label in enumerate(labels):
        buckets.setdefault(label, []).append(node)
    groups = []
    for bucket in buckets.values():
        groups.extend(split(bucket, adjacency))
    return sorted(groups, key=lambda g: g[0])


def graph(n, mask):
    adjacency = [[left == right for right in range(n)] for left in range(n)]
    for index, (left, right) in enumerate(combinations(range(n), 2)):
        adjacency[left][right] = adjacency[right][left] = bool(mask & (1 << index))
    return adjacency


def check(adjacency, labels):
    before, after = old_grouping(adjacency, labels), new_grouping(adjacency, labels)
    if before != after:
        raise AssertionError((labels, adjacency, before, after))


def main():
    exhaustive_single_bucket = 0
    for n in range(7):
        for mask in range(1 << (n * (n - 1) // 2)):
            check(graph(n, mask), (0,) * n)
            exhaustive_single_bucket += 1
    exhaustive_two_labels = 0
    for n in range(6):
        for mask in range(1 << (n * (n - 1) // 2)):
            adjacency = graph(n, mask)
            for labels in product((0, 1), repeat=n):
                check(adjacency, labels)
                exhaustive_two_labels += 1
    rng = random.Random(20261003)
    randomized = 2000
    for _ in range(randomized):
        n = rng.randrange(1, 61)
        probability = rng.random()
        adjacency = [[left == right for right in range(n)] for left in range(n)]
        for left, right in combinations(range(n), 2):
            adjacency[left][right] = adjacency[right][left] = rng.random() < probability
        labels = [rng.randrange(5) for _ in range(n)]
        check(adjacency, labels)
    result = {
        'scope': 'Independent control-flow model, not repository tests',
        'reviewed_commit': 'ec7e82754db062797a9c4d0646ced79ac2d8e7ff',
        'exhaustive_single_bucket_cases_nodes_0_to_6': exhaustive_single_bucket,
        'exhaustive_two_label_cases_nodes_0_to_5': exhaustive_two_labels,
        'randomized_cases_nodes_1_to_60': randomized,
        'total_cases': exhaustive_single_bucket + exhaustive_two_labels + randomized,
        'mismatches': 0,
        'assumptions': ['symmetric same_issue relation', 'stable source-ID ordering',
                        'unchanged path/category bucketing', 'unchanged greedy complete-link split'],
    }
    target = Path(__file__).with_name('grouping_equivalence_results.json')
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
