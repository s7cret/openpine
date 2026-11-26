"""Read-only matrix property checks with per-cell cancellation and work accounting."""
from math import fsum


def evaluate_predicate(e, node, matrix):
    op = node.operation
    size = matrix.rows
    square = size == matrix.columns
    charge = e.work.charge
    charge(node, e.cancelled)
    if op == 'is_square':
        return square

    def read(index):
        charge(node, e.cancelled)
        return matrix.items[index]

    if op == 'is_zero':
        return all(read(i) == 0 for i in range(len(matrix.items)))
    if op == 'is_binary':
        return all(read(i) in (0, 1) for i in range(len(matrix.items)))
    if not square:
        return False
    if op == 'is_stochastic':
        def probabilities(row):
            for index in range(row * size, (row + 1) * size):
                value = read(index)
                if value is None or not 0 <= value <= 1:
                    yield float('nan')
                    return
                yield value
        return size > 0 and all(fsum(probabilities(row)) == 1 for row in range(size))

    if op in ('is_symmetric', 'is_antisymmetric'):
        for row in range(size):
            diagonal = read(row * size + row)
            if diagonal is None or op == 'is_antisymmetric' and diagonal != 0:
                return False
            for col in range(row):
                left, right = read(row * size + col), read(col * size + row)
                if left is None or right is None or left != (right if op == 'is_symmetric' else -right):
                    return False
        return True

    if op == 'is_triangular':
        upper = lower = True
        for row in range(size):
            for col in range(row):
                if upper and read(row * size + col) != 0:
                    upper = False
                if lower and read(col * size + row) != 0:
                    lower = False
                if not (upper or lower):
                    return False
        return True

    for row in range(size):
        for col in range(size):
            if op == 'is_identity':
                if read(row * size + col) != int(row == col):
                    return False
            else:
                diagonal = row == col if op == 'is_diagonal' else row + col == size - 1
                if not diagonal and read(row * size + col) != 0:
                    return False
    return True
