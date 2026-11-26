"""Budgeted fraction-free integer and floating-point pivoted elimination."""
from math import frexp, isfinite, ldexp
from sys import float_info

from .history_memory import Allocation


def pivot_row(a, start, column):
    return max(range(start,len(a)),key=lambda i:abs(a[i][column]))


def eliminate(e, node, matrix):
    rows, cols = matrix.rows, matrix.columns
    determinant = node.operation == 'det'
    integer = matrix.element == 'int'
    if determinant and rows != cols:
        e.heap.error('MATRIX_SHAPE', 'Determinant requires a square matrix.', node)
    charge = lambda n: e.work.charge(node, e.cancelled, n)
    charge(1)
    if not rows or not cols:
        return (1 if integer else 1.0) if determinant else 0

    count = rows * cols
    charge(count)
    if None in matrix.items:
        return None
    scratch = Allocation(e.heap.account) if e.heap.account is not None else None
    label = e.heap.owner + ' / elimination'
    try:
        if scratch:
            scratch.reserve(count, label, 'array_cell')
            scratch.reserve(rows, label, 'array_object')
        a = [matrix.items[i:i+cols] for i in range(0,count,cols)]
        threshold = 0
        if not integer and not determinant:
            charge(2*count)
            scale = max(map(abs,matrix.items))
            if scale == 0:
                return 0
            for row in a:
                for j in range(cols):
                    row[j] /= scale
            threshold = max(rows,cols) * float_info.epsilon

        rank, sign, previous = 0, 1, 1
        mantissa, exponent = 1.0, 0
        bits = 64
        reserved_width = 0
        for col in range(cols):
            charge(rows-rank)
            selected = pivot_row(a,rank,col)
            pivot = a[selected][col]
            if abs(pivot) <= threshold:
                if determinant:
                    return 0 if integer else 0.0
                continue
            if selected != rank:
                a[rank], a[selected] = a[selected], a[rank]
                sign = -sign
            if determinant and not integer:
                fraction, shift = frexp(pivot)
                mantissa, adjust = frexp(mantissa * fraction)
                exponent += shift + adjust
            # At this stage, integer coefficients occupy at most 'bits' bits.
            # Reserve space for retained coefficients and the wide multiply/subtract
            # temporaries before performing any big-integer operation.
            limbs = (bits+29)//30
            if integer and scratch and rank+1 < rows and col+1 < cols:
                width = (2*bits+8)//8
                if width > reserved_width:
                    scratch.resize((count+4)*width,label,'byte')
                    reserved_width = width
            next_bits = 1
            for i in range(rank+1,rows):
                row = a[i]
                factor = row[col] if integer else row[col]/pivot
                row[col] = 0
                charge(1 + (cols-col-1)*(limbs*limbs if integer else 1))
                for j in range(col+1,cols):
                    if integer:
                        row[j] = (pivot*row[j] - factor*a[rank][j]) // previous
                        next_bits = max(next_bits,row[j].bit_length())
                    else:
                        row[j] -= factor*a[rank][j]
                        if not isfinite(row[j]):
                            e.heap.error('MATRIX_NUMERIC','Nonfinite elimination intermediate.',node)
            previous = pivot
            bits = max(next_bits,previous.bit_length()) if integer else 0
            rank += 1
            if rank == rows:
                break
        if not determinant:
            return rank
        if integer:
            value = sign * previous
            if not -(2**63) <= value < 2**63:
                e.heap.error('MATRIX_NUMERIC','Determinant exceeds signed int64.',node)
            return value
        try:
            return ldexp(sign * mantissa,exponent)
        except OverflowError:
            e.heap.error('MATRIX_NUMERIC','Determinant exceeds the floating-point range.',node)
    finally:
        if scratch:
            scratch.close()
