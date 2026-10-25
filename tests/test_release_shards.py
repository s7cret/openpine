import unittest
from tools.release_gate import ROOT,PRIORITY
from tools.test_shards import partition,weights

class ReleaseShardTests(unittest.TestCase):
    def modules(self):return [*PRIORITY,*sorted(p.name for p in (ROOT/'tests').glob('test_*.py') if p.name not in PRIORITY)]

    def test_measured_duration_partition_covers_every_module_exactly_once(self):
        modules=self.modules();measured,default=weights();self.assertFalse(set(measured)-set(modules))
        for count in (1,2,3,7,16):
            parts=partition(modules,count);flat=[n for p in parts for n in p]
            self.assertEqual(sorted(flat),sorted(modules));self.assertEqual(len(flat),len(set(flat)));self.assertEqual(parts,partition(modules,count))
            for row in parts:self.assertEqual(row,sorted(row,key=modules.index))

    def test_longest_first_balances_known_weights_and_preserves_single_gate_order(self):
        modules=['test_a.py','test_b.py','test_c.py','test_d.py'];estimate=dict(zip(modules,(90,60,30,30),strict=True))
        self.assertEqual(partition(modules,1,estimate),[modules]);self.assertEqual(partition(modules,2,estimate),[['test_a.py','test_d.py'],['test_b.py','test_c.py']])
        measured,default=weights();parts=partition(self.modules(),3);loads=[sum(measured.get(n,default) for n in p) for p in parts]
        self.assertLessEqual(max(loads)-min(loads),max(measured.get(n,default) for n in self.modules()))

    def test_invalid_count_duplicate_names_paths_and_durations_fail_closed(self):
        for modules,count,estimated in ((['test_a.py'],0,{}),(['test_a.py'],True,{}),(['test_a.py']*2,2,{}),(['../test_a.py'],2,{}),(['test_a.py'],2,{'test_a.py':0})):
            with self.assertRaises(ValueError):partition(modules,count,estimated)

if __name__=='__main__':unittest.main()
