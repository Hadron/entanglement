"use strict";
var entanglement = require('../../javascript/entanglement.js');
var assert = require('assert');
let sr = new entanglement.SyncRegistry();
sr._schemaItem('Fruit', ['id'], ['id', 'flavor']);
sr._schemaItem('Circle',['id'], ['id', 'fill']);

class Fruit extends sr.bases.Fruit(entanglement.Synchronizable) {

    eat() {
        console.log(`Mmm, it's ${this.flavor}`);
    }
};

sr.register(Fruit);

class Shape {}

class Circle extends sr.bases.Circle(Shape) {
}
sr.register(Circle);

let apple1 = new Fruit();
apple1.flavor = "apple";
apple1.id = 20;
let apple1_msg = apple1.toSync();
apple1_msg._sync_type = 'Fruit';
let apple2;
sr.syncReceive(apple1_msg, {}).then ((v) => {
    apple2 = v;
    assert.deepEqual(apple1, apple2);
}) .catch((e) => {
    console.error(e);
    process.exit(2);
});

