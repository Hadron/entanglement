"use strict";

if (!('WebSocket' in this)) {
    var WebSocket =require('websocket').w3cwebsocket;
}
try {
    var uuid = require('node-uuid');
} catch(err) {}

class  SyncManager {
    constructor(url) {
	this.socket = new WebSocket(url);
	this.socket.addEventListener('message', event => {
	    this._in_counter++;
	    var message = JSON.parse(event.data);
	    if ( '_no_resp_for' in message) {
		message._no_resp_for.forEach( n => {
		    var ex = this.expected[number(n)];
		    if (ex) {
			ex.forEach( p => p.resolved(null));
			delete this.expected[number(n)];
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
	this.receivers = {}
	this.addEventListener = this.socket.addEventListener.bind(this.socket);
    }

    synchronize(obj, attributes, operation, response) {
	var res;
	var obj2 = {};
	attributes.forEach( attr => obj2[attr] = obj[attr]);
	obj2['_sync_operation'] = operation;
	obj2._sync_owner = obj._sync_owner;
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
	this.receivers[type] = handlers.filter(h => h === handler);
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
	var full_attrs = Array.from(attributes);
	full_attrs.push('transition_id');
	var sync_result = this.synchronize(obj, full_attrs, 'transition', first_transition);
    if (first_transition) {
	obj.transition_promise = sync_result;
	result = sync_result;
    }
    return result;
}


    close() { this.socket.close();}
    
}

try {
    module.exports = {        SyncManager: SyncManager,
		     }
} catch (err) { }

