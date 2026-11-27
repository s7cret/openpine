"""Deterministic real spectral values and column bases on the shared matrix heap."""
from fractions import Fraction
from math import fsum, hypot, isfinite

from . import matrix_eigen_exact as exact
from . import matrix_eigen_float as floating
from .history_memory import Allocation
from .matrices import array, publish


def null_basis(a,root,multiplicity,integer,symmetric,work,fail):
    n = len(a)
    b = [[v-root if i==j else v for j,v in enumerate(row)] for i,row in enumerate(a)]
    columns = list(range(n))
    tolerance = 64*n*floating.EPS
    rank = 0
    while rank<n:
        work((n-rank)**2)
        i,j = max(((i,j) for i in range(rank,n) for j in range(rank,n)),key=lambda ij:abs(b[ij[0]][ij[1]]))
        if abs(b[i][j]) <= (0 if integer else tolerance):
            break
        b[rank],b[i] = b[i],b[rank]
        columns[rank],columns[j] = columns[j],columns[rank]
        for row in b:
            row[rank],row[j] = row[j],row[rank]
        for i in range(rank+1,n):
            row = b[i]
            ratio = Fraction(row[rank],b[rank][rank]) if integer else row[rank]/b[rank][rank]
            for j in range(rank+1,n):
                width = exact.bits(ratio)+exact.bits(b[rank][j])+exact.bits(row[j]) if integer else 1
                work(1,width)
                row[j] -= ratio*b[rank][j]
        rank += 1
    if n-rank < multiplicity:
        fail('MATRIX_EIGENBASIS','Insufficient independent eigenvectors for this eigenvalue.')
    basis = []
    for free in range(rank,rank+multiplicity):
        permuted = [0]*n
        permuted[free] = 1
        for i in reversed(range(rank)):
            total = 0
            for j in range(i+1,n):
                work(1,exact.bits(total)+exact.bits(b[i][j])+exact.bits(permuted[j]) if integer else 1)
                total += b[i][j]*permuted[j]
            permuted[i] = Fraction(-total,b[i][i]) if integer else -total/b[i][i]
        vector = [0]*n
        for col,x in zip(columns,permuted):
            vector[col] = x
        if integer:
            vector = exact.primitive(vector,work)
            for row,target in zip(a,vector):
                total = 0
                for x,y in zip(row,vector):
                    work(1,exact.bits(total)+exact.bits(x)+exact.bits(y))
                    total += x*y
                if total!=root*target:
                    fail('MATRIX_EIGENBASIS','Exact eigenvector verification failed.')
        else:
            if symmetric:
                for other in basis:
                    work(2*n)
                    dot = fsum(x*y for x,y in zip(other,vector))
                    vector = [x-dot*y for x,y in zip(vector,other)]
            norm = hypot(*vector)
            if not norm or not isfinite(norm):
                fail('MATRIX_EIGENBASIS','Eigenvector could not be normalized.')
            vector = [x/norm for x in vector]
        largest = max(range(n),key=lambda j:abs(vector[j]))
        if vector[largest]<0:
            vector = [-x for x in vector]
        basis.append(vector)
    return basis


def eigen(e,node,matrix):
    n = matrix.rows
    values_only = node.operation=='eigenvalues'
    def fail(code,message):
        e.heap.error(code,message,node)
    if matrix.columns!=n:
        fail('MATRIX_SHAPE','Eigenvalues and eigenvectors require a square matrix.')
    integer = matrix.element=='int'
    def build():
        if not n:
            return []
        if None in matrix.items:
            return [None]*(n if values_only else n*n)
        scratch = Allocation(e.heap.account) if e.heap.account is not None else None
        label = e.heap.owner+' / eigen'
        peak = 0
        def work(count,width=1):
            nonlocal peak
            e.work.charge(node,e.cancelled,count*((width+29)//30)**2)
            if scratch and width>peak and width>64:
                peak = width
                scratch.resize((8*n*n+16)*((width+7)//8),label,'byte')
        try:
            work(n*n)
            if scratch:
                scratch.reserve(8*n*n+12*n,label,'array_cell')
                scratch.reserve(8*n+12,label,'array_object')
            data = matrix.items
            if all(data[i*n+j]==0 for i in range(n) for j in range(n) if i!=j):
                indices = sorted(range(n),key=lambda i:data[i*n+i])
                if values_only:
                    return [data[i*n+i] for i in indices]
                one,zero = (1,0) if integer else (1.,0.)
                return [one if i==j else zero for i in range(n) for j in indices]
            scale = 1 if integer else max(map(abs,data))
            a = [data[i:i+n] for i in range(0,n*n,n)]
            symmetric = all(a[i][j]==a[j][i] for i in range(n) for j in range(i))
            if integer:
                roots = exact.spectrum(a,work,fail)
            else:
                a = [[x/scale for x in row] for row in a]
                if any(x and a[i//n][i%n]==0 for i,x in enumerate(data)):
                    fail('MATRIX_NUMERIC','Scaling would discard a nonzero matrix element.')
                roots = floating.spectrum(a,symmetric,work,fail)
            if values_only:
                result = [r*scale for r in roots]
            else:
                vectors = []
                i = 0
                while i<n:
                    j = i+1
                    while j<n and abs(roots[j]-roots[i]) <= (0 if integer else 16*n*floating.EPS):
                        j += 1
                    basis = null_basis(a,roots[i],j-i,integer,symmetric,work,fail)
                    if not integer:
                        for value,vector in zip(roots[i:j],basis,strict=True):
                            work(n*n)
                            residual = max(abs(fsum(x*y for x,y in zip(row,vector))-value*t)
                                           for row,t in zip(a,vector))
                            if residual > 128*n*floating.EPS*max(1,abs(value)):
                                fail('MATRIX_CONVERGENCE','Eigenpair residual exceeds the scaled tolerance.')
                    vectors.extend(basis)
                    i = j
                result = [v[row] for row in range(n) for v in vectors]
            if any(not -(2**63)<=x<2**63 if integer else not isfinite(x) for x in result):
                fail('MATRIX_NUMERIC','Spectral result is outside its numeric range.')
            return result
        finally:
            if scratch:
                scratch.close()
    if values_only:
        return array(e,node,matrix.element,n,0,build)
    return publish(e,node,None,n,n,0,build)
