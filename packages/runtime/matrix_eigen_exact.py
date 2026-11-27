"""Exact integer spectrum certification using a characteristic polynomial and Sturm counts."""
from fractions import Fraction
from math import gcd, lcm, isqrt


def bits(x):
    return max(x.numerator.bit_length(),x.denominator.bit_length(),1)


def primitive(poly,work):
    if not poly:
        return []
    common = 1
    for x in poly:
        work(2,bits(x)+common.bit_length())
        common = lcm(common,x.denominator)
    values = [x.numerator*(common//x.denominator) for x in poly]
    factor = gcd(*values) or 1
    return [x//factor for x in values]


def divide(a,b,work):
    a = list(map(Fraction,a))
    quotient = [0]*max(0,len(a)-len(b)+1)
    while len(a)>=len(b):
        shift = len(a)-len(b)
        ratio = a[-1]/b[-1]
        quotient[shift] = ratio
        for j,x in enumerate(b):
            work(1,bits(x)+bits(ratio)+bits(a[j+shift]))
            a[j+shift] -= ratio*x
        while a and not a[-1]:
            a.pop()
    return quotient,primitive(a,work)


def sturm(poly,work):
    chain = [poly,[i*poly[i] for i in range(1,len(poly))]]
    while chain[-1]:
        _,r = divide(chain[-2],chain[-1],work)
        if not r:
            break
        chain.append([-x for x in r])
    return chain


def evaluate(p,x,work):
    y = 0
    for c in reversed(p):
        work(1,bits(y)+bits(x)+bits(c))
        y = y*x+c
    return y


def spectrum(a,work,fail):
    n = len(a)
    work(n*n,64)
    if all(a[i][j]==0 for i in range(n) for j in range(i)) or all(
            a[i][j]==0 for i in range(n) for j in range(i+1,n)):
        return sorted(a[i][i] for i in range(n))
    if n==2:
        x,y = a[0]
        z,t = a[1]
        disc = (x-t)**2+4*y*z
        if disc<0:
            fail('MATRIX_COMPLEX','Spectrum contains a complex conjugate pair.')
        root = isqrt(disc)
        if root*root!=disc or (x+t-root)%2:
            fail('MATRIX_FRACTIONAL','Eigenvalue is not an integer.')
        return [(x+t-root)//2,(x+t+root)//2]

    # Faddeev-LeVerrier recurrence, coefficients in ascending degree order.
    b = [[int(i==j) for j in range(n)] for i in range(n)]
    poly = [1]
    for k in range(1,n+1):
        width = max(bits(x) for row in b for x in row)+64+n.bit_length()
        work(n**3,width)
        b = [[sum(a[i][s]*b[s][j] for s in range(n)) for j in range(n)] for i in range(n)]
        c = -sum(b[i][i] for i in range(n))//k
        poly.insert(0,c)
        for i in range(n):
            b[i][i] += c
    chain = sturm(poly,work)
    if len(chain[-1])>1:
        distinct,_ = divide(poly,chain[-1],work)
        chain = sturm(primitive(distinct,work),work)
    def variations(x):
        signs = [evaluate(p,x,work) for p in chain]
        signs = [v>0 for v in signs if v]
        return sum(x!=y for x,y in zip(signs,signs[1:]))
    bound = 1+max(sum(abs(x) for x in row) for row in a)
    vl,vr = variations(-bound),variations(bound)
    if vl-vr != len(chain[0])-1:
        fail('MATRIX_COMPLEX','Spectrum contains nonreal roots.')
    intervals = [(-bound,bound,vl,vr)]
    roots = []
    while intervals:
        lo,hi,vl,vr = intervals.pop()
        if vl==vr:
            continue
        if hi-lo==1:
            if vl-vr!=1 or evaluate(poly,hi,work)!=0:
                fail('MATRIX_FRACTIONAL','Spectrum contains a nonintegral root.')
            while len(poly)>1 and evaluate(poly,hi,work)==0:
                quotient,_ = divide(poly,[-hi,1],work)
                poly = [int(x) for x in quotient]
                roots.append(hi)
            continue
        middle = (lo+hi)//2
        vm = variations(middle)
        intervals.extend(((lo,middle,vl,vm),(middle,hi,vm,vr)))
    return sorted(roots)
