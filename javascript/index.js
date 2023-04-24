"use strict";
/*
 * Copyright (C) 2017, 2020, Hadron Industries, Inc.
 *  Entanglement is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU Lesser General Public License version 3
 *  as published by the Free Software Foundation. It is distributed
 *  WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
 *  LICENSE for details.
*/
var WebSocket;
try {
    WebSocket = window.WebSocket;
} catch(e) {
    WebSocket =require('websocket').w3cwebsocket;
}



function promiseAnyPolyfill(promises) {
    // Sort of like Promise.any (which node.js does not support)
    // Except no Aggrigate error support
    let result_found = false;
    let rejections = 0;
    promises = Array.from(promises); // So we know the length
    function inner(resolve, reject) {
        if (promises.length == 0) {
            reject(null);
        }
        for (let p of promises) {
            p.then((result) => {
                if (!result_found) {
                    result_found = true;
                    resolve(result);
                }
            }). catch(
                (rejection) => {
                    rejections++;
                    if (rejections == promises.length)
                        reject(rejection);
                });
        }
    }
    return new Promise(inner);
}

function EventHandlerMixin(obj, events = null) {
    const methods = {
        addEventListener(event, handler) {
            let handlers = this.event_handlers[event];
            if (handlers === undefined)
                throw new TypeError("Illegal event");
            if (!handlers.includes(handler))
                handlers.push(handler);
            return handler;
        },

        removeEventListener(event, handler) {
            let handlers = this.event_handlers[event];
            if (handlers === undefined)
                throw new TypeError("Illegal event");
            this.event_handlers[event] = handlers.filter((i) => i != handler);
        },

        _dispatchEvent(event, ...rest) {
            let handlers = this.event_handlers[event];
            for (let h of handlers) {
                try {
                    Promise.resolve(h(...rest)) .catch((e) => {
                        console.error(`${e} handling ${event} event`);
                    });
                } catch(e) {
                    console.error( `${e} dispatching to ${event} event`);
                }
            }
        }
    };
    Object.assign(obj, methods);
    if (events) {
        Object.defineProperties(
            obj,
            {
                handled_events: {
                    enumerable: false,
                    value: Object.freeze(Array.from(events))},
                event_handlers: {
                    enumerable: false,
                    get: function() {
                        if (eventHandlerMap.has(this))
                            return eventHandlerMap.get(this);
                        let res = {};
                        for (let e of this.handled_events) {
                            res[e] = [];
                        }
                        eventHandlerMap.set(this, res);
                        return res;
                    },
                },
            });
    }
}

const eventHandlerMap = new WeakMap();

const syncHandledEvents = Object.freeze(
    ['sync', 'forward', 'create',
     'transition', 'brokenTransition',
     'delete',
     // Disappear is dispatched for cases where owner removal or epoch change causes an object to be cleared.
     'disappear']);

        
export class SyncManager {

    constructor(options) {
        if (typeof options == "string") {
            // Old format where it is a url
            let url = options;
            options = {url: url};
        }
                this.receivers = {};
        this._open = false;
        this._backoff = 256;
        this.url = options.url;
        for (let r of options.registries || []) {
            r.associateManager(this);
        }
        this._connection_attempt_error_count = 0;
        this._connect();
    }

    genuuid4(){
        let a;
        try{
            a = new Uint8Array(16);
            window.crypto.getRandomValues(a);
        }catch (err){
            let crypto = require('crypto');
            a = crypto.randomBytes(16);
        }

        // Mark as v4
        a[6] = (a[6] & 0x0f) | 0x40;
        a[8] = (a[8] & 0x3f) | 0x80;

        let str = '';
        for(let i=0; i<16; i++){
            if(i==4||i==6||i==8||i==10){
                str += '-'; }
            str += (a[i] + 0x100).toString(16).substr(1);
        }
        return str;
    }

