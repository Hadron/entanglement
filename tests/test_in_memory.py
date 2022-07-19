# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, dataclasses, pytest, uuid
from entanglement import *
from entanglement.memory import *
from .utils import settle_loop

class Registry(SyncStoreRegistry): pass

registry = Registry()
registry.register_syncable(SyncOwner.sync_type, SyncOwner)
@dataclasses.dataclass

@dataclasses.dataclass
class A( StoreInSyncStoreMixin):
    sync_registry = registry

    id: uuid.UUID = sync_property(dataclasses.field(default_factory = uuid.uuid4))

    sync_primary_keys = ('id', )
                                  
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
    
    
