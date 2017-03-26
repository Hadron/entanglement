#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.



import asyncio, logging, ssl, time
from . import protocol
from .util import CertHash, certhash_from_file
from .bandwidth import BwLimitProtocol
from .interface import WrongSyncDestination, UnregisteredSyncClass
from . import interface

logger = protocol.logger


class SyncManager:

    '''A SyncManager manages connections to other Synchronization
    endpoints.  A SyncManager presents a single identity to the rest
    of the world represented by a private key and certificate.
    SyncManager includes the logic necessary to act as a client;
    SyncServer extends SyncManager with logic necessary to accept
    connections.
    '''

    def __init__(self, cert, port, *, key = None, loop = None,
                 capath = None, cafile = None,
                 registries = []):
        if loop:
            self.loop = loop
            self.loop_allocated = False
        else:
            self.loop = asyncio.new_event_loop()
            self.loop_allocated = True
        self._transports = []
        self._destinations = {}
        self._connections = {}
        self._connecting = {}
        self._ssl = self._new_ssl(cert, key = key,
                                 capath = capath, cafile = cafile)
        self.registries = registries
        self.registries.append(interface.error_registry)
        for r in list(self.registries):
            if hasattr(r, 'inherited_registries'):
                self.registries.extend(r.inherited_registries)
            self.registries = set(self.registries) # Remove duplicates
        self.port = port
        for r in self.registries: r.associate_with_manager(self)

    def _new_ssl(self, cert, key, capath, cafile):
        sslctx = ssl.create_default_context()
        sslctx.load_cert_chain(cert, key)
        self.cert_hash = certhash_from_file(cert)
        sslctx.load_verify_locations(cafile=cafile, capath = capath)
        return sslctx

    def _protocol_factory_client(self, dest):
        "This is more of a factory factory than a factory.  Construct a protocol object for a connection to a given outgoing SyncDestination"
        return lambda: BwLimitProtocol(chars_per_sec = 10000000,
                                       bw_quantum = 0.1, loop = self.loop,
                                       upper_protocol = protocol.SyncProtocol(manager = self, dest = dest))

    def _protocol_factory_server(self):
        "Factory factory for server connections"
        return lambda: BwLimitProtocol(
            chars_per_sec = 10000000,
            bw_quantum = 0.1,
            loop = self.loop,
            upper_protocol = protocol.SyncProtocol(manager = self, incoming = True))
    

    def synchronize(self, objects):
        for o in objects:
            for c in self._connections.values():
                c.synchronize_object(o)
                
    async     def _create_connection(self, dest):
        "Create a connection on the loop.  This is effectively a coroutine."
        if not hasattr(self, 'loop'): return
        loop = self.loop
        delta = 1
        close_transport = None #Close this transport if we fail to
        task = self._connecting[dest.cert_hash] #our task
        #connect There are two levels of try; the outer catches exceptions
        #that end all connection attempts and cleans up the cache of
        #destinations we're connecting to.
        try:
            while True: # not connected
                if dest.connect_at > loop.time():
                    logger.info("Waiting until {time} to connect to {dest}".format(
                    time = time.ctime(dest.connect_at),
                    dest = dest))
                    delta = dest.connect_at-loop.time()
                    await asyncio.sleep(delta)
                delta = min(2*delta, 10*60)
                try:
                    logger.debug("Connecting to {hash} at {host}".format(
                        hash = dest.cert_hash,
                        host = dest.host))
                    transport, bwprotocol = await \
                                          loop.create_connection(self._protocol_factory_client(dest),
                                                                 port = self.port, ssl = self._ssl,
                                                                 host = dest.host,
                                                                 server_hostname = dest.server_hostname)
                    logger.debug("Transport connection to {dest} made".format(dest = dest))
                    close_transport = transport
                    protocol = bwprotocol.protocol
                    if protocol.cert_hash != dest.cert_hash:
                        raise WrongSyncDestination(dest = dest, got_hash = protocol.cert_hash)
                
                    await dest.connected(self, protocol, bwprotocol = bwprotocol)
                    self._connections[dest.cert_hash] = protocol
                    close_transport = None
                    logger.info("Connected to {hash} at {host}".format(
                        hash = dest.cert_hash,
                        host = dest.host))
                    dest.connect_at = loop.time()+delta
                    return transport, protocol
                except asyncio.futures.CancelledError:
                    logger.debug("Connection to {dest} canceled".format(dest = dest))
                    raise
                except (SyntaxError, TypeError, LookupError, ValueError, WrongSyncDestination) as e:
                    logger.exception("Connection to {} failed".format(dest.cert_hash))
                    raise
                except:
                    logger.exception("Error connecting to  {}".format(dest))
                    dest.connect_at = loop.time() + delta
        finally:
            if self._connecting.get(dest.cert_hash, None) == task:
                del self._connecting[dest.cert_hash]
            if close_transport: close_transport.close()

    
                

    async def _incoming_connection(self, protocol):
        old = None
        task = None
        if protocol.cert_hash not in self._destinations:
            logger.error("Unexpected connection from {}".format(protocol.cert_hash))
            protocol.close()
        protocol.dest = self._destinations[protocol.cert_hash]
        dest = protocol.dest
        if self.cert_hash == dest.cert_hash:
            logger.debug("Self connection to {}".format(dest.cert_hash))
            return
        if dest.cert_hash in self._connections:
            logger.warning("Replacing existing connection to {}".format(dest))
            self._connections[dest.cert_hash].close()
            del self._connections[dest.cert_hash]
        if dest.cert_hash in self._connecting:
            logger.info("Replacing existing connection in progress to {}".format(dest))
            old = self._connecting[dest.cert_hash]
        try:
            task = self.loop.create_task(dest.connected(self, protocol,
                                                        bwprotocol = protocol.bwprotocol))
            self._connecting[dest.cert_hash] = task
            if old: old.cancel()
            await self._connecting[dest.cert_hash]
            self._connections[dest.cert_hash] = protocol
            logger.info("New incoming connection from {}".format(dest))
        finally:
            if dest.cert_hash in self._connecting and self._connecting[dest.cert_hash] == task:
                del self._connecting[dest.cert_hash]

    async def _connection_lost(self, protocol, exc):
        if exc is None:
            exc = EOFError()
        if self._connections.get(protocol.dest.cert_hash,None)  == protocol:
            del self._connections[protocol.dest.cert_hash]
            logger.exception("Connection to {} lost:".format(protocol.dest),
                             exc_info = exc)
            if protocol.dest.host is None: return
            self._connecting[protocol.dest.cert_hash] = self.loop.create_task(self._create_connection(protocol.dest))
            
                    
                
    def add_destination(self, dest):
        if dest.cert_hash is None or dest.name is None:
            raise ValueError("cert_hash and name are required in SyncDestination before adding")
        if dest.cert_hash in self._destinations:
            raise KeyError("{} is already a destination".format(repr(dest)))
        self._destinations[dest.cert_hash] = dest
        assert dest.protocol is None
        assert dest.cert_hash not in self._connecting
        if dest.host is None: return
        self._connecting[dest.cert_hash] = self.loop.create_task(self._create_connection(dest))
        return self._connecting[dest.cert_hash]

    def remove_destination(self, dest):
        assert dest.cert_hash in self._destinations
        logger.info("Removing destination {}".format(dest))
        if dest.cert_hash in self._connections:
            self._connections[dest.cert_hash].close()
            dest.protocol = None
            del self._connections[dest.cert_hash]
        if dest.cert_hash in self._connecting:
            self._connecting[dest.cert_hash].cancel()
            del self._connecting[dest.cert_hash]
        del self._destinations[dest.cert_hash]

    def run_until_complete(self, *args):
        return self.loop.run_until_complete(*args)

    def _sync_receive(self, msg, protocol):
        info = {'protocol': protocol,
                'manager': self}
        if protocol.dest: info['sender'] = protocol.dest
        self._validate_message(msg)
        cls, registry = self._find_registered_class(msg['_sync_type'])
        if self.should_listen(msg, cls, registry) is not True:
            # Failure should raise because ignoring an exception takes
            # active work, leading to a small probability of errors.
            # However, active authorization should be an explicit true
            # not falling off the end of a function.
            raise SyntaxError("should_listen must either return True or raise")
        if msg['_sync_authorized'] != self:
            raise SyntaxError("When SyncManager.should_listen is overwridden, you must call super().should_listen")
        del msg['_sync_authorized']
        try:
            registry.sync_receive(cls.sync_receive(msg, **info), **info)
        except Exception as e:
            registry.exception_receiving(e, sync_type = cls, **info)
            logger.exception("Error receiving a {}".format(cls.__name__),
exc_info = e)

    def _validate_message(self, msg):
        if not isinstance(msg, dict):
            raise protocol.MessageError('Message is a {} not a dict'.format(msg.__class__.__name__))
        for k in msg:
            if k.startswith('_') and k not in protocol.sync_magic_attributes:
                raise interface.SyncBadEncodingError('{} is not a valid attribute in a sync message'.format(k), msg = msg)

    def should_listen(self, msg, cls, registry):
        msg['_sync_authorized'] = self #To confirm we've been called.
        if registry.should_listen(msg, cls)is not True:
            raise SyntaxError('should_listen must return True or raise')
        if cls.sync_should_listen(msg) is not True:
            raise SyntaxError('sync_should_listen must return True or raise')
        return True
    
    def _find_registered_class(self, name):
        for reg in self.registries:
            if name in reg.registry: return reg.registry[name], reg
        raise UnregisteredSyncClass('{} is not registered for this manager'.format(name))
    
    def close(self):
        if not hasattr(self,'_transports'): return
        for c in self._connections.values():
            c.close()
        self._connections = {}
        for t in self._transports:
            if t(): t().close()
        if self.loop_allocated:
            self.loop.call_soon(self.loop.stop)
            self.loop.run_forever()
            #Two trips through the loop because of ssl
            self.loop.call_soon(self.loop.stop)
            self.loop.run_forever()
            self.loop.close()
        del self._transports
        del self.loop

    def __del__(self):
        self.close()

    @property
    def connections(self):
        "Return a list of all active protocol objects"
        return list(self._connections.values())

