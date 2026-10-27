"""Pure scalar binary semantics shared by runtime and frozen target evaluation."""

def binary(operator,left,right,base,policy):
    if operator in ('and','or'):
        if operator=='and':return False if left is False or right is False else None if left is None or right is None else True
        return True if left is True or right is True else None if left is None or right is None else False
    if operator in ('==','!=','<','<=','>','>='):
        if left is None or right is None:return policy.comparison_na
        if operator=='==':return left==right
        if operator=='!=':return left!=right
        if operator=='<':return left<right
        if operator=='<=':return left<=right
        if operator=='>':return left>right
        return left>=right
    if left is None or right is None:return None
    if operator=='+':return left+right
    if operator=='-':return left-right
    if operator=='*':return left*right
    if right==0:return None
    if operator=='%':return left%right
    if operator!='/':raise ValueError('Unknown scalar operation.')
    if base=='int':return (abs(left)//abs(right))*(-1 if (left<0)!=(right<0) else 1)
    return left/right
