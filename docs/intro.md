```eval_rst
.. _Introduction:
```
# Introduction

**Entanglement** is a state synchronization system.  It is superficially similar to database replication technologies.  Database replication focuses on quickly transmitting committed changes to a database in a consistent manner.  Entanglement focuses  on eventually synchronizing state between nodes in a network hile minimizing bandwidth.

Consider an application where a group of people are trying to meet up in real time.  Each person in the group securely receives the realtime location of the other group members.  However if network conditions do not permit some update to be sent and that update becomes outdated, it is better to skip the outdated update and send the current state than send the entire history of updates.  Entanglement is designed for this sort of application.

Entanglement has the following properties:



* Coalesce updates to the same object so only the most recent is sent.

* Provide policy and security controls so that objects are only sent where needed and permitted.

* Integrate with mandatory access control frameworks.

* Work for client-server and web applications.

## SQL Integration

Entanglement can be integrated with a relational database using [SqlAlchemy](http://www.sqlalchemy.org/).  In this model, `Columns` are synchronized whenever an object is committed to a `Session`.  This model has several differences from traditional database replication:

* Each object has an owner responsible for deciding the authoritative version of that object.  However, a single table can combine objects from multiple owners.

* Objects may not be sent or received at all nodes, so the set of objects available at a given node may be different from other nodes.

* Entanglement works in applications where referential integrity is not maintained.  This may happen because not all objects are sent to all nodes or because the application permits messages to be sent in an order that does not maintain refferential integrity.  For many highly distributed applications where Entanglement is a good fit, refferential integrity is harmful.

* If an application wishes to maintain refferential integrity, it can constrain how it uses Entanglement and integrity will be maintained.

* Any node may propose a change to an object; this forwarded update request is sent towards the object owner.

* A mechanism called `Transitions` permits high-speed realtime tracking of proposed changes to an object that need not make their way to the owner before being considered by other nodes.  This can be used for tracking moving a graphical object or similar events where network latency would get in the way of responsive interface.

## Ephemeral Objects

Entanglement can also be used without relational databases.  As shipped, entanglement also supports ephemeral objects that live in memory.  These can be used to implement remote procedure calls, errors, or other state that does not have a long-term persistence.

## Other Object Stores

Entanglement can easily be extended to support other persistent objects besides SQL.  This involves the following tasks:

* Adapting the schema definition of the objects to produce `Synchronizable` classes and `sync_properties`

* Detecting changes to the objects and generating `synchronize events`

* Providing a way to retrieve the object in `sync_construct`

* Deciding when objects can be coalesced

* If objects will be updated other than by the owner, providing a way to make these updates and avoiding storing them prematurely