    _connect() {
        console.log(`Entanglement connecting to ${this.url}`);
        this.socket = new WebSocket(this.url);
        this.socket.addEventListener('open', event => {
            if (this._onopen) this._onopen(this);
            this._open = true;
            this._connection_attempt_error_count = 0;
            setTimeout(() => {
                if (this._open) this._backoff = 256;
            }, this._backoff);
        });
        this.socket.addEventListener('close', this._disconnect.bind(this));
        this.socket.addEventListener('error', this._disconnect.bind(this));
        this.socket.addEventListener('message', event => {
            this._in_counter++;
            var message = JSON.parse(event.data);
            if ( '_no_resp_for' in message) {
                message._no_resp_for.forEach( n => {
                    var ex = this.expected[Number(n)];
                    if (ex) {
                        ex.resolved(null);
                        delete this.expected[Number(n)];
                    }
                });
            }
            let result_promises = [];
            if (message['_sync_type'] in this.receivers) {
                this.receivers[message._sync_type].forEach( r => {
                    result_promises.push(
                        Promise.resolve(r(message, {manager: this})). catch(
                            (e) => console.error(`${e.toString()}: ${e.stack||""} a ${message._sync_type}`)));
                });
            }
            if (!(message._resp_for === undefined)) {
                // If any of the receivers returns a value, pass that
                // into the response handler.  If there are no
                // receivers or they all reject, pass in the message
                // itself.
                promiseAnyPolyfill(result_promises).then (
                    (result) => this._handleRespFor(message, result)). catch(
                        (error) => this._handleRespFor(message, message));
                
            }
        });
        this._out_counter = 0;
        this._in_counter = 0;
        this.expected = {}
    }

    _handleRespFor(message, result) {
        message._resp_for.forEach( r => {
            if( Number(r) in this.expected) {
                try {
                    var prom = this.expected[r];
                    if (message._sync_is_error === undefined) {
                        prom.resolved(result ||message);
                    } else {prom.rejected(message);}
                }
                catch(e) {
                    console.error(`${e.stack} handling response`);
                }
                delete this.expected[r];
            }
        } );
    }
    
    _disconnect(event) {

        if(event.type=='error'){
            if(this._on_connection_setup_failed){
                this._on_connection_setup_failed(this._connection_attempt_error_count,event);
            }
            this._connection_attempt_error_count += 1;
        }

        if (this.socket === undefined) return;
        if (this._open) {
            this._open = false;
            if (this._onclose) this._onclose(this,event);
        }
        try {
            this.socket.close();
        } catch(e) {}
        this.socket = undefined;
        setTimeout(this._connect.bind(this), this._backoff);
        this._backoff = this._backoff*2;
        if (this._backoff > 32768)
            this._backoff = 32768;
    }

    onclose(fn) {this._onclose = fn;}
    onopen(fn) {this._onopen = fn;}
    on_connection_setup_failed(fn) {this._on_connection_setup_failed = fn;}

    synchronize(obj, options, ...rest) {
        var res;
        if (Array.isArray(options)) {
            // old attributes, operation, response calling convention.
            let [operation, response] = rest;
            options = {attributes: options,
                       operation: operation,
                       response: response};
        }
        
        var obj2 = {};
        options.manager = this;
            if (obj.toSync) {
                obj2 = obj.toSync(options);
            }else {
                // no tosync
                for (let a of options.attributes) {
                    obj2[a] = obj[a];
                }
            }
        obj2['_sync_operation'] = options.operation || 'sync';
        obj2._sync_owner = obj._sync_owner;
        if (obj.transition_id) {
            obj2.transition_id = obj.transition_id;
        }
        if (options.response === true) {
            obj2['_flags'] = 1;
            res = new Promise((resolved, rejected) => {
            this.expected[this._out_counter] = {
                resolved: resolved,
                rejected: rejected};
            });
        }
        this.socket.send(JSON.stringify(obj2));
        this._out_counter++;
        return res;
    }

    on_receive(type, handler) {
        var handlers = this.receivers[type];
        if (handlers === undefined) {
            this.receivers[type] = handlers = [];
        }
        if (handlers.indexOf(handler) != -1) { return;}
        handlers.push(handler);
    }

    remove_on_receive(type, handler) {
        var handlers = this.receivers[type];
        this.receivers[type] = handlers.filter(h => h != handler);
    }

