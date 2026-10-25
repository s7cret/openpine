"""Explicit net-order and OCA contracts for confirmed historical bars."""
from dataclasses import dataclass
from .trading import BrokerPolicy

PROFILE='openpine.order-group-broker.1'
OPERATIONS=('simulation.order','simulation.entry_group','simulation.exit_group','simulation.close_all')
EVIDENCE_NAMES=('strategy.order','strategy.entry','strategy.exit','strategy.close_all','strategy.oca.none','strategy.oca.cancel','strategy.oca.reduce')
NOTE='Order groups use a bounded historical reconstruction: v1 signatures and incomplete v2 parameters are explicit; OCA transitions and net-order allocation are artifact-defined policies, not external TradingView equivalence.'


@dataclass(frozen=True)
class OrderGroupPolicy(BrokerPolicy):
    profile_id:str=PROFILE
    pyramiding:int=0
    close_entries_rule:str='FIFO'

    def __post_init__(self):
        super().__post_init__()
        if self.profile_id!=PROFILE or type(self.pyramiding) is not int or not 0<=self.pyramiding<=16 or self.close_entries_rule not in ('FIFO','ANY'):
            raise ValueError('Grouped orders require pyramiding 0..16 and FIFO or ANY.')


@dataclass(frozen=True)
class GroupEntryIntent:
    call_site_id:str
    kind:str
    entry_id:str
    direction:int
    quantity:str|None
    limit:str|None=None
    stop:str|None=None
    oca_name:str=''
    oca_type:str='none'


@dataclass(frozen=True)
class GroupExitIntent:
    call_site_id:str
    kind:str
    entry_id:str
    exit_id:str
    stop:str|None=None
    limit:str|None=None
    quantity:str|None=None
    percent:str|None=None
    oca_name:str=''


@dataclass(frozen=True)
class CloseAllIntent:
    call_site_id:str
    kind:str='close_all'


def group_apis(version):
    when=[('when','bool','series',True)] if version<6 else []
    direction=('long' if version<5 else 'direction','bool' if version<5 else 'string','const',None)
    entry=[('id','string','const',None),direction,('qty','float','series',{'kind':'na'}),
        ('limit','float','series',{'kind':'na'}),('stop','float','series',{'kind':'na'}),
        ('oca_name','string','const',''),('oca_type','string','const','none')]
    return [('strategy.order','simulation.order',entry+when),('strategy.entry','simulation.entry_group',entry+when),
        ('strategy.exit','simulation.exit_group',[('id','string','const',None),('from_entry','string','const',None),
            ('stop','float','series',{'kind':'na'}),('limit','float','series',{'kind':'na'}),
            ('qty','float','series',{'kind':'na'}),('qty_percent','float','series',100.),('oca_name','string','const','')]+when),
        ('strategy.close_all','simulation.close_all',when)]


def group_policy(version):
    return {'language_version':version,'profile_id':f'openpine.order-groups.v{version}.1',
        'basis':'reconstructed' if version==1 else 'version_examples_with_reconstructed_gaps' if version==2 else 'artifact_defined_bounded_subset',
        'when':version<6,'activation':'order_close_all_or_explicit_oca_arguments',
        'net_order':'same_direction_add_opposite_reduce_oldest_globally_then_open_residual',
        'net_order_pyramiding':'ignores_entry_pyramiding_with_hard_limit_16_active_lots',
        'entry_pyramiding':'all_active_lots_count_against_header_cap_zero_permits_one',
        'id_namespace':'entry_and_order_share_pending_id_exit_ids_are_separate',
        'close_all':'freeze_unreserved_current_lot_ids_in_source_order_next_open_no_future_binding',
        'oca_none':'name_retained_as_metadata_no_peer_effect',
        'oca_cancel':'after_actual_fill_cancel_live_peers_in_sequence_before_next_execution',
        'oca_reduce':'subtract_actual_filled_quantity_floor_to_step_cancel_zero_preserve_oldest_targets',
        'oca_entry_reversal_quantity':'sum_both_actual_fill_legs',
        'oca_group_type':'one_type_per_active_nonempty_group_name',
        'oca_exit':'explicit_nonempty_name_uses_reduce_unnamed_bracket_keeps_own_peer_cancellation',
        'oca_members':'pending_entry_order_and_protective_exit_only',
        'replacement':'new_order_identity_reset_activation_release_old_membership',
        'atomicity':'whole_bar_publication_shared_lot_accounting_and_order_crossing',
        'ordering':'source_sequence_at_open_then_protection_then_at_most_one_intrabar_entry_or_net_order',
        'stop_limit':'open_activation_same_bar_intrabar_activation_next_bar',
        'new_protection':'open_fill_same_bar_intrabar_fill_next_bar',
        'fees':'one_rounded_fee_per_fill_exact_cumulative_lot_allocation',
        'max_groups':16,'max_group_members':32,'max_pending_entries':16,'max_active_brackets':16,
        'max_open_lots':16,'max_commands_per_bar':16,'max_written_sites':64,
        'state':'active_members_only_no_closed_group_history','replay':'confirmed_history_only',
        'external_execution_verified':False}
