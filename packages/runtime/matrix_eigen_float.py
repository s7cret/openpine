"""Real eigenvalues of scaled matrices via orthogonal similarity transformations."""
from math import copysign, fsum, hypot, sqrt
from sys import float_info

EPS = float_info.epsilon
MAX_STEPS = 96


def reflector(values):
    length = hypot(*values)
    if length == 0:
        return None
    v = [x/length for x in values]
    v[0] += copysign(1.,v[0])
    norm = hypot(*v)
    return [x/norm for x in v]


def left(a, k, v):
    for j in range(len(a)):
        dot = 2*fsum(x*a[k+i][j] for i,x in enumerate(v))
        for i,x in enumerate(v):
            a[k+i][j] -= dot*x


def right(a, k, v):
    for row in a:
        dot = 2*fsum(x*row[k+i] for i,x in enumerate(v))
        for i,x in enumerate(v):
            row[k+i] -= dot*x


def hessenberg(a,work):
    n = len(a)
    for k in range(n-2):
        work(4*n*(n-k))
        v = reflector([a[i][k] for i in range(k+1,n)])
        if v:
            left(a,k+1,v)
            right(a,k+1,v)
        for i in range(k+2,n):
            a[i][k] = 0.


def ql(d,e,work,fail):
    """Implicit QL on a symmetric tridiagonal, with a Wilkinson shift."""
    n = len(d)
    for start in range(n):
        for _ in range(MAX_STEPS):
            work(n-start)
            end = start
            while end < n-1 and abs(e[end]) > EPS*(abs(d[end])+abs(d[end+1])):
                end += 1
            if end==start:
                break
            g = (d[start+1]-d[start])/(2*e[start])
            g = d[end]-d[start]+e[start]/(g+copysign(hypot(g,1),g))
            s = c = 1.
            p = 0.
            for i in range(end-1,start-1,-1):
                f = s*e[i]
                b = c*e[i]
                r = hypot(f,g)
                e[i+1] = r
                if r==0:
                    d[i+1] -= p
                    e[end] = 0.
                    break
                s,c = f/r,g/r
                g = d[i+1]-p
                r = (d[i]-g)*s+2*c*b
                p = s*r
                d[i+1] = g+p
                g = c*r-b
            else:
                d[start] -= p
                e[start] = g
                e[end] = 0.
        else:
            fail('MATRIX_CONVERGENCE','Symmetric QL iteration limit exceeded.')
    return d


def pair(a,b,c,d):
    # Use the determinant for the smaller root to avoid subtractive cancellation.
    center = (a+d)/2
    half = (a-d)/2
    scale = max(abs(half),sqrt(abs(b))*sqrt(abs(c)))
    if not scale:
        return center,center
    disc = (half/scale)**2 + copysign((sqrt(abs(b))*sqrt(abs(c))/scale)**2,b*c)
    if disc < 0:
        return None
    large = center+copysign(scale*sqrt(disc),center)
    return (large,(a*d-b*c)/large) if large else (0.,0.)


def qr_block(h,work,fail):
    pending = [h]
    roots = []
    while pending:
        a = pending.pop()
        n = len(a)
        if n==1:
            roots.append(a[0][0])
            continue
        for step in range(MAX_STEPS):
            work(n)
            cut = next((i for i in range(1,n) if abs(a[i][i-1])<=
                        EPS*(abs(a[i-1][i-1])+abs(a[i][i]))),None)
            if cut is not None:
                pending.extend(([r[:cut] for r in a[:cut]],[r[cut:] for r in a[cut:]]))
                break
            x,y,z,t = a[-2][-2],a[-2][-1],a[-1][-2],a[-1][-1]
            tail = pair(x,y,z,t)
            if n==2:
                if tail is None:
                    fail('MATRIX_COMPLEX','Spectrum contains a complex conjugate pair.')
                roots.extend(tail)
                break
            work(8*n**3)
            if step%16==15 or tail is not None:
                shift = t+abs(z) if step%16==15 else min(tail,key=lambda v:abs(v-t))
                q = [[v-shift if i==j else v for j,v in enumerate(row)] for i,row in enumerate(a)]
            else:
                # A real double shift for a trailing conjugate pair.
                q = [[fsum(a[i][k]*a[k][j] for k in range(n))-(x+t)*a[i][j]
                      + ((x*t-y*z) if i==j else 0.) for j in range(n)] for i in range(n)]
            for k in range(n-1):
                v = reflector([q[i][k] for i in range(k,n)])
                if v:
                    left(q,k,v)
                    left(a,k,v)
                    right(a,k,v)
            for i in range(2,n):
                for j in range(i-1):
                    a[i][j] = 0.
        else:
            fail('MATRIX_CONVERGENCE','Real QR iteration limit exceeded.')
    return roots


def spectrum(a,symmetric,work,fail):
    n = len(a)
    h = [row.copy() for row in a]
    hessenberg(h,work)
    if symmetric:
        return sorted(ql([h[i][i] for i in range(n)],[h[i+1][i] for i in range(n-1)]+[0.],work,fail))
    return sorted(qr_block(h,work,fail))
