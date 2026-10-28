"""Finite deferred checkpoint records; no persistence dependency in the runtime."""
from collections.abc import Mapping,Sequence

class RecordSequence(Sequence):
    def __init__(self,count,read):self.count,self.read=count,read
    def __len__(self):return self.count
    def __getitem__(self,index):
        if type(index) is not int:raise TypeError('Checkpoint records need an integer index.')
        if index<0:index+=self.count
        if not 0<=index<self.count:raise IndexError(index)
        return self.read(index)
    def __deepcopy__(self,memo):return self

class RecordMapping(Mapping):
    def __init__(self,keys,read):self.names,self.read=tuple(keys),read
    def __len__(self):return len(self.names)
    def __iter__(self):return iter(self.names)
    def __getitem__(self,key):
        if key not in self.names:raise KeyError(key)
        return self.read(key)
    def __deepcopy__(self,memo):return self
