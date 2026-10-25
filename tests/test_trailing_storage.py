from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from apps.openpine.bootstrap import local_services
from packages.storage.trades import TradeSink,event_rows,MAX_EVENT_BYTES
from packages.execution.exit_views import chart_events,export_events,CHART_EVENT_BYTES
from packages.contracts.wire import canonical

class TrailingStorageTests(unittest.TestCase):
    def event(self,index,value):return {'sequence':index,'bar_index':index,'time':index*60000,'kind':'mark','value':value}

    def test_wide_rows_and_byte_sealing_keep_exact_events_on_small_connection(self):
        with tempfile.TemporaryDirectory() as d,local_services(Path(d)/'live') as services:
            sink=TradeSink(services.objects,Path(d)/'writer',memory_mb=14);value={'wide':'Ж'*40000}
            with patch('packages.storage.trades.PART_BYTES',200000):
                for i in range(8):sink.emit([self.event(i,value)])
            journal=sink.finish();sink.close();self.assertGreater(len(journal['parts']),1)
            self.assertEqual(list(event_rows(services.objects,journal)),[self.event(i,value) for i in range(8)])

    def test_pending_wide_row_restores_buffer_size_without_changing_prefix_schema(self):
        with tempfile.TemporaryDirectory() as d,local_services(Path(d)/'live') as services:
            value={'wide':'x'*90000};first=TradeSink(services.objects,Path(d)/'first',memory_mb=14)
            first.emit([self.event(0,value)]);saved=first.checkpoint(1)
            self.assertEqual(first.bytes_written,(Path(d)/'first/journal.csv').stat().st_size);first.close()
            second=TradeSink(services.objects,Path(d)/'second',memory_mb=14);second.restore(saved,1);second.emit([self.event(1,value)])
            second.checkpoint(2);self.assertEqual(second.bytes_written,(Path(d)/'second/journal.csv').stat().st_size)
            journal=second.finish();second.close();self.assertEqual(list(event_rows(services.objects,journal)),[self.event(i,value) for i in range(2)])

    def test_event_byte_limit_rejects_before_advancing_the_journal(self):
        with tempfile.TemporaryDirectory() as d,local_services(Path(d)/'live') as services:
            sink=TradeSink(services.objects,Path(d)/'writer')
            with self.assertRaises(ValueError):sink.emit([self.event(0,{'too_wide':'x'*MAX_EVENT_BYTES})])
            self.assertEqual((sink.rows,sink.records),(0,0));self.assertIsNone(sink.stream)

    def test_chart_projection_is_byte_bounded_and_closes_the_reader(self):
        closed=[]
        def events():
            try:
                for i in range(10000):yield {'sequence':i,'kind':'fill','value':{'payload':'x'*1000}}
            finally:closed.append(True)
        visible,truncated,size=chart_events(events());self.assertTrue(truncated);self.assertEqual(closed,[True]);self.assertLessEqual(len(canonical(visible)),size);self.assertLessEqual(size,CHART_EVENT_BYTES)
        self.assertLess(len(visible),5000)

    def test_chart_removes_configuration_and_export_falls_back_instead_of_truncating(self):
        mark={k:[] for k in ('protections','entries','lots','order_groups')};mark.update(position_size='0',position_avg_price=None,open_lots=0,
            exit_states=[{'order_id':'x','exit_id':'X','from_entry':'L','configuration':{'unused':'x'*100000},'targets':[]}])
        visible,truncated,size=chart_events(iter([self.event(0,mark)]));self.assertFalse(truncated);self.assertNotIn('configuration',visible[0]['value']['exit_states'][0]);self.assertLess(size,1000)
        closed=[]
        def events():
            try:
                for i in range(100):yield self.event(i,{'payload':'x'*100000})
            finally:closed.append(True)
        self.assertIsNone(export_events(events()));self.assertEqual(closed,[True])

if __name__=='__main__':unittest.main()