    perform_transition(obj, attributes) {
        var first_transition = false;
        var result;
        if(obj.transition_id == null ) { // undefined is OK too
            obj.transition_id = this.genuuid4();
            first_transition = true;
        } else {
            result = obj.transition_promise;
        }
        if (attributes === undefined) {
            attributes = obj.syncModified();
            for (let a of obj.constructor.syncPrimaryKeys)
                attributes.add(a);
        }
        var sync_result = this.synchronize(obj, {
            attributes: attributes,
            operation: 'transition',
            response: first_transition});
        if (first_transition) {
            Object.defineProperty(obj, 'transition_promise', {
                configurable: true,
                enumerable: false,
                value: sync_result});
            result = sync_result;
            if (obj._orig) {
                Object.defineProperty(obj, '_orig_pre_transition',
                                      {enumerable: false,
                                       configurable: true,
                                       value: {},
                                      });
            }
            sync_result.catch(() => true).finally(() => {
                if (obj.transition_promise == sync_result) {
                    delete obj.transition_promise;
                    delete obj.transition_id;
                    if (obj._orig_pre_transition)
                        delete obj._orig_pre_transition;
                }
            });
        }
        if (obj._orig_pre_transition) {
            for (let a of attributes)
                obj._orig_pre_transition[a] = obj._orig[a];
        }
        return result;
    }

    close() { this.socket.close();}
    
    close() {
        if (this._open) {
            this.socket.close();
            delete this.socket;
        }
        delete this.url; // Will break any attempt to reconnect
    }

}

export class SyncRegistry {

    constructor() {
        this.registry = new Map();
        this.bases = {};
        this.event_handlers = {};
        for (let e of syncHandledEvents)
            this.event_handlers[e] = [];
        this.event_handlers.receive = [];
        this.addEventListener('delete', this._incomingDelete.bind(this));
    }



    async _incomingDelete(object, msg) {
        if (object.persistDelete)
            return await object.persistDelete(this);
    }
    
    _schemaItem(name, keys, attrs) {
        if (this.bases[name]) {
            console.warn(`${name} already registered`);
            return;
        }
        keys = Object.freeze(keys);
        attrs = Object.freeze(attrs);
        this.bases[name] = function(base, extend = true) {
            // By default we extend the base class with a new class
            // But internally if register is called with something
            // that is a Synchronizable, has a name in our schema, but
            // does not have the syncattributes etc, we add those
            // properties directly to it.  See what happens in
            // entanglement.persistence.setupPersistence as an example
            
            let result;
            if (extend)
                result = class extends base { }
            else result = base;
            Object.defineProperties(
                result,                                    {
                    name: {configureable: false,
                           writable: false,
                           enumerable: false,
                           value: name},
                    _syncAttributes: {configurable: false,
                                       writable: false,
                                       enumerable: true,
                                       value: attrs},
                    syncPrimaryKeys: {
                        enumerable: true,
                        writable: false,
                        configurable: false,
                        value: keys},
                    syncType: {
                        configurable: false,
                        writable: false,
                        enumerable: true,
                        value: name},
                })
            if (! (base instanceof Synchronizable)) 
                Synchronizable._mixinSynchronizable(result);
            return result;
        }
    }

    associateManager(manager) {
        this._finalize();
        for (let [k,v] of this.registry) {
            manager.on_receive(k, this.syncReceive.bind(this));
        }
    }
        

    register(cls) {
        if ((!('syncType' in cls)) && (cls.name in this.bases) && (cls.prototype instanceof Synchronizable)) {
            // We assume that the intent is for us to add the schema items to the class in this instance.
            this.bases[cls.name](cls, false);
        }
            
        for (let k of ['syncType', '_syncAttributes', 'syncPrimaryKeys']) {
            if (cls[k] === undefined)
                throw new TypeError(`${cls.name} is not Synchronizable`);
        }
        let sync_type = cls.syncType;
        if(this.registry.has(sync_type))
            throw new TypeError( `${cls} is already registered.`);
        this.registry.set(sync_type, cls);
    }

