#!/usr/bin/python3
# Copyright (C) 2017, Hadron Industries, Inc.
# Entanglement is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.



import asyncio

class BwLimitProtocol(asyncio.protocols.Protocol):

    '''A protocol that calls pause_writing when more than the allocated bandwidth is used.'''

    def __init__(self, *, loop, chars_per_sec, bw_quantum, upper_protocol):
        self.loop = loop
        self.protocol = upper_protocol
        self.bw_per_quantum = chars_per_sec*bw_quantum
        self.bw_quantum  = bw_quantum
        self._quantum_start = loop.time()
        self.used = 0
        self._paused = False
        self._transport_paused = False
        

    def data_received(self, data):
        return self.protocol.data_received(data)

    def connection_made(self, transport):
        orig_write = transport.write
        def bwlimit_write(data):
            res = orig_write(data)
            self.bw_used(len(data))
            return res

        transport.write = bwlimit_write
        self.transport = transport
        try: res =  self.protocol.connection_made(self.transport, bwprotocol = self)
        except TypeError: res = self.protocol.connection_made(self.transport)
        return res

    def connection_lost(self, exc):
        if hasattr(self.protocol, 'connection_lost'):
            return self.protocol.connection_lost(exc)

    def eof_received(self):
        return self.protocol.eof_received()
    def pause_writing(self):
        self._transport_paused = True
        if not self._paused:
            self._paused = True
            return self.protocol.pause_writing()

    def resume_writing(self):
        self._transport_paused = False
        return self._maybe_resume()

    def _maybe_pause(self):
        if self._paused: return
        self._paused = True
        return self.protocol.pause_writing()

    def _maybe_resume(self):
        if (self.used < self.bw_per_quantum) and self._paused:
            self._paused = False
            self.protocol.resume_writing()
            
    def _quantum_passed(self, min_quanta = 1):
        '''Consider whether bw quanta have passed.  Called both from write and if we have paused,  also from a timed callback.  In the timed callback case, make sure we allow  at least one quanta to avoid confusion around boundary conditions.'''
        quanta = round((self.loop.time()-self._quantum_start)/self.bw_quantum)
        quanta = min(quanta, min_quanta)
        if quanta >= 1:
            self._quantum_start = self.loop.time()
            self.used -= quanta*self.bw_per_quantum
        self.used = max(self.used, 0)
        self._maybe_resume()

    def bw_used(self, chars):
        self._quantum_passed(0)
        self.used += chars
        if self.used > self.bw_per_quantum:
            self._maybe_pause()
            self.loop.call_later(self.bw_quantum, self._quantum_passed)

