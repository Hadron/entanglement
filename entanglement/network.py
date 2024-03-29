#!/usr/bin/python3
# Copyright (C) 2017, 2018, 2019, 2020, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.



import asyncio, logging, ssl, time, weakref
import functools
from . import protocol
from .util import DestHash, certhash_from_file
from .bandwidth import BwLimitProtocol
from .interface import WrongSyncDestination, UnregisteredSyncClass, SyncNotConnected
from . import interface
from .operations import SyncOperation
logger = protocol.logger

no_traceback_connection_failures = (OSError, EOFError)


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
        if cert is not None:
            self._ssl = self._new_ssl(cert, key = key,
                                 capath = capath, cafile = cafile)
        else:
            self._ssl = None
            self.cert_hash = None
        self.registries = registries
        self.registries.append(interface.error_registry)
        for r in list(self.registries):
            if hasattr(r, 'inherited_registries'):
                self.registries.extend(r.inherited_registries)
        self.registries = set(self.registries) # Remove duplicates
        self.port = port
        for r in self.registries: r.associate_with_manager(self)

    def _new_ssl(self, cert, key, capath, cafile, server=False):
        sslctx = ssl.create_default_context(cafile = cafile, capath = capath, purpose=( ssl.Purpose.CLIENT_AUTH if server else ssl.Purpose.SERVER_AUTH))
        if server:
            sslctx.verify_mode = ssl.VerifyMode.CERT_OPTIONAL
        sslctx.load_cert_chain(cert, key)
        self.cert_hash = certhash_from_file(cert)
        return sslctx

    def _protocol_factory_client(self, dest, protocol = protocol.SyncProtocol):
        "This is more of a factory factory than a factory.  Construct a protocol object for a connection to a given outgoing SyncDestination"
        return lambda: BwLimitProtocol(chars_per_sec = 10000000,
                                       bw_quantum = 0.1, loop = self.loop,
                                       upper_protocol = protocol(manager = self, dest = dest))



    def synchronize(self, obj, *,
                    destinations = None,
                    exclude = [],
                    operation = 'sync',
                    attributes_to_sync = None,
                    response = False,
                    response_for = None,
                    priority = None):
        '''The primary interface for synchronizing an object.

        Destinations must be destinations in self.destinations;
        exclude is a set of destinations to exclude.  If attributes is
        set only these attributes are included in outgoing messages.
        If response is True, returns a future that will receive the
        response from this message.  Response may also be a future to
        associate with the object.  Response_for should be passed the
        response object from the context when responding to a message
        in a flood

        '''
        future = None
        if response and response_for:
            raise ValueError('Response and response_for cannot both be true')
        if response:
            if isinstance(response, asyncio.Future): future = response
            else: future = self.loop.create_future()
            response_for = protocol.ResponseReceiver()
            response_for.add_future(future)
            
        if isinstance(obj, interface.Synchronizable) and (obj.sync_receive.__func__ is not interface.Synchronizable.sync_receive.__func__):
            raise SyntaxError('Must not override sync_receive in {}'.format(obj.__class__.__name__))
        if priority is None: priority = obj.sync_priority
        if destinations is None:
            destinations = filter(lambda  x: x.dest_hash in self._connections,
                                  self.destinations)
        valid_dest_hashes = set(self._connections.keys()).union( set(self._connecting.keys()))
        should_send_destinations = set()
        info = {}
        info['manager'] = self
        info['response_for'] = response_for
        info['sync_type'] = obj.__class__
        cls, registry = self._find_registered_class( obj.sync_type)
        info['registry'] = registry
        if isinstance(operation, SyncOperation):
            info['operation'] = operation
        else:
            operation = registry.get_operation(operation)
            info['operation'] = operation
        if response_for:
            destinations = list(destinations)
            response_for.sending_to(filter(lambda d: not d in exclude, destinations))
        for d in destinations:
            if d in exclude: continue
            if self.should_send( obj, destination = d, **info):
                if d.dest_hash not in valid_dest_hashes:
                    raise SyncNotConnected(dest = d)
                should_send_destinations.add(d)
        for d in should_send_destinations:
            con = d.protocol
            con._synchronize_object(obj,
            attributes = attributes_to_sync,
                                    operation = operation,
                                    response_for = response_for, priority = priority)
        return future


    async     def _create_connection(self, dest):
        "Create a connection on the loop.  This is effectively a coroutine."
        if not hasattr(self, 'loop'): return
        loop = self.loop
        delta = 1
        task = self._connecting[dest.dest_hash] #our task
        close_transport = None #Close this transport if we fail to connect

        # There are two levels of try; the outer catches exceptions
        #that end all connection attempts and cleans up the cache of
        #destinations we're connecting to.
        try:
            while True: # not connected
                if dest.connect_at > time.time():
                    logger.info("Waiting until {time} to connect to {dest}".format(
                    time = time.ctime(dest.connect_at),
                    dest = dest))
                    delta = dest.connect_at-time.time()
                    await asyncio.sleep(delta)
                delta = min(2*delta, 10*60)
                try:
                    logger.debug("Connecting to {hash} at {host}".format(
                        hash = dest.dest_hash,
                        host = dest.endpoint_desc))
                    transport, bwprotocol = \
                        await dest.create_connection(
                            self.port, loop,
                            self._protocol_factory_client,
                            self._ssl)
                    logger.debug("Transport connection to {dest} made".format(dest = dest))
                    close_transport = transport
                    protocol = bwprotocol.protocol
                    if protocol.dest_hash != dest.dest_hash and protocol.confirm_outgoing_dest_hash:
                        raise WrongSyncDestination(dest = dest, got_hash = protocol.dest_hash)
                    protocol._enable_reading()

                    await dest.connected(self, protocol, bwprotocol = bwprotocol)
                    self._connections[dest.dest_hash] = protocol
                    close_transport = None
                    logger.info("Connected to {hash} at {host}".format(
                        hash = dest.dest_hash,
                        host = dest.endpoint_desc))
                    dest.connect_at = time.time()+delta
                    return transport, protocol
                except (asyncio.CancelledError, GeneratorExit):
                    logger.debug("Connection to {dest} canceled".format(dest = dest))
                    raise
                except (SyntaxError, TypeError, LookupError, ValueError, WrongSyncDestination) as e:
                    logger.exception("Connection to {} failed".format(dest.dest_hash))
                    raise
                except Exception as e:
                    if isinstance(e, no_traceback_connection_failures):
                        logger.error("Error connecting to {}: {}".format(
                            dest, str(e)))
                    else:
                        logger.exception("Error connecting to  {}".format(dest))
                    dest.connect_at = time.time() + delta
        finally:
            if self._connecting.get(dest.dest_hash, None) == task:
                del self._connecting[dest.dest_hash]
            if close_transport: close_transport.close()





    def _connection_lost(self, protocol, exc):
        if self._connections.get(protocol.dest.dest_hash,None)  == protocol:
            del self._connections[protocol.dest.dest_hash]
            msg = "Connection to {} lost:".format(protocol.dest)

            protocol.dest.connection_lost(self)

            if exc is None:
                logger.info(msg)
            else: logger.exception(msg, exc_info = exc)
            if protocol.dest.can_connect: 
                self._connecting[protocol.dest.dest_hash] = self.loop.create_task(self._create_connection(protocol.dest))
        protocol.dest = None

    async def unknown_destination(self, protocol):
        "Called when protocol.dest_hash is not in the local set of destinations.  Can return a new destination which will be added to the set of destinations or None, in which case an error is raised and the protocol disconnected.  This method is an extension point; by default it returns None"
        return None
    


    def add_destination(self, dest):
        if dest.dest_hash is None or dest.name is None:
            raise ValueError("dest_hash and name are required in SyncDestination before adding")
        if dest.dest_hash in self._destinations:
            raise KeyError("{} is already a destination".format(repr(dest)))
        self._destinations[dest.dest_hash] = dest
        assert dest.protocol is None
        assert dest.dest_hash not in self._connecting
        if not dest.can_connect: return
        self._connecting[dest.dest_hash] = self.loop.create_task(self._create_connection(dest))
        return self._connecting[dest.dest_hash]

    def remove_destination(self, dest):
        assert dest.dest_hash in self._destinations
        logger.info("Removing destination {}".format(dest))
        p = self._connections.get(dest.dest_hash, None)
        if p:
            del self._connections[dest.dest_hash]
            p.close()
            dest.protocol = None
        if dest.dest_hash in self._connecting:
            self._connecting[dest.dest_hash].cancel()
            del self._connecting[dest.dest_hash]
        del self._destinations[dest.dest_hash]

    def run_until_complete(self, *args):
        return self.loop.run_until_complete(*args)

    def _sync_receive(self, msg, protocol, response_for):
        info = {'protocol': protocol,
                'manager': self,
                'response_for': response_for}
        if protocol.dest: info['sender'] = protocol.dest
        try:
            cls = None
            self._validate_message(msg)
            cls, registry = self._find_registered_class(msg['_sync_type'])
            info['operation'] = registry.get_operation(msg['_sync_operation'])
            info['registry'] = registry
            info['attributes'] = frozenset(filter(lambda a: not a.startswith('_'), msg.keys()))
            if self.should_listen(msg, cls, **info) is not True:
                # Failure should raise because ignoring an exception takes
                # active work, leading to a small probability of errors.
                # However, active authorization should be an explicit true
                # not falling off the end of a function.
                raise SyntaxError("should_listen must either return True or raise")
            if msg['_sync_authorized'] != self:
                raise SyntaxError("When SyncManager.should_listen is overwridden, you must call super().should_listen")
            del msg['_sync_authorized']
            with registry.sync_context(sync_type = cls, **info) as ctx:
                info['context'] = ctx
                obj = cls.sync_construct(msg, **info)
                if self.should_listen_constructed(obj, msg, **info) is not True:
                    raise SyntaxError("should_listen_constructed must either return True or raise")
                obj.sync_receive_constructed(msg, **info)
                registry.sync_receive(obj, **info)
            if response_for:
                response_for(obj)
        except Exception as e:
            if isinstance(e, interface.SyncError):
                exc_str = f': {str(e)}'
            else: exc_str = None
            logger.error("Error receiving a {}{}".format(
                cls.__name__ if cls is not None else msg['_sync_type'],
                exc_str),
                         exc_info = e  if not exc_str else None)
            if isinstance(e,interface.SyncError) and not '_sync_is_error' in msg:
                if not e.network_msg: e.network_msg = msg
                try:
                    self.synchronize(e,
                                          destinations = [protocol.dest],
                                          response_for = response_for,
                                          operation = 'error')
                except: pass

        finally:
            if 'context' in info: del info['context']

    def _validate_message(self, msg):
        if not isinstance(msg, dict):
            raise interface.SyncBadEncodingError('Message is a {} not a dict'.format(msg.__class__.__name__))
        msg.setdefault('_sync_operation', 'sync')
        for k in msg:
            if k.startswith('_') and k not in protocol.sync_magic_attributes:
                raise interface.SyncBadEncodingError('{} is not a valid attribute in a sync message'.format(k), msg = msg)

    def should_send(self, obj, destination, registry, sync_type, **info):
        info['registry'] = registry
        info['sync_type'] = sync_type
        info['destination'] = destination
        if not destination.should_send( obj, **info):
            return False
        if not registry.should_send( obj, **info):
            return False
        if not obj.sync_should_send(**info):
            return False
        return True
    
    def should_listen(self, msg, cls, **info):
        sender = info['sender']
        registry = info['registry']
        msg['_sync_authorized'] = self #To confirm we've been called.
        if sender.should_listen(msg, cls, **info) is not True:
                        raise SyntaxError('should_listen must return True or raise')
        if registry.should_listen(msg, cls, **info)is not True:
            raise SyntaxError('should_listen must return True or raise')
        if cls.sync_should_listen(msg, **info) is not True:
            raise SyntaxError('sync_should_listen must return True or raise')
        return True

    def should_listen_constructed(self, obj, msg, **info):
        if info['registry'].should_listen_constructed(obj, msg, **info) is not True:
            raise SyntaxError("should_listen_constructed must return true or raise")
        if obj.sync_should_listen_constructed(msg, **info) is not True:
            raise SyntaxError('sync_should_listen_constructed must return True or raise')
        return True

    

    def _find_registered_class(self, name):
        for reg in self.registries:
            if name in reg.registry: return reg.registry[name], reg
        raise UnregisteredSyncClass('{} is not registered for this manager'.format(name))

    def close(self):
        if not hasattr(self,'_transports'): return
        connections = list(self._connections.values())
        self._connections = {}
        for c in connections:
            c.close()
        connecting = list(self._connecting.values())
        self._connecting = {}
        for c in connecting: c.cancel()
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

    def dest_by_hash(self, dest_hash):
        dest_hash = DestHash(dest_hash)
        return self._destinations.get(dest_hash, None)
    
    @property
    def connections(self):
        "Return a list of all active protocol objects"
        return list(self._connections.values())

    @property
    def destinations(self):
        "A set of destinations for this manager"
        return set(self._destinations.values())

