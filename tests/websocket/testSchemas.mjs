"use strict";
import * as entanglement from '../../javascript/index.js';
import { strict as assert } from 'node:assert';
let sr = new entanglement.SyncRegistry();
import websocket_schemas from "./schemas/websocket_test.mjs";
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
