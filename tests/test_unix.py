# Copyright (C) 2019, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from entanglement.interface import Synchronizable, sync_property, SyncRegistry
from entanglement import DestHash, OutgoingUnixDestination, SyncDestination
import os, logging, pytest
from .utils import settle_loop

@pytest.fixture()
def requested_layout():
    return dict(
        server = {'server': True,
                  'listen_ssl': False,
                  'listen_unix': "unix.sock"},
        client = dict()
        )

@pytest.fixture()
def registries():
    return [reg]

reg = SyncRegistry()

class Syncable(Synchronizable):

    sync_registry = reg

    id = sync_property()

    sync_primary_keys = ('id',)
    
def connect_unix(layout):
    dh = DestHash(os.urandom(32))
    dh_in = DestHash.from_unix_dest_info("unix.sock", os.getpid(), os.getuid(), os.getgid())
    d_in = SyncDestination(dh_in, "Incoming")
    layout.server.manager.add_destination(d_in)
    d = OutgoingUnixDestination(dh, "to server", "unix.sock")
    fut = layout.client.manager.add_destination(d)
    settle_loop(layout.loop)
    assert len(layout.client.manager.connections) == 1
    return fut.result()

def test_unix_server_starts(layout):
    pass
    

def test_unix_connection(layout):
    connect_unix(layout)
    t = Syncable()
    t.id = 45
    layout.client.manager.synchronize(t)
    settle_loop(layout.loop)
    
logging.getLogger('entanglement.protocol').setLevel(10)
