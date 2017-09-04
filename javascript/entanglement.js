"use strict";

if (!('WebSocket' in global)) {
    var WebSocket =require('websocket').w3cwebsocket;
}

class  SyncManager {
    constructor(url) {
	this.socket = new WebSocket(url);
	this.socket.addEventListener('message', event => {
	    this._in_counter++;
	    var message = JSON.parse(event.data);
	    console.log(message);
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
	});
	this._out_counter = 0;
	    this._in_counter = 0;
	    this.expected = {}
	this.addEventListener = this.socket.addEventListener.bind(this.socket);
    }

    synchronize(obj, attributes, operation, response) {
	var res;
	var obj2 = {};
	attributes.forEach( attr => obj2[attr] = obj[attr]);
	obj2['_sync_operation'] = operation;
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
	return res
    }
}

module.exports = {
    SyncManager: SyncManager,
    }
