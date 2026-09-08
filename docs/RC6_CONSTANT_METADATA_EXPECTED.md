# Stage 2 independent static input metadata

The frozen40-case table contains manually calculated default values, source
texts and exact source hashes. The host checks them through its normal native
compiler adapter, admitted dependency pins and published input descriptors.
Integer inputs require actual Python int values; float descriptors admit JSON
integers or floats and compare within explicit absolute tolerance1e-14, excluding
booleans. This reflects InputSpec normalization without weakening integer checks.

The producer prerequisites include reviewed typed UDF constant coercion, modern
numeric promotion/comparisons, lexical global/default context, and the independent
nested child-type P1 repair348909. All40 direct normal-compiler observations have
passed on both Python versions. Host/Linux full acceptance remains pending.
The original P1 failure evidence and incomplete modulo constant folding remain
explicit separate records. This corpus does not close the whole Stage2 criteria.
