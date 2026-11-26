"""Square matrix inversion with transactional publication and metered workspace."""
from math import isfinite

from .history_memory import Allocation
from .matrices import publish
from .matrix_elimination import pivot_row


def pivot(e, node, a, k):
    e.work.charge(node, e.cancelled, len(a)-k)
    selected = pivot_row(a,k,k)
    if a[selected][k] == 0:
        e.heap.error('MATRIX_SINGULAR','Matrix has no inverse.',node)
    a[k], a[selected] = a[selected], a[k]
    return selected


def float_inverse(e, node, a):
    n = len(a)
    permutation = list(range(n))
    def finite(value):
        if not isfinite(value):
            e.heap.error('MATRIX_NUMERIC','Nonfinite matrix inverse intermediate or result.',node)
        return value

    for k in range(n):
        selected = pivot(e,node,a,k)
        permutation[k], permutation[selected] = permutation[selected], permutation[k]
        for i in range(k+1,n):
            e.work.charge(node,e.cancelled,n-k)
            factor = a[i][k] / a[k][k]
            a[i][k] = factor
            for j in range(k+1,n):
                a[i][j] = finite(a[i][j] - factor*a[k][j])

    result = [0.0]*(n*n)
    for col in range(n):
        x = [float(row == col) for row in permutation]
        for i in range(n):
            e.work.charge(node,e.cancelled,i+1)
            for j in range(i):
                x[i] -= a[i][j]*x[j]
        for i in reversed(range(n)):
            e.work.charge(node,e.cancelled,n-i)
            for j in range(i+1,n):
                x[i] -= a[i][j]*x[j]
            x[i] = finite(x[i]/a[i][i])
            result[i*n+col] = x[i]
    return result


def integer_inverse(e, node, a, scratch, label):
    n = len(a)
    for i,row in enumerate(a):
        row.extend(int(i==j) for j in range(n))
    # Fraction-free Gauss-Jordan on [A | I]. At the end each diagonal
    # coefficient is the denominator of its corresponding augmented row.
    previous, bits = 1, 64
    for k in range(n):
        pivot(e,node,a,k)
        diagonal = a[k][k]
        limbs = (bits+29)//30
        if scratch:
            scratch.resize((2*n*n+4)*((2*bits+8)//8),label,'byte')
        next_bits = bits
        for i,row in enumerate(a):
            if i==k:
                continue
            e.work.charge(node,e.cancelled,2*n*limbs*limbs)
            factor = row[k]
            for j in range(2*n):
                if j!=k:
                    row[j] = (diagonal*row[j]-factor*a[k][j])//previous
                    next_bits = max(next_bits,row[j].bit_length())
            row[k] = 0
        previous, bits = diagonal, next_bits
    result = []
    for i,row in enumerate(a):
        e.work.charge(node,e.cancelled,n*((bits+29)//30)**2)
        for j in range(n,2*n):
            value, remainder = divmod(row[j],row[i])
            if remainder:
                e.heap.error('MATRIX_FRACTIONAL','Inverse contains a nonintegral entry.',node)
            if not -(2**63) <= value < 2**63:
                e.heap.error('MATRIX_NUMERIC','Inverse exceeds signed int64.',node)
            result.append(value)
    return result


def inverse(e, node, matrix):
    n = matrix.rows
    if n != matrix.columns:
        e.heap.error('MATRIX_SHAPE','Inverse requires a square matrix.',node)
    def build():
        if not n:
            return []
        if None in matrix.items:
            return [None]*(n*n)
        integer = matrix.element=='int'
        scratch = Allocation(e.heap.account) if e.heap.account is not None else None
        label = e.heap.owner+' / matrix inverse'
        try:
            if scratch:
                scratch.reserve((2*n*n if integer else n*n+2*n),label,'array_cell')
                scratch.reserve(n+2,label,'array_object')
            a = [matrix.items[i:i+n] for i in range(0,n*n,n)]
            return integer_inverse(e,node,a,scratch,label) if integer else float_inverse(e,node,a)
        finally:
            if scratch:
                scratch.close()
    return publish(e,node,None,n,n,0,build)
