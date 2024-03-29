# Copyright (C) 2018, 2019, 2020, 2023, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, copy, gc, unittest, random, warnings, weakref
from sqlalchemy import create_engine
from entanglement import SyncManager, SyncServer, certhash_from_file, interface, SyncDestination
import entanglement.sql as sql
from entanglement.sql import SqlSyncDestination, sync_session_maker, SqlSyncRegistry, SqlSyncSession, SqlSynchronizable
from unittest import mock
from entanglement import transition
from entanglement import pki
import pytest
from .utils import test_port, settle_loop

@pytest.fixture()
def loop():
    return asyncio.get_event_loop()

@pytest.fixture(scope = 'module')
def registries():
    "Return the set of entanglement registries to use"
    return []

@pytest.fixture(autouse = True, scope = 'session')
def entanglement_basic_config():
    sql.internal.you_have_timeout = 0 #Send YouHave serial number updates immediately for testing
    warnings.filterwarnings('ignore', module = 'asyncio.sslproto')
    warnings.filterwarnings('ignore', module = 'asyncio.selector_events')
    sql.internal.sql_meta_messages.yield_between_classes = False
    for name in ('host1', 'host2', 'host3'):
        pki.host_cert(pki_dir, name,"")

@pytest.fixture(scope = 'module')
def requested_layout():
    return {
        'server': {
            'server': True,
            },
        'client': {
            'server': False,
            'connections': ['server']
            },
        }

class LayoutContext:

    def disconnect_all(self):
        for le in self.layout_entries:
            for d in le.manager.destinations:
                le.manager.remove_destination(d)
        settle_loop(self.loop)

    def connect_all(self, settle = True):
        for le in self.layout_entries:
            for d in le.destinations:
                d.connect_at = 0
                le.manager.add_destination(d)
        if settle: self.wait_connecting()

    def wait_connecting(self, allow_empty = False):
        connecting = []
        for e in self.layout_entries:
            connecting.extend(e.manager._connecting.values())
        if not connecting:
            if allow_empty: return
            raise ValueError("No connections are connecting")
        asyncio.get_event_loop().run_until_complete(asyncio.wait(connecting, timeout = 1.0))
        settle_loop(asyncio.get_event_loop(), timeout = 1.0)


def setup_manager(name, le, registries):
    "Given a layout entry, return a layout context"
    ctx = LayoutContext()
    ctx.port = test_port
    if 'port_offset' in le:
        ctx.port += le['port_offset']
    ctx.server = le.get('server', False)
    if le.get('server', False):
        cls = SyncServer
    else: cls = SyncManager
    ctx.name = name
    pki.host_cert(pki_dir, name, "")
    ctx.cert = "{p}/{h}.pem".format(p = pki_dir, h = name)
    ctx.key = "{p}/{h}.key".format(p = pki_dir, h = name)
    ctx.registries = []
    ctx.engine = create_engine('sqlite:///:memory:', echo = False)
    ctx.session = SqlSyncSession(bind = ctx.engine)
    # For each registry, copy the registry so that each manager gets a distinct copy
    # Also deal with declarative bases embedded in the registries list
    for r in registries:
        if isinstance(r, type) and \
           issubclass(r, SqlSynchronizable): # it's a declarative sync base
            r.registry.create_bookkeeping(ctx.engine)
            r.metadata.create_all(ctx.engine)
            r = r.registry
        r_new = type(r)()
        r_new.registry = r.registry
        r_new.operations = r.operations
        if hasattr(r_new, 'sessionmaker'):
            r_new.sessionmaker.configure(bind = ctx.engine)
        ctx.registries.append(r_new)

    ctx.manager = cls(cafile = pki_dir+"/ca.pem",
                      key = ctx.key,
                      cert = ctx.cert,
                      port = ctx.port,
                      loop = asyncio.get_event_loop(),
                      registries = ctx.registries
                      )
    if cls is SyncServer:
        if le.get('listen_ssl', True):
            ctx.manager.listen_ssl()
        ctx.unix_path = le.get('listen_unix', None)
        if ctx.unix_path:
            ctx.manager.listen_unix(ctx.unix_path)
    ctx.connections = le.get('connections', [])
    ctx.session.manager = ctx.manager
    ctx.destinations = []
    ctx.websocket = le.get('websocket', False)
    if ctx.websocket:
        def find_sync_destination( request, *args, **kwargs):
            return ctx.websocket_destination
        ctx.websocket_destination = SyncDestination(b'n' * 32, 'websocket')
        ctx.websocket_destination.connected_future = asyncio.get_event_loop().create_future()
        ctx.websocket_destination.on_connect(lambda: ctx.websocket_destination.connected_future.set_result(True))
        import tornado.web
        import tornado.httpserver
        from entanglement.websocket import SyncWsHandler
        ctx.web_app = tornado.web.Application([(r'/ws', SyncWsHandler)])
        ctx.http_server = tornado.httpserver.HTTPServer(ctx.web_app)
        ctx.http_server.listen(test_port+2) # right now run_js_test hard codes this
        ctx.web_app.sync_manager = ctx.manager
        ctx.web_app.find_sync_destination = find_sync_destination

    return ctx


def connect_layout(layout, destination_class):
    for name, le in layout.items():
        for connect_to in le.connections:
            assert connect_to in layout
            connect_to = layout[connect_to] # get the object not just the name
            d_out = destination_class(certhash_from_file(connect_to.cert),
                                    "{}->{}".format(connect_to.name, le.name),
                                    host = "127.0.0.1" if connect_to.server else None,
                                    server_hostname = connect_to.name)
            d_out.port = connect_to.port
            le.manager.add_destination(d_out)
            le.destinations.append(d_out)
            setattr(le, "to_"+connect_to.name, d_out)
            d_in = destination_class(certhash_from_file(le.cert),
                                      "{}<-{}".format(connect_to.name, le.name),
                                   host = "127.0.0.1" if le.server else None,
                                   server_hostname = le.name)
            d_in.port = le.port
            connect_to.manager.add_destination(d_in)
            connect_to.destinations.append(d_in)
            setattr(connect_to, 'to_'+le.name, d_in)
            setattr(le, 'from_'+connect_to.name, d_in)
            setattr(connect_to, "from_"+le.name, d_out)
            

def layout_fn(registries, requested_layout):
    layout_dict = {}
    destination_class = requested_layout.pop('destination_class', SqlSyncDestination)
    for name, layout_entry in requested_layout.items():
        layout_dict[name] = setup_manager(name, layout_entry, registries)
    connect_layout(layout_dict, destination_class)
    layout_dict['layout_entries'] = tuple(layout_dict.values())
    layout = LayoutContext()
    layout.__dict__ = layout_dict
    layout.wait_connecting(True)
   
    layout.loop = asyncio.get_event_loop()
    yield layout
    for e in layout.layout_entries:
        e.session.close()
        if e.websocket:
            e.http_server.stop()
        e.manager.close()
    layout_dict.clear()
    settle_loop(asyncio.get_event_loop())
    

pki_dir = "."


@pytest.fixture()
def layout(registries, requested_layout):
    yield from layout_fn(registries = registries, requested_layout = requested_layout)

@pytest.fixture(scope = 'module')
def layout_module(registries, requested_layout):
    yield from layout_fn(registries = registries, requested_layout = requested_layout)
    
