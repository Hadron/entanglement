#!/usr/bin/python3
# Copyright (C) 2017, 2018, 2019, 2020, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from contextlib import contextmanager
import asyncio, gc, unittest, random, warnings, weakref
from sqlalchemy import create_engine
from entanglement import SyncManager, SyncServer, certhash_from_file, interface
import entanglement.sql as sql
from entanglement.sql import SqlSyncDestination, sync_session_maker, SqlSyncRegistry, SqlSyncSession
from unittest import mock
from entanglement import transition
import pytest

@contextmanager
def wait_for_call(loop, obj, method, calls = 1, trap_exceptions = False, timeout = 0.5):
    fut = loop.create_future()
    num_calls = 0
    def cb(*args, **kwargs):
        nonlocal num_calls
        # print(f'cb: {num_calls+1}/{calls} {args} {kwargs}')
        try: res = wraps(*args, **kwargs)
        except Exception as e:
            if trap_exceptions: fut.set_exception(e)
            raise
        if not fut.done():
            num_calls +=1
            if num_calls >= calls:
                fut.set_result(True)
        return res
                
    try:
        wraps = getattr(obj, method)
        with mock.patch.object(obj, method, new=cb):
            yield
            loop.run_until_complete(asyncio.wait_for(fut, timeout))
    except asyncio.TimeoutError:
        raise AssertionError("Timeout waiting for call to {} of {}".format(
            method, obj)) from None

class SqlFixture(unittest.TestCase):

    def setUp(self):
        if not hasattr(self, 'other_registries'):
            self.other_registries = []
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
                                 port = test_port,
                                 registries = [self.base.registry] + self.other_registries,
                                 loop = asyncio.get_event_loop())
        self.server.listen_ssl()
        # We do not listen ssl for this item
        self.manager = SyncServer(cafile = "ca.pem",
                                   cert = "host2.pem",
                                   key = "host2.key",
                                   loop = self.server.loop,
                                   registries = [self.manager_registry] + self.other_registries,
                                   port = test_port)
        self.loop = self.server.loop
        self.d1 = self.to_server = SqlSyncDestination(certhash_from_file("host1.pem"),
                                  "server", host = "127.0.0.1",
                                  server_hostname = "host1")
        self.d2 = self.to_client = SqlSyncDestination(certhash_from_file("host2.pem"),
                                  "manager")
        self.server.add_destination(self.d2)
        self.server.session.add(self.d2)
        self.manager.add_destination(self.d1)
        with wait_for_call(self.loop,
                           sql.internal.sql_meta_messages,
                           'handle_i_have', 2):
            self.manager.run_until_complete(asyncio.wait(self.manager._connecting.values(), timeout = 1.0))

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
    def loop_busy(loop):
        if len(loop._ready) > 0: return True
        event_list = loop._selector.select(0.03)
        if len(event_list) > 0: return True
        if len(loop._scheduled) == 0: return False
        timeout = loop._scheduled[0]._when
        timeout -= loop.time()
        return timeout <= 0
    try:
        timeout_fut =loop.create_task(asyncio.sleep(timeout))
        done = False
        while not done:
            loop.call_soon(loop.stop)
            loop.run_forever()
            if timeout_fut.done(): break
            done = not loop_busy(loop)
        #after loop
        if timeout_fut.done():
            raise AssertionError("Loop failed to settle in {} seconds".format(timeout))
    finally:
        timeout_fut.cancel()

@pytest.fixture()
def sql_fixture(base_fixture):
    s = SqlFixture()
    s.base = base_fixture
    s.manager_registry = SqlSyncRegistry()
    s.manager_registry.registry = base_fixture.registry.registry
    s.setUp()
    yield s
    s.tearDown()

@pytest.fixture()
def server_session(sql_fixture):
    s = SqlSyncSession(sql_fixture.e1)
    s.manager = sql_fixture.server
    yield s
    s.close()

@pytest.fixture()
def manager_session(sql_fixture):
    s = SqlSyncSession(sql_fixture.e2)
    s.manager = sql_fixture.manager
    yield s
    s.close()


@contextmanager
def transitions_tracked_as(manager):
    old_dict = transition.transition_objects
    if not hasattr(manager, 'transition_objects'):
        manager.transition_objects = weakref.WeakKeyDictionary()
    transition.transition_objects = manager.transition_objects
    try:
        yield
    finally:
        transition.transition_objects = old_dict

@contextmanager
def transitions_partitioned():
    old_receive = SyncManager._sync_receive
    old_sft = transition.TransitionTrackerMixin.store_for_transition
    def receive_wrap(manager, *args, **kwargs):
        def wrap_sft(obj, *args, **kwargs):
            res = old_sft(obj, *args, **kwargs)
            obj.transition_tracked_objects = obj.transition_tracked_objects #Collapse the memoization so that this dictionary is always used for this instance
            return res
        with transitions_tracked_as(manager), \
             mock.patch.object(transition.TransitionTrackerMixin, 'store_for_transition',
                               new = wrap_sft):
            res = old_receive(manager, *args, **kwargs)
        return res
    with mock.patch.object(SyncManager, '_sync_receive',
                           new = receive_wrap):
        yield
        

def random_port():
    return random.randrange(10000,60000)

class FloodableSyncOwner:
    "A syncowner class that will allow objects to flood even if they are not sql objects; simply set the owner to this in sync_constructed"

    destination = None
    @property
    def dest_hash(self):
        if self.destination is not None:
            return self.destination.dest_hash
        return None
    def sync_encode_value(self):
        return "flood"
    
    

test_port = random_port()
__all__ = "FloodableSyncOwner wait_for_call SqlFixture sql_fixture server_session manager_session settle_loop transitions_tracked_as transitions_partitioned test_port".split(' ')