class SyncServer(SyncManager):

    "A SyncManager that accepts incoming connections"

    def __init__(self, cert, port, *, cafile = None, capath = None,
                 key = None, **kwargs):
        super().__init__(cert, port, capath = capath,
                         cafile = cafile, key = key,
                         **kwargs)
        self._cert = cert
        self._cafile = cafile
        self._key = key
        self._capath = capath
        self._servers = []

    def listen_ssl(self, port = None, host = None):
        '''
        Start listening for ssl connections

        :param port: None will use the outgoing port specified in the constructuor; otherwise specify  a port to listen on.

        :param host: None will listen on all addresses, else a hostname or address.

        '''
        if port is None: port = self.port
        if self._ssl is None:
            raise ValueError("You must construct the SyncServer with a valid certificate to listen for SSL")
        self.host = host
        self._ssl_server = self._new_ssl(self._cert, key = self._key, cafile = self._cafile,
                                                 capath = self._capath, server=True)
        self._ssl_server.check_hostname = False
        self._servers.append(self.loop.run_until_complete(self.loop.create_server(
                    self._protocol_factory_server(),
                    host = host,
                    port = port,
                    ssl = self._ssl_server,
                    reuse_address = True, reuse_port = True)))

    def listen_unix(self, path):
        '''
Listen on the given unix path.
        '''
        self._servers.append(self.loop.run_until_complete(
        self.loop.create_unix_server(self._protocol_factory_server(protocol.UnixProtocol),
                                      path = path)))


    def _protocol_factory_server(self,
                                 protocol = protocol.SyncProtocol):
        "Factory factory for server connections"
        return lambda: BwLimitProtocol(
            chars_per_sec = 10000000,
            bw_quantum = 0.1,
            loop = self.loop,
            upper_protocol = protocol(manager = self, incoming = True))

    def close(self):
        for s in self._servers:
            s.close()
        self._servers.clear()
        super().close()

    async def _incoming_connection(self, protocol):
        old = None
        task = None
        if protocol.dest_hash not in self._destinations:
            try: dest = await self.unknown_destination(protocol)
            except Exception as e:
                dest = None
                logger.exception("Error handling unknown_destination:", exc_info = e)
                
            if dest and dest not in self.destinations:
                self.add_destination(dest)
        else:
            dest = self._destinations[protocol.dest_hash]
        if dest is None:
            logger.error("Unexpected connection from {}".format(protocol.dest_hash))
            protocol.close()
            return
        protocol.dest = dest
        if self.cert_hash == dest.dest_hash:
            logger.debug("Self connection to {}".format(dest.dest_hash))
            self.incoming_self_protocol = weakref.ref(protocol)
            protocol._enable_reading()
            return
        if dest.dest_hash in self._connections:
            logger.warning("Replacing existing connection to {}".format(dest))
            self._connections[dest.dest_hash].close()
        if dest.dest_hash in self._connecting:
            if hash(dest.dest_hash) >hash(self.cert_hash):
                # Wait a tenth of a second to break mutual open race
                logger.debug("Waiting briefly before replacing connection in progress to {}".format(dest))
                await asyncio.sleep(0.1)
                try:
                    if protocol.transport.is_closing(): return
                except AttributeError: return
                
                    
            logger.info("Replacing existing connection in progress to {}".format(dest))
            old = self._connecting[dest.dest_hash]
        try:
            task = self.loop.create_task(dest.connected(self, protocol,
                                                        bwprotocol = protocol.bwprotocol))
            self._connecting[dest.dest_hash] = task
            if old: old.cancel()
            protocol._enable_reading()
            await self._connecting[dest.dest_hash]
            self._connections[dest.dest_hash] = protocol
            logger.info("New incoming connection from {}".format(dest))
        finally:
            if dest.dest_hash in self._connecting and self._connecting[dest.dest_hash] == task:
                del self._connecting[dest.dest_hash]

