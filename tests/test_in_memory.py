# Copyright (C) 2018, 2019, 2020, 2023, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, dataclasses, pytest, uuid
from entanglement import *
from entanglement.memory import *
from entanglement.filter import *
from .utils import settle_loop
from . import conftest

class Registry(SyncStoreRegistry): pass

registry = Registry()
registry.register_syncable(SyncOwner.sync_type, SyncOwner)


@dataclasses.dataclass
class A( StoreInSyncStoreMixin):
    sync_registry = registry

    id: uuid.UUID = sync_property(dataclasses.field(default_factory = uuid.uuid4))

    sync_primary_keys = ('id', )

class B(A):
    sync_store_with = A

    value: int = sync_property()
@pytest.fixture()
def filter_layout(registries, requested_layout):
    rl = requested_layout
    rl['destination_class'] = FilteredSyncDestination
    yield from conftest.layout_fn(registries=registries, requested_layout=rl)
    
@pytest.fixture(scope='module')
def registries():
    return [registry]

def test_send_object(layout, loop):
    a = A()
    owner = SyncOwner()
    a._sync_owner = owner.id
    registry_server = layout.server.registries[0]
    registry_server.add_to_store(a)
    registry_server.add_to_store(owner)
    layout.server.manager.synchronize(owner)
    layout.server.manager.synchronize(a)
    settle_loop(loop, 1.5)
    registry_client = layout.client.registries[0]
    store_client = registry_client.store_for_class(A)
    assert a.id in store_client
    a_client = store_client[a.id]
    assert a_client.sync_owner.id == owner.id
    
    
def test_filter(filter_layout, loop):
    layout = filter_layout
    registry_server = layout.server.registries[0]
    b1 = B()
    b1.value = 30
    owner = SyncOwner()
    b1._sync_owner = owner.id
    registry_server.add_to_store(b1)
    registry_server.add_to_store(owner)
    layout.server.to_client.add_filter(
        Filter(lambda o: True, store=registry_server.store_for_class(B)))
    layout.server.to_client.add_filter(SyncOwnerFilter(registry_server))
    loop.run_until_complete(layout.server.to_client.send_initial_objects(layout.server.manager))
    settle_loop(loop)
    b_client_store = layout.client.registries[0].store_for_class(B)
    assert b1 in b_client_store
    # Now confirm that if we add a filter that the object fails, it is removed on next sync
    layout.server.to_client.add_filter(Filter(
        lambda o: o.value < 40, type=B))
    b1.value = 40
    layout.server.manager.synchronize(b1)
    settle_loop(loop)
    assert b1 not in b_client_store
    
    
