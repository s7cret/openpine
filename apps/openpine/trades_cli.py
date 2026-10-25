"""Read a bounded page of committed trade history locally or from the running server."""
import argparse,re,sys
from pathlib import Path
import apsw
from packages.contracts import OpenPineError
from packages.contracts.wire import canonical

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--id',required=True,help='completed job ID')
 parser.add_argument('--server',help='running loopback server URL');parser.add_argument('--state-dir',type=Path,default=Path('.runtime/jobs'))
 parser.add_argument('--start',type=int,default=0);parser.add_argument('--limit',type=int,default=100);args=parser.parse_args(argv)
 if not re.fullmatch(r'[0-9a-f]{32}',args.id) or args.start<0 or not 1<=args.limit<=200:parser.error('Use a valid job ID, start>=0 and limit 1..200.')
 try:
  if args.server:
   from .http_client import LocalClient
   value=LocalClient(args.server).call(f'/api/jobs/{args.id}/trades?start={args.start}&limit={args.limit}')
  else:
   if not (args.state_dir/'catalog.sqlite3').is_file():raise ValueError('The state directory has no existing catalog.')
   from .bootstrap import local_services
   with local_services(args.state_dir) as services:value=services.queries.trades(args.id,args.start,args.limit)
  code=0
 except (ValueError,OSError,apsw.Error,OpenPineError) as error:
  value={'status':'rejected','diagnostics':[{'code':'TRADE_HISTORY_UNAVAILABLE','message':str(error)}]};code=2
 sys.stdout.write(canonical(value).decode()+'\n');return code