class SyncServer(SyncManager):

    "A SyncManager that accepts incoming connections"

    def __init__(self, cert, port, *, host = None, cafile = None, capath = None,
                 key = None, **kwargs):
        super().__init__(cert, port, capath = capath,
                         cafile = cafile, key = key,
                         **kwargs)
        self.host = host
        self._server = None
        self._ssl_server = self._new_ssl(cert, key = key, cafile = cafile,
                                         capath = capath)
        self._ssl_server.check_hostname = False
        self._server = self.loop.run_until_complete(self.loop.create_server(
            self._protocol_factory_server(),
                        host = host,
            port = port,
            ssl = self._ssl_server,
            reuse_address = True, reuse_port = True))

    def close(self):
        if hasattr(self, '_server') and self._server:
            self._server.close()
            self._server = None
        super().close()

        

class SyncDestination:

    '''A SyncDestination represents a SyncManager other than ourselves that can receive (and generate) synchronizations.  The Synchronizable and subclasses of SyncDestination must cooperate to make sure that receiving and object does not create a loop by trying to Synchronize that object back to the sender.  One solution is for should_send on SyncDestination to return False (or raise) if the outgoing object is received from this destination.'''

    def __init__(self, cert_hash = None, name = None, *,
                 host = None, bw_per_sec = 10000000,
                 server_hostname = None):
        if server_hostname is None: server_hostname = host
        self.host = host
        self.cert_hash = CertHash(cert_hash)
        self.name = name
        self.server_hostname = server_hostname
        self.bw_per_sec = bw_per_sec
        self.protocol = None
        self.connect_at = 0
        
    def __repr__(self):
        return "<SyncDestination {{name: '{name}', hash: {hash}}}".format(
            name = self.name,
            hash = self.cert_hash)

    def should_send(self, obj, manager , **kwargs):
        return True

    def should_listen(self, msg, manager, cls, **kwargs):
        '''Must return True or raise'''
        return True

    

    async def connected(self, manager, protocol, bwprotocol):
        '''Interface point; called by manager when an outgoing or incoming
        connection is made to the destination.  Except in the case of
        an unknown destination connecting to a server, the destination
        is known and already in the manager's list of connecting
        destinations.  The destination will not be added to the
        manager's list of connections until this coroutine returns
        true.  However, incoming synchronizations will be processed
        and will result in calls to the destination's should_listen
        method.  If this raises, the connection will be closed and aborted.
        '''
        self.protocol = protocol
        self.bwprotocol = bwprotocol
        bwprotocol.bw_per_quantum = self.bw_per_sec*bwprotocol.bw_quantum
        return
    
