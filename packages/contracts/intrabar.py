"""Explicit sampled-update calculation policy; independent of the price feed."""

PROFILE='openpine.observed-calculation.1'
OPERATION='simulation.strategy_intrabar'

def policy(version):
    if type(version) is not int or version not in range(1,7):raise ValueError('Invalid calculation language version.')
    return {'profile_id':PROFILE,'language_version':version,'header_parameter':version>=2,'varip':version>=4,
        'basis':'explicit_OpenPine_v1_mode' if version==1 else 'pinned_artifact_names_with_explicit_sampled_execution',
        'order':'observe_price_and_existing_fills_then_calculate_then_submit_new_commands',
        'new_orders':'first_eligible_at_next_primary_observation_never_retroactive',
        'runtime':'rollback_ordinary_variables_kernels_and_history_to_last_close_varip_escapes',
        'commit':'one_history_and_output_commit_per_confirmed_bar',
        'requests':'independent_requested_context_pass_once_per_own_observation',
        'varip_types':'primitive_scalars_and_primitive_arrays_only',
        'price_path':'received_updates_only_no_unknown_ticks',
        'limits':'existing_per_pass_work_heap_and_order_limits',
        'unsupported':['calc_on_order_fills','process_orders_on_close','unknown_tick_reconstruction'],
        'external_equivalence':False}

def uses_varip(program):return any(b.mode=='varip' for b in program.bindings)

def source_setting(program):
    header=program.statements[0]
    return header.arguments[-1].expression.value if header.operation==OPERATION else None

def resolve(program,requested=None):
    from .trading import STRATEGY_DECLARATIONS
    from .strategy_cycle import PROFILE as CYCLE
    if requested not in (None,PROFILE,CYCLE):raise ValueError('Unknown calculation profile.')
    from .strategy_cycle import selected,PROFILE as CYCLE
    if selected(program,requested):return CYCLE
    setting=source_setting(program)
    if setting is False and requested:raise ValueError('Calculation profile conflicts with calc_on_every_tick=false.')
    strategy=program.statements[0].operation in STRATEGY_DECLARATIONS
    return PROFILE if requested or setting is True or not strategy and uses_varip(program) else None