    _finalize() {
        for (let k in this.bases) {
            if (! this.registry.has(k))
                this.registry.set(k, this.bases[k](Synchronizable))
        }
    }

    async syncReceive(msg, options) {
        if (options === undefined)
            options = {};
        let sync_type = msg._sync_type;
        let cls = this.registry.get(sync_type);
        if (cls === undefined)
            throw new TypeError( `${sync_type} is not registered`)
        options.operation = msg._sync_operation || 'sync';
        options.registry = this;
        let obj = await Promise.resolve(cls.syncConstruct(msg, options));
        await Promise.resolve(obj.syncReceive(msg, options));
        this._dispatchEvent('receive', obj, msg);
        if (options.operation in this.event_handlers) {
            this._dispatchEvent(options.operation, obj, msg, options);
            obj.constructor._dispatchEvent(options.operation, obj, msg, options);
        }
        return obj;
    }
    
}

EventHandlerMixin(SyncRegistry.prototype);

            
    
export class Synchronizable {

    toSync(options) {
        options = options || {};
        let attributes = options.attributes || this.constructor._syncAttributes;
        let res = {};
        if (this._sync_owner !== undefined)
            res['_sync_owner'] = this._sync_owner;
        res['_sync_type'] = this.constructor.syncType;
        for (let attr of attributes) {
            res[attr] = this[attr];
        }
        return res;
    }

    async syncClone() {
        //Returns  results of SyncReceive ontoSync
        // In the persistence case, this is going to be a new object not in the storage map.
        // Needs to be async so that SyncReceive can be async
        let res = Object.create(this.constructor.prototype, {});
        await res.syncReceive(this.toSync({}), {operation: 'clone'});
        return res;
    }

    static syncConstruct(msg, options) {
        let res = this.syncEmptyObject();
        // Override to look up in a database or stable map or similar.
        return res;
    }

    static syncEmptyObject() {
        // Produce an empty object.  We try using the constructor
        // because it may set locally managed attributes, but that may
        // not work because the constructor may require arguments.
        // This approach is potentially fragile and if this is
        // inappropriate for a given class, override this method.
        let res;
        try {
            res = new this;
        } catch (e) {
            res = Object.create(this.prototype);
        }
        return res;
    }
    

    syncReceive(msg, options) {
        if (this.transition_id && (this.transition_id != msg.transition_id)) {
            if (this._orig_pre_transition) {
                for (let a in this._orig_pre_transition) {
                    if (a in msg) continue;
                    this[a] = this._orig_pre_transition[a];
                }
            }
            this.constructor._dispatchEvent("brokenTransition", this, msg, this.transition_id);
        }
        let orig = Object.assign({},
                                 this._orig || {});
        for (let k in msg) {
            if (k[0] == "_" && (k != "_sync_owner"))
                continue;
            orig[k] = msg[k];
            // Don't use Object.assign to deal better with Vue or other reactive frameworks
            this[k] = msg[k];
        }
        Object.defineProperty(this, '_orig',
                              {value: Object.freeze(orig),
                               writable: false,
                               configurable: true,
                               enumerable: false});
        return this;
    }

    static _mixinSynchronizable(target) {
        function mix(t, o) {
            
            let exclusions = new Set(['name', 'prototype', 'constructor'])
            let obj = t
            while (obj  !== Object.prototype) {
                for (let k of Reflect.ownKeys(obj)) {
                    exclusions.add(k)
                }
                obj = Reflect.getPrototypeOf(obj)
            }
        
            for (let k of Reflect.ownKeys(o)) {
                if (!exclusions.has(k)) {
                    Object.defineProperty(t,
                                          k, Reflect.getOwnPropertyDescriptor(o, k))
                }
            }
        }
        mix(target,Synchronizable)
        mix(target.prototype, Synchronizable.prototype)
        return target
    }
    
}

EventHandlerMixin(Synchronizable, syncHandledEvents);


        
        
try {
    module.exports = {        
        SyncManager,
        SyncRegistry,
        Synchronizable,
        default: SyncManager
    }
} catch (err) { }

