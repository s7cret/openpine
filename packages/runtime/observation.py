"""Optional observation calls. Values always come from the actual evaluator."""
def evaluated(evaluation,node):
    observer=evaluation.observer
    token=observer.begin(evaluation,node)
    try:
        result=evaluation._evaluate(node)
    except BaseException as error:
        observer.end(evaluation,node,token,None,error)
        raise
    observer.end(evaluation,node,token,result,None)
    return result
