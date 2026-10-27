"""Runtime adapters for the shared numeric registry; no duplicate algorithms."""
from packages.contracts.numeric_functions import NumericError
from packages.contracts.scalar_functions import ScalarError
from packages.contracts.scalar_registry import invoke
from packages.runtime.series import KernelError

def numeric_kernel(operation):
    def run(arguments,context):
        try:return invoke(operation,arguments,getattr(context,'numeric_budget',None),getattr(context,'language_version',6))
        except NumericError as error:raise KernelError(error.code,str(error),'rule.numeric.round' if operation=='numeric.round' else 'rule.numeric') from error
        except ScalarError as error:raise KernelError('NUMERIC_BUDGET',str(error),'rule.numeric') from error
    return run

absolute=numeric_kernel('numeric.abs')
missing=numeric_kernel('numeric.na')
replace_missing=numeric_kernel('numeric.nz')
cast_int=numeric_kernel('numeric.int')
cast_float=numeric_kernel('numeric.float')
minimum=numeric_kernel('numeric.min')
maximum=numeric_kernel('numeric.max')
rounded=numeric_kernel('numeric.round')
