#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from contextlib import contextmanager
import asyncio, gc, unittest, warnings
from sqlalchemy import create_engine
from hadron.entanglement import SyncManager, SyncServer, certhash_from_file
import hadron.entanglement.sql as sql
from hadron.entanglement.sql import SqlSyncDestination, sync_session_maker
from unittest import mock

@contextmanager
def wait_for_call(loop, obj, method, calls = 1):
    fut = loop.create_future()
    num_calls = 0
    def cb(*args, **kwargs):
        nonlocal num_calls
        if not fut.done():
            num_calls +=1
            if num_calls >= calls:
                fut.set_result(True)
        return wraps(*args, **kwargs)
    try:
        wraps = getattr(obj, method)
        with mock.patch.object(obj, method,
                               new= cb):
            yield
            loop.run_until_complete(asyncio.wait_for(fut, 0.5))
    except  asyncio.futures.TimeoutError:
        raise AssertionError("Timeout waiting for call to {} of {}".format(
            method, obj)) from None

class SqlFixture(unittest.TestCase):

    def setUp(self):
        sql.internal.you_have_timeout = 0 #Send YouHave serial number updates immediately for testing
        warnings.filterwarnings('ignore', module = 'asyncio.sslproto')
        warnings.filterwarnings('ignore', module = 'asyncio.selector_events')
        self.e1 = create_engine('sqlite:///:memory:', echo = False)
        self.e2 = create_engine('sqlite:///:memory:', echo = False)
        Session = sync_session_maker()
        self.session = Session(bind = self.e2)
        self.base.registry.sessionmaker.configure(bind = self.e1)
        self.manager_registry.sessionmaker.configure( bind = self.e2)
        self.base.metadata.create_all(bind = self.e1)
        self.base.metadata.create_all(bind = self.e2)
        self.base.registry.create_bookkeeping(self.e1)
        self.base.registry.create_bookkeeping(self.e2)
        self.server = SyncServer(cafile = "ca.pem",
                                 cert = "host1.pem", key = "host1.key",
                                 port = 9120,
                                 registries = [self.base.registry])
        self.manager = SyncManager(cafile = "ca.pem",
                                   cert = "host2.pem",
                                   key = "host2.key",
                                   loop = self.server.loop,
                                   registries = [self.manager_registry],
                                   port = 9120)
        self.loop = self.server.loop
        self.d1 = self.to_server = SqlSyncDestination(certhash_from_file("host1.pem"),
                                  "server", host = "127.0.0.1",
                                  server_hostname = "host1")
        self.d2 = self.to_client = SqlSyncDestination(certhash_from_file("host2.pem"),
                                  "manager")
        self.server.add_destination(self.d2)
        self.manager.add_destination(self.d1)
        with wait_for_call(self.loop,
                           sql.internal.sql_meta_messages,
                           'handle_i_have', 4):
            self.manager.run_until_complete(asyncio.wait(self.manager._connecting.values()))

        sql.internal.sql_meta_messages.yield_between_classes = False


    def tearDown(self):
        self.manager.close()
        self.server.close()
        self.session.close()
        del self.session
        del self.server
        del self.manager
        del self.d1
        del self.d2
        gc.collect()

def settle_loop(loop, timeout = 0.5):
    "Call the loop while it continues to have callbacks, waiting at most timeout seconds"
    try:
        timeout_fut =loop.create_task(asyncio.sleep(timeout))
        while len(loop._ready) > 0:
            loop.call_soon(loop.stop)
            loop.run_forever()
            if timeout_fut.done(): break
        #after loop
        if timeout_fut.done():
            raise AssertionError("Loop failed to settle in {} seconds".format(timeout))
    finally:
        timeout_fut.cancel()
        
