"use strict";
var entanglement = require('../../javascript/entanglement');
var assert = require('assert');
let sr = new entanglement.SyncRegistry();

sr._schemaItem('TableInherits',
               ['id'],
               ['id', 'info', 'info2']);
class TableInherits extends sr.bases.TableInherits(entanglement.Synchronizable) {

}
sr.register(TableInherits);
var sm = new entanglement.SyncManager({
    url: process.argv[2],
    registries: [sr],
});

var received = false;
function onReceive(obj, msg) {
    received = true;
}
function onSync(obj, msg) {
    if (received)
        process.exit(0);
    console.error("received is false in onSync");
    process.exit(2);
}

sr.addEventListener('receive', onReceive);
sr.addEventListener('sync', onSync);
setTimeout(() => process.exit(2), 10000);
