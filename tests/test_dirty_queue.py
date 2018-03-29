# Copyright (C) 2018, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import pytest
from entanglement.protocol.dirty import DirtyQueue, DirtyMember
from entanglement.interface import Synchronizable, sync_property

class MockSync(Synchronizable):
    id = sync_property()
    sync_primary_keys = ('id',)

cur_id = 10000
def new_id():
    global cur_id
    cur_id += 1
    return cur_id

def add_item(q, q_dict, desc):
    if not isinstance(desc, tuple):
        id = new_id()
        priority = desc
    else: id, priority = desc
    obj = MockSync()
    obj.id = id
    elt = DirtyMember(obj, 'sync', None, None, priority)
    q.add_or_replace(elt)
    if id in q_dict:
        q_dict[id] = min(q_dict[id], priority)
    else: q_dict[id] = priority
    

class popmark: pass
popmark = popmark()


def pop_1(q, q_dict):
    assert len(q_dict) > 0
    assert len(q) == len(q_dict)
    min_priority = min(q_dict.values())
    q_pop = q.pop()
    print("Expecting pop of priority {e}, popped priority {g}".format(
        e = min_priority,
        g = q_pop.priority))
    assert q_pop.priority == min_priority
    del q_dict[q_pop.obj.id]

def pop_all(q, q_dict):
    for l in range(len(q_dict)):
        pop_1(q, q_dict)
            
@pytest.mark.parametrize('in_',
                         [[
                             4,3,2,1],
                          [1,2,3,4],
                          [4,3,popmark, 2, 1],
                          [(1,3), (2,3), (1,2)],
                          [3,3,4,2,4,2,1,3],
                          [2, 2, popmark, 3, 1, popmark, 2]
                          ])
def test_dirty_queue(in_):
    q = DirtyQueue()
    q_dict = {}
    for l in in_:
        if l is popmark:
            pop_1(q, q_dict)
        else:
            add_item(q, q_dict, l)
    pop_all(q, q_dict)
    
