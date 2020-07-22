"use strict";
var entanglement = require('../../javascript/entanglement.js');
var assert = require('assert');
let sr = new entanglement.SyncRegistry();

sr._schemaItem('TableInherits',
               ['id'],
               ['id', 'info', 'info2']);
class TableInherits extends sr.bases.TableInherits(entanglement.Synchronizable) {

    syncReceive(msg, options) {
        try {
            super.syncReceive(msg, options);
                        incoming[options.operation](this);
            if (this.info == 0)
                process.exit(0);
        } catch(e) {
            console.error(e);
            process.exit(2);
        }
    }

    static syncConstruct(msg) {
        let result = obj_map.get(msg.id);
        if (result === undefined) {
            result = Object.create(this.prototype);
            obj_map.set(msg.id, result);
        }
        return result;
    }
    
}
sr.register(TableInherits);
var sm = new entanglement.SyncManager({
    url: process.argv[2],
    registries: [sr],
});

var incoming = {
    sync: (obj) => {
        for (let a of obj.constructor._syncAttributes) {
            assert.deepEqual(obj[a], obj._orig[a]);
        }
    },
    forward: (obj) => {
        //The forward is not expected to include info2
        assert.equal(obj._orig.info2, 19);
    }
}

var obj_map = new Map();

setTimeout(() => process.exit(2), 10000);
