"use strict";
/*
*  Copyright (C) 2017 by Hadron Industries
* All rights Reserved; distributed under license
*/

if (!('WebSocket' in this)) {
    var WebSocket =require('websocket').w3cwebsocket;
}


class SyncManager {

    constructor(url) {
        this.receivers = {}
        this._open = false;
        this._backoff = 256;
        this.url = url;
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
            if (!(message._resp_for === undefined)) {
                message._resp_for.forEach( r => {
                    if( Number(r) in this.expected) {
                        var prom = this.expected[r];
                        if (message._sync_is_error === undefined) {
                            prom.resolved(message);
                        } else {prom.rejected(message);}
                        delete this.expected[r];
                    }
                } );
            }
            if (message['_sync_type'] in this.receivers) {
                this.receivers[message._sync_type].forEach( r => r(message));
            }
        });
        this._out_counter = 0;
        this._in_counter = 0;
        this.expected = {}
    }

    _disconnect(event) {
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

    synchronize(obj, attributes, operation, response) {
        var res;
        var obj2 = {};
        attributes.forEach( attr => obj2[attr] = obj[attr]);
        obj2['_sync_operation'] = operation;
        obj2._sync_owner = obj._sync_owner;
        if (obj.transition_id) {
            obj2.transition_id = obj.transition_id
        }
        if (response === true) {
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
        var sync_result = this.synchronize(obj, attributes, 'transition', first_transition);
        if (first_transition) {
            Object.defineProperty(obj, 'transition_promise', {
                config: true,
                enumerable: false,
                value: sync_result});
            result = sync_result;
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

class SyncRegistry {

    constructor() {
        this.registry = new Map();
        this.bases = {};
        this.event_handlers = {
            received: [],
            sync: [],
            transition: [],
            delete: [],
            forward: []
        };
    }

    _schemaItem(name, keys, attrs) {
        this.bases[name] = function(base) {
            let result = class extends base {
            }
            Object.defineProperties(
                result,                                    {
                    name: {configureable: false,
                           writable: false,
                           enumerable: false,
                           value: name},
                    _sync_attributes: {configurable: false,
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
            return result
        }
    }

    register(cls) {
        for (let k of ['syncType', '_syncAttributes', 'syncPrimaryKeys']) {
            if (cls[k] === undefined)
                throw new TypeError(`${cls} is not Synchronizable`);
        }
        let sync_type = cls.syncType;
        if(this.registry.has(sync_type))
            throw new TypeError( `${cls} is already registered.`);
        this.registry.add(sync_type, cls);
    }

    _finalize() {
        for (let k in this.bases) {
            if (! this.registry.has(k))
                this.registry.add(k, this.bases[k](Synchronizable))
        }
    }

    async syncReceive(msg, options) {
        let sync_type = msg._sync_type
        let cls = this.registry.get(sync_type)
        if (cls === undefined)
            throw new TypeError( `${sync_type} is not registered`)
        options.operation = msg._sync_operation || 'sync';
        options.registry = this;
        let obj = await Promise.resolve(cls.syncConstruct(msg, options));
        return await Promise.resolve(obj.syncReceive(msg, options));
    }
    
            
    
}

class Synchronizable {

    toSync(options) {
        options = options || {}
        let attributes = options.attributes || this.constructor._sync_attributes
        let res = {}
        if (this.sync_owner !== undefined)
            res['_sync_owner'] = this.sync_owner
        res['_sync_type'] = this.sync_type
        for (let attr of attributes) {
            res[attr] = this[attr]
        }
        return res
    }

    static syncConstruct(msg, options) {
        let res = Object.create(this.prototype)
        // This method can be overridden
        // It is reasonable for overrides to remove properties from msg that are set as primary keys etc.
        //override for database lookups etc
        return res
    }

    syncReceive(msg, options) {
        // Don't use Object.assign to deal better with Vue or other reactive frameworks
        let orig = {}
        for (let k in msg) {
            orig[k] = this[k]
            this[k] = msg[k]
        }
        this._orig = Object.freeze(orig)
        return this
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


        
        
try {
    module.exports = {        
        SyncManager,
        SyncRegistry,
        Synchronizable,
        default: SyncManager
    }
} catch (err) { }

