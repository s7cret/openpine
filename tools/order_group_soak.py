"""Long-lived OCA peers, activated stop-limit and global close through real recovery."""
import argparse
from pathlib import Path
from tools.launch_soak import run as prepared_run
from packages.contracts.wire import atomic_json

SOURCE='''//@version=6
strategy("Group worker recovery",pyramiding=0,close_entries_rule="ANY",default_qty_type=strategy.fixed,initial_capital=1000,commission_value=1,slippage=2)
part=input.int(2)
higher=request.security("TEST:LOAD","4",close)
if bar_index==3 and higher>0
    strategy.order("A",strategy.short,qty=2,limit=100)
    strategy.exit("PA","A",stop=130,limit=90,qty_percent=100)
if bar_index==4
    strategy.order("B",strategy.short,qty=3,limit=100)
    strategy.exit("PB","B",stop=130,limit=90,qty_percent=100)
if bar_index==5
    strategy.order("C",strategy.short,qty=4,stop=100-syminfo.mintick,limit=100+5*syminfo.mintick,oca_name="entries",oca_type=strategy.oca.cancel)
    strategy.order("D",strategy.short,qty=7,limit=110,oca_name="entries",oca_type=strategy.oca.cancel)
    strategy.exit("PC1","C",stop=130,limit=100,qty=part,oca_name="profit")
    strategy.exit("PC2","C",stop=130,limit=101,qty_percent=100,oca_name="profit")
    strategy.exit("PD","D",stop=130,limit=90,qty_percent=100)
if barstate.islast
    strategy.close_all()
plot(strategy.position_size,title="Position")
plot(strategy.equity,title="Equity")
plot(strategy.position_avg_price,title="Entry price")
plot(ta.cum(volume),title="Volume")
plot(bar_index,title="Index")
'''
INSTRUMENT={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'LOAD','type':'custom'}


def run(directory,bars=1000000):
    result=prepared_run(directory,bars,source=SOURCE,instrument=INSTRUMENT,expected_trades=1)
    result.update(schema_version='openpine.order-group-soak.1',order_groups={'primary_bars':bars,'higher_bars':bars//4,'outputs':5,
        'two_retained_lots_and_activated_stop_limit':True,'cancel_before_stale_open_candidate':True,'reduce_cancels_protective_peer':True,
        'cancelled_parent_protection_not_resurrected':True,'final_close_all_freezes_three_lots':True,'final_position':'-7',
        'final_cash':'1736.52','final_equity':'1036.52','final_commission':'11.48'})
    atomic_json(Path(directory)/'evidence.json',result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=1000000);args=p.parse_args();print(run(args.directory,args.bars))
if __name__=='__main__':main()
