"use strict";
var entanglement = require('../../javascript/entanglement');
var assert = require('assert');
let sr = new entanglement.SyncRegistry();
var websocket_schemas = require("./schemas/websocket_test");
websocket_schemas(sr);
class TableInherits extends sr.bases.TableInherits(entanglement.Synchronizable) {

    syncReceive(msg, options) {
        super.syncReceive(...arguments);
        if (!(this.sync_serial >1))
            throw assert.AssertionError("Unexpected sync_serial");
        assert.equal(this.info2, 20);
        process.exit(0);
    }
};

sr.register(TableInherits);
var sm = new entanglement.SyncManager({
    url: process.argv[2],
    registries: [sr],
});

setTimeout(() => process.exit(2), 10000);