class SyncDestinationBase:

    '''A SyncDestination represents a SyncManager other than ourselves that can receive (and generate) synchronizations.  The Synchronizable and subclasses of SyncDestination must cooperate to make sure that receiving and object does not create a loop by trying to Synchronize that object back to the sender.  One solution is for should_send on SyncDestination to return False (or raise) if the outgoing object is received from this destination.'''

    def __init__(self, dest_hash, name, bw_per_sec = 10000000000):
        self.dest_hash = DestHash(dest_hash)
        self.name = name
        self.bw_per_sec = bw_per_sec
        self.protocol = None
        self.connect_at = 0
        self._on_connected_cbs = []
        self._on_connection_lost_cbs = []
        
    def __repr__(self):
        return "<{c} {{name: '{name}', hash: {hash}}}>".format(
            name = self.name,
            hash = self.dest_hash,
            c = self.__class__.__name__)

    def on_connect(self, callback):
        "call callback on connection"
        self._on_connected_cbs.append(callback)

    def on_connection_lost(self, callback):
        self._on_connection_lost_cbs.append(callback)
        
    def should_send(self, obj, manager , **kwargs):
        return True

    def should_listen(self, msg, cls, **kwargs):
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
        method.  If *connected* raises, the connection will be closed and aborted.
        '''
        self.protocol = protocol
        self.bwprotocol = bwprotocol
        bwprotocol.bw_per_quantum = self.bw_per_sec*bwprotocol.bw_quantum
        for cb in self._on_connected_cbs:
            manager.loop.call_soon(cb)
            
        return

    def connection_lost(self, manager):
        for cb in self._on_connection_lost_cbs:
            manager.loop.call_soon(functools.partial(cb,manager))


class SyncDestination(SyncDestinationBase):

    '''A standard  SyncDestination representing another system that can be reached by a TLS encrypted TCP connection.'''
    

    def __init__(self, dest_hash = None, name = None, *,
                 host = None, bw_per_sec = 10000000,
                 server_hostname = None):
        if server_hostname is None: server_hostname = host
        super().__init__(dest_hash, name, bw_per_sec = bw_per_sec)
        self.host = host
        self.server_hostname = server_hostname

    async def create_connection(self, port, loop, proto_factory, ssl):
        port = getattr(self, 'port', port)
        # There is a race if we get canceled while the
        # create_connection call is in SSL handshake
        # phase: we will leave the network connection open
        # but it will not be functional.  It would be
        # possible to work around this with
        # asyncio.shield, although Python > 3.5 may fix
        # this as well.  We avoid the race by sleeping in
        # _incoming_connection before replacing a
        # connection in one direction.  That sleep is
        # needed anyway for tests, and the race seems very
        # unlikely so we accept it until it becomes a
        # problem.
        return await \
            loop.create_connection(proto_factory(self),
                                   port = port, ssl = ssl,
                                   host = self.host,
                                   server_hostname = self.server_hostname)

    @property
    def can_connect(self):
        return bool(self.host)

    @property
    def endpoint_desc(self):
        return self.host
    
                    
class OutgoingUnixDestination(SyncDestinationBase):

    def __init__(self, dest_hash, name, path,
                 *, bw_per_sec = 10000000000):
        super().__init__(dest_hash, name, bw_per_sec = bw_per_sec)
        self.path = path

    async def create_connection(self, port, loop, proto_factory, ssl):
        return await loop.create_unix_connection(proto_factory(self, protocol.UnixProtocol), self.path)

    @property
    def can_connect(self):
        return bool(self.path)

    @property
    def endpoint_desc(self):
        return self.path

