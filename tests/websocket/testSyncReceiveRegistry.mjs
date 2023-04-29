"use strict";
import * as entanglement from '../../javascript/index.js';
import { strict as assert } from 'node:assert';
let sr = new entanglement.SyncRegistry();

sr._schemaItem('TableInherits',
               ['id'],
               ['id', 'info', 'info2']);
class TableInherits extends sr.bases.TableInherits(entanglement.Synchronizable) {

    syncReceive(msg, options) {
        super.syncReceive(msg, options);
        console.log(this);
        process.exit(0);
    }
}
sr.register(TableInherits);
var sm = new entanglement.SyncManager({
    url: process.argv[2],
    registries: [sr],
});
console.error("Starting sync manager");

setTimeout(() => process.exit(2), 10000);
