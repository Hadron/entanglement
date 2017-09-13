"use strict";
/*
*  Copyright (C) 2017 by Hadron Industries
* All rights Reserved; distributed under license
*/

if (!('WebSocket' in this)) {
    var WebSocket =require('websocket').w3cwebsocket;
}
try {
    var uuid = require('node-uuid');
} catch(err) {}

class  SyncManager {
    constructor(url) {

	this.receivers = {}
	this._open = false;
	this._backoff = 256;
	this.url = url;
	this._connect();
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

    _disconnect() {
	if (this.socket === undefined) return;
	if (this._open) {
	    this._open = false;
	    if (this._onclose) this._onclose(this);
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
	    obj.transition_id = uuid.v4();
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

try {
    module.exports = {        SyncManager: SyncManager,
		     }
} catch (err) { }

