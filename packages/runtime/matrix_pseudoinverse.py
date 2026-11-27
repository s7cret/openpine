"""Moore-Penrose inverse with bounded Jacobi sweeps and exact integral admission."""
from math import copysign, fsum, hypot, isfinite, sqrt
from sys import float_info

from packages.contracts import OpenPineError
from .history_memory import Allocation
from .matrices import publish
from .matrix_inverse import float_inverse, integer_inverse

MAX_SWEEPS = 64


def integral(e, node, matrix, scratch, label):
    m, n, data = matrix.rows, matrix.columns, matrix.items
    e.work.charge(node,e.cancelled,2*m*n)
    rows = [i for i in range(m) if any(data[i*n+j] for j in range(n))]
    cols = [j for j in range(n) if any(data[i*n+j] for i in range(m))]
    # Integral orthogonal projectors are coordinate projections: P_ii is
    # either 0 or 1 and equals the squared norm of row i. Thus A and A+
    # can both be integral only if A's nonzero rows/columns span a square
    # invertible block. Conversely its exact inverse suffices.
    if len(rows) != len(cols):
        e.heap.error('MATRIX_FRACTIONAL','Pseudoinverse contains a nonintegral entry.',node)
    result = [0]*(m*n)
    if not rows:
        return result
    block = [[data[i*n+j] for j in cols] for i in rows]
    try:
        inverse = integer_inverse(e,node,block,scratch,label)
    except OpenPineError as error:
        if error.diagnostic.code != 'MATRIX_SINGULAR':
            raise
        e.heap.error('MATRIX_FRACTIONAL','Pseudoinverse contains a nonintegral entry.',node)
    for i,col in enumerate(cols):
        for j,row in enumerate(rows):
            result[col*m+row] = inverse[i*len(rows)+j]
    return result


def rotate(x, y, cosine, sine):
    for i,(a,b) in enumerate(zip(x,y)):
        x[i], y[i] = cosine*a-sine*b, sine*a+cosine*b


def floating(e, node, matrix):
    m, n, data = matrix.rows, matrix.columns, matrix.items
    scale = max(map(abs,data))
    if scale == 0:
        return [0.0]*(m*n)
    # Operate on the taller orientation so the right singular vectors take
    # min(m,n)^2 slots even for a very wide input.
    wide = m < n
    h, w = max(m,n), min(m,n)
    b = [[data[j*n+i if wide else i*n+j]/scale for i in range(h)] for j in range(w)]
    v = [[float(i==j) for i in range(w)] for j in range(w)]
    eps = float_info.epsilon
    for _ in range(MAX_SWEEPS):
        changed = False
        for p in range(w):
            for q in range(p+1,w):
                e.work.charge(node,e.cancelled,5*h+2*w)
                x, y = b[p], b[q]
                aa = fsum(t*t for t in x)
                bb = fsum(t*t for t in y)
                ab = fsum(a*c for a,c in zip(x,y))
                if abs(ab) <= eps*sqrt(aa)*sqrt(bb) or min(aa,bb) <= eps**2:
                    continue
                delta = (bb-aa)/2
                tangent = ab/(delta+copysign(hypot(delta,ab),delta))
                cosine = 1/hypot(1,tangent)
                sine = tangent*cosine
                rotate(x,y,cosine,sine)
                rotate(v[p],v[q],cosine,sine)
                changed = True
        if not changed:
            break
    else:
        e.heap.error('MATRIX_CONVERGENCE','Jacobi SVD did not converge within the sweep limit.',node)

    singular = [sqrt(fsum(x*x for x in col)) for col in b]
    cutoff = max(singular)*h*eps
    kept = [k for k,s in enumerate(singular) if s>cutoff]
    if m==n and len(kept)==n:
        return float_inverse(e,node,[data[i:i+n] for i in range(0,m*n,n)])
    result = []
    for i in range(n):
        e.work.charge(node,e.cancelled,m*(1+len(kept)))
        for j in range(m):
            row,col = (j,i) if wide else (i,j)
            value = fsum(v[k][row]*(b[k][col]/singular[k])/singular[k] for k in kept)/scale
            if not isfinite(value):
                e.heap.error('MATRIX_NUMERIC','Pseudoinverse exceeds floating-point range.',node)
            result.append(value)
    return result


def pseudoinverse(e, node, matrix):
    m, n = matrix.rows, matrix.columns
    def build():
        if not m or not n:
            return []
        if None in matrix.items:
            return [None]*(m*n)
        scratch = Allocation(e.heap.account) if e.heap.account is not None else None
        label = e.heap.owner+' / matrix pseudoinverse'
        try:
            if scratch:
                scratch.reserve(3*m*n+4*(m+n),label,'array_cell')
                scratch.reserve(3*min(m,n)+8,label,'array_object')
            if matrix.element=='int':
                return integral(e,node,matrix,scratch,label)
            return floating(e,node,matrix)
        finally:
            if scratch:
                scratch.close()
    return publish(e,node,None,n,m,0,build)
